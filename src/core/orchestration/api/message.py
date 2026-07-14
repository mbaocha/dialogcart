"""
Message API — HTTP boundary for the orchestration layer.

Receives HTTP requests and delegates turn orchestration to ConversationEngine.

Production flow:
    POST /message
        ↓  session loaded (_raw_session), unfiltered session passed to engine
    ConversationEngine.process_turn()
        ↓  plan → browse short-circuit → eligibility → execution → rendering
        → result dict
    post_message()
        ↓  capability boundary → handler boundary → persistence → response

Session contract:
    - HTTP loads the raw session from the store.
    - ConversationEngine receives the raw (unfiltered) session as session_state.
    - TurnPlanner owns interpreting session contents (merge eligibility, confirmation gate).
    - HTTP applies a status filter only for the capability boundary call
      (apply_capability_to_result); that filtered value is never forwarded to the engine.
"""

import json
import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

# Ensure environment variables are loaded at startup
# Import app module which loads .env files
import core.app  # noqa: F401
from core.orchestration.api.capability_boundary import apply_capability_to_result
from core.session.session_projector import SessionProjector as _SessionProjector
from core.orchestration.errors import ContractViolation, UpstreamError
from core.orchestration.execution.clients.availability_client import AvailabilityClient
from core.orchestration.execution.clients.booking_client import BookingClient
from core.engine.conversation_engine import ConversationEngine
from core.orchestration.session import clear_session, get_session, save_session
from core.rendering.llm_renderer import LlmRenderRequest, render_llm

# Module-level execution clients — core owns these; callers pass text only.
_availability_client = AvailabilityClient()
_booking_client = BookingClient()

_session_projector = _SessionProjector()

# Production orchestration engine — single instance, reused across requests.
_engine = ConversationEngine()


class _SessionStore:
    """Thin adapter so orchestrator can persist execution artifacts between turns."""

    @staticmethod
    def get_session(user_id: str):
        return get_session(user_id)

    @staticmethod
    def save_session(user_id: str, session_state: dict) -> None:
        save_session(user_id, session_state)


_session_store = _SessionStore()

# Extension runners (optional) — single integration point between core and extensions.
# Core never imports adapters/handlers or branches on their names.
try:
    from extensions.bootstrap import register_default_extensions
    from extensions.capabilities.runner import CapabilityRunner
    from extensions.handlers.runner import HandlerRunner

    _capability_runner = CapabilityRunner()
    _handler_runner: Optional[HandlerRunner] = HandlerRunner()
    _extensions_available = True
except ImportError:
    register_default_extensions = None  # type: ignore[misc, assignment]
    _capability_runner = None
    _handler_runner = None
    _extensions_available = False

_capability_runner_available = _extensions_available
_handler_runner_available = _extensions_available

# Bootstrap flag to ensure adapters are registered exactly once
_BOOTSTRAPPED = False

router = APIRouter()
logger = logging.getLogger(__name__)


class MessageRequest(BaseModel):
    """Request model for /message endpoint."""

    user_id: str
    text: str
    domain: Optional[str] = "service"
    timezone: Optional[str] = "UTC"
    organization_id: Optional[int] = None
    transaction_id: Optional[str] = None  # Optional per-request tracing ID


class MessageResponse(BaseModel):
    """Response model for /message endpoint."""

    success: bool
    outcome: Optional[dict] = None
    text: Optional[str] = None
    error: Optional[str] = None
    message: Optional[str] = None
    invariant_trace: Optional[dict] = None
    decision_trace: Optional[dict] = None
    decision_trace_text: Optional[str] = None
    trace_view: Optional[str] = None


@router.post("/message", response_model=MessageResponse)
async def post_message(request: MessageRequest, http_request: Request):
    """
    Process a user message through the orchestration pipeline.

    Session handling:
    - Loads raw session at request start; passes it unfiltered to ConversationEngine.
    - Session merge and interpretation are owned by TurnPlanner inside the engine.
    - Saves session for outcome statuses: NEEDS_CLARIFICATION, AWAITING_CONFIRMATION,
      AWAITING_CAPABILITY, READY, EXECUTED.
    - Capability boundary receives a status-filtered view of the session
      (only NEEDS_CLARIFICATION and AWAITING_CAPABILITY pass through).

    Capability handling:
    - If core emits AWAITING_CAPABILITY, routes to capability runner.
    - Runner manages adapter lifecycle and returns facts when complete.
    - Facts are merged into outcome.facts and saved to session.
    - On next turn, core reads facts from session and proceeds.

    Args:
        request: Message request with user_id, text, domain, timezone

    Returns:
        Message response with success status and outcome or error
    """
    # Bootstrap extensions (once per process, org-scoped gate adapters)
    global _BOOTSTRAPPED
    if not _BOOTSTRAPPED and _extensions_available and register_default_extensions:
        try:
            register_default_extensions(organization_id=request.organization_id)
            _BOOTSTRAPPED = True
        except Exception as e:
            logger.warning(
                f"Failed to bootstrap extensions: {e}. Continuing without extensions."
            )

    try:
        from core.tracing.decision_trace import (
            TRACE_ENV_VAR as DECISION_TRACE_ENV_VAR,
            TRACE_HEADER as DECISION_TRACE_HEADER,
            TRACE_QUERY_PARAM as DECISION_TRACE_QUERY_PARAM,
            TurnTrace,
            finalize_turn_trace as finalize_decision_trace,
            is_decision_trace_enabled,
            clear_request_decision_trace_context,
            set_request_decision_trace_enabled,
            trace_to_dict,
            measure_stage,
        )
        from core.tracing.views import (
            build_trace_response_fields,
            resolve_trace_view,
        )
        from core.tracing.server_log import log_decision_trace_text
        from core.tracing.spine import (
            emit_persist_save_for_outcome,
            emit_reload_verify,
            emit_turn_outcome,
        )
        from core.tracing.invariant_trace import (
            TurnInvariantTrace,
            finalize_turn_trace,
            is_trace_enabled,
        )
        from core.tracing.stage_runner import StageRunner

        query_trace = http_request.query_params.get(DECISION_TRACE_QUERY_PARAM)
        trace_enabled, trace_view = resolve_trace_view(
            query_trace=query_trace,
            header_value=http_request.headers.get(DECISION_TRACE_HEADER),
            env_enabled=os.getenv(DECISION_TRACE_ENV_VAR, "").strip().lower()
            in {"1", "true", "yes", "on"},
        )
        decision_trace_enabled = trace_enabled
        set_request_decision_trace_enabled(decision_trace_enabled, view=trace_view)

        if is_trace_enabled():
            TurnInvariantTrace.defer_finalize(True)

        # Generate transaction_id if not provided (per-request tracing only)
        transaction_id = request.transaction_id or str(uuid.uuid4())

        if decision_trace_enabled:
            TurnTrace.begin(
                user_id=request.user_id,
                text=request.text,
                transaction_id=transaction_id,
            )

        # Load session at request start
        session_state = get_session(request.user_id)
        # Keep the unfiltered session so HANDLER_DELEGATED can preserve booking state
        _raw_session = session_state

        if not decision_trace_enabled:
            logger.info(
                "[session] load",
                extra={
                    "user_id": request.user_id,
                    "transaction_id": transaction_id,
                    "found": session_state is not None,
                    "status": session_state.get("status") if session_state else None,
                    "intent": session_state.get("intent") if session_state else None,
                },
            )

        # Only consider session if status == "NEEDS_CLARIFICATION" or "AWAITING_CAPABILITY"
        # AWAITING_CAPABILITY sessions need to be loaded to preserve active_capability
        if session_state and session_state.get("status") not in (
            "NEEDS_CLARIFICATION",
            "AWAITING_CAPABILITY",
        ):
            session_state = None

        # Note: missing_slots are NOT persisted in session anymore
        # They are computed fresh from intent contract + collected slots
        # No snapshot needed for missing_slots

        # Production path: ConversationEngine is the direct orchestration entrypoint.
        # session_state=_raw_session passes the unfiltered session — this matches the
        # value handle_message() previously loaded from _session_store and forwarded.
        result = _engine.process_turn(
            user_id=request.user_id,
            text=request.text,
            session_state=_raw_session,
            availability_client=_availability_client,
            booking_client=_booking_client,
            session_store=_session_store,
            organization_id=request.organization_id,
            timezone=request.timezone,
            transaction_id=transaction_id,
        )

        # Capability boundary (single owner: API layer, not turn_planner)
        outcome = result.get("outcome")
        if _capability_runner_available and _capability_runner and outcome:
            early = apply_capability_to_result(
                result,
                _capability_runner,
                user_id=request.user_id,
                user_text=request.text,
                session_state=session_state,
                domain=request.domain,
                timezone=request.timezone,
                organization_id=request.organization_id,
                transaction_id=transaction_id,
            )
            if early is not None:
                invariant_trace = (
                    finalize_turn_trace() if is_trace_enabled() else None
                )
                if invariant_trace:
                    early["invariant_trace"] = invariant_trace
                decision_trace_fields = {
                    "trace_view": trace_view,
                    "decision_trace": None,
                    "decision_trace_text": None,
                }
                if decision_trace_enabled:
                    early_outcome = early.get("outcome")
                    emit_persist_save_for_outcome(
                        outcome_status=(
                            early_outcome.get("status")
                            if isinstance(early_outcome, dict)
                            else None
                        ),
                        saved=False,
                    )
                    emit_turn_outcome(
                        outcome=early_outcome,
                        success=early.get("success", False),
                    )
                    frozen_trace = finalize_decision_trace()
                    if frozen_trace is not None:
                        decision_trace_fields = build_trace_response_fields(
                            trace_to_dict(frozen_trace),
                            trace_view,
                        )
                log_decision_trace_text(logger, decision_trace_fields)
                return MessageResponse(**early, **decision_trace_fields)
            outcome = result.get("outcome")

        # Handle HANDLER_DELEGATED — invoke registered intent handler (e.g. RAG)
        if (
            outcome
            and isinstance(outcome, dict)
            and outcome.get("status") == "HANDLER_DELEGATED"
            and _handler_runner_available
            and _handler_runner
        ):
            handler_name = outcome.get("active_handler", "")
            context = {
                "user_id": request.user_id,
                "organization_id": request.organization_id,
                "user_text": request.text,
                "intent_name": outcome.get("intent_name"),
                "search_query": outcome.get("search_query"),
                "slots": outcome.get("slots", {}),
                "session_slots": (_raw_session.get("slots", {}) if _raw_session else {}),
                "session": _raw_session or {},
            }
            handler_result = _handler_runner.handle(handler_name, context)

            # Render via LLM — extension owns the instruction, core executes it.
            if handler_result.render_instruction:
                conversation_history = (_raw_session or {}).get("messages", [])
                rendered_text = render_llm(
                    LlmRenderRequest(
                        render_instruction=handler_result.render_instruction,
                        facts=handler_result.facts,
                        conversation_history=conversation_history,
                    )
                )
            else:
                rendered_text = "I'm unable to respond right now."

            outcome["text"] = rendered_text
            result["outcome"] = outcome

            # Update conversation memory and persist, preserving any active booking session.
            from core.orchestration.nlu.conversation_memory import (
                append_messages_turn,
                update_conversation,
            )

            _conv_base = _raw_session or {}
            _updated_session = update_conversation(
                _conv_base,
                user_text=request.text,
                intent=outcome.get("intent_name", "UNKNOWN"),
                search_query=outcome.get("search_query"),
                assistant_text=rendered_text,
            )
            _updated_session = append_messages_turn(
                _updated_session, request.text, rendered_text
            )
            save_session(request.user_id, _updated_session)
            logger.info(
                "[session] handler_delegated_save",
                extra={
                    "user_id": request.user_id,
                    "handler": handler_name,
                    "intent": outcome.get("intent_name"),
                },
            )

        # Handle session persistence after response
        session_saved = False
        saved_session_state = None
        if outcome and isinstance(outcome, dict):
            with measure_stage("persistence"):
                outcome_status = outcome.get("status")
                merged_luma_response = result.get("_merged_luma_response")
                previous_session = _raw_session or session_state
                persistence_outcome = dict(outcome)
                if not persistence_outcome.get("intent_name") and not persistence_outcome.get(
                    "intent"
                ):
                    plan_for_intent = result.get("plan", {})
                    if isinstance(plan_for_intent, dict):
                        plan_intent = plan_for_intent.get("intent_name") or plan_for_intent.get(
                            "intent"
                        )
                        if plan_intent:
                            persistence_outcome["intent_name"] = plan_intent

                browse_pagination = result.get("availability_pagination")
                if isinstance(browse_pagination, dict) and not persistence_outcome.get(
                    "availability_pagination"
                ):
                    persistence_outcome["availability_pagination"] = browse_pagination

                if outcome_status in (
                    "NEEDS_CLARIFICATION",
                    "AWAITING_CONFIRMATION",
                    "AWAITING_CAPABILITY",
                    "READY",
                    "EXECUTED",
                    "success",
                ):
                    new_session_state = _session_projector.project(
                        persistence_outcome,
                        outcome_status,
                        merged_luma_response,
                        previous_session,
                        request.user_id,
                        session_store=_session_store,
                    )
                    if new_session_state is None and outcome_status in ("READY", "success"):
                        # Preserve chat history when no durable session payload was produced.
                        # EXECUTED durable intents must rebuild via build_session_state_from_outcome.
                        new_session_state = previous_session or {}

                    if new_session_state is not None:
                        from core.orchestration.nlu.conversation_memory import (
                            append_messages_turn,
                        )

                        new_session_state = append_messages_turn(
                            new_session_state,
                            request.text,
                            result.get("text") or outcome.get("text"),
                        )
                        save_session(request.user_id, new_session_state)
                        session_saved = True
                        saved_session_state = new_session_state
                        if is_trace_enabled():
                            _stages = StageRunner()
                            _stages.save_session(
                                new_session_state=new_session_state,
                                user_id=request.user_id,
                            )
                            reloaded_state = get_session(request.user_id)
                            _stages.reload_session(
                                saved_state=new_session_state,
                                reloaded_state=reloaded_state,
                                user_id=request.user_id,
                            )
                        if decision_trace_enabled:
                            reloaded_state = get_session(request.user_id)
                            emit_reload_verify(
                                saved_state=new_session_state,
                                reloaded_state=reloaded_state,
                            )
                        log_event = (
                            "session_preserved_on_ready"
                            if outcome_status == "READY"
                            else "[session] save"
                        )
                        if not decision_trace_enabled:
                            logger.info(
                                log_event,
                                extra={
                                    "user_id": request.user_id,
                                    "transaction_id": transaction_id,
                                    "intent": new_session_state.get("intent_name")
                                    or new_session_state.get("intent"),
                                    "status": new_session_state.get("status"),
                                    "missing_slots": new_session_state.get("missing_slots", []),
                                    "slots": list((new_session_state.get("slots") or {}).keys()),
                                },
                            )

                        decision = result.get("_decision")
                        if decision and isinstance(decision, dict):
                            if "facts" not in decision:
                                decision["facts"] = {}
                            if not isinstance(decision["facts"], dict):
                                decision["facts"] = {}
                            if "facts" in outcome and isinstance(outcome["facts"], dict):
                                slot_attempts = outcome["facts"].get("slot_attempts")
                                if slot_attempts is not None:
                                    decision["facts"]["slot_attempts"] = (
                                        slot_attempts.copy()
                                        if isinstance(slot_attempts, dict)
                                        else slot_attempts
                                    )

                if decision_trace_enabled:
                    emit_persist_save_for_outcome(
                        outcome_status=outcome_status,
                        saved=session_saved,
                        new_session_state=saved_session_state,
                    )
        elif decision_trace_enabled:
            emit_persist_save_for_outcome(
                outcome_status=None,
                saved=False,
            )

        # Convert to response model
        outcome_for_response = result.get("outcome")
        if isinstance(outcome_for_response, dict):
            outcome_for_response = dict(outcome_for_response)
            browse_pagination = result.get("availability_pagination")
            if isinstance(browse_pagination, dict):
                outcome_for_response.setdefault(
                    "availability_pagination", browse_pagination
                )
        response_text = result.get("text")
        if not response_text and isinstance(outcome_for_response, dict):
            response_text = outcome_for_response.get("text")

        invariant_trace = None
        if is_trace_enabled():
            invariant_trace = finalize_turn_trace()

        decision_trace_fields = {
            "trace_view": trace_view if decision_trace_enabled else None,
            "decision_trace": None,
            "decision_trace_text": None,
        }
        if decision_trace_enabled:
            emit_turn_outcome(
                outcome=outcome_for_response,
                success=result.get("success", False),
                handler_delegated=(
                    isinstance(outcome_for_response, dict)
                    and outcome_for_response.get("status") == "HANDLER_DELEGATED"
                ),
                result=result,
                inputs_evaluated={
                    "success": result.get("success", False),
                    "error": result.get("error"),
                },
            )
            frozen_trace = finalize_decision_trace()
            if frozen_trace is not None:
                decision_trace_fields = build_trace_response_fields(
                    trace_to_dict(frozen_trace),
                    trace_view,
                )

        log_decision_trace_text(logger, decision_trace_fields)

        return MessageResponse(
            success=result.get("success", False),
            outcome=outcome_for_response,
            text=response_text,
            error=result.get("error"),
            message=result.get("message"),
            invariant_trace=invariant_trace,
            **decision_trace_fields,
        )

    except ContractViolation as e:
        raise HTTPException(
            status_code=400,
            detail={"success": False, "error": "contract_violation", "message": str(e)},
        )
    except UpstreamError as e:
        raise HTTPException(
            status_code=502,
            detail={"success": False, "error": "upstream_error", "message": str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"success": False, "error": "internal_error", "message": str(e)},
        )
    finally:
        try:
            from core.tracing.decision_trace import clear_request_decision_trace_context

            clear_request_decision_trace_context()
        except ImportError:
            pass


@router.get("/session/{user_id}")
async def get_user_session(user_id: str):
    """Return persisted session for a user (debug / chat REPL status command)."""
    return {"session": get_session(user_id)}


@router.delete("/session/{user_id}")
async def delete_user_session(user_id: str):
    """Clear persisted session for a user (chat REPL reset command)."""
    clear_session(user_id)
    return {"success": True}
