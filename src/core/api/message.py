"""
Message API — HTTP boundary for the orchestration layer.

Receives HTTP requests and delegates turn orchestration to ConversationEngine.

Production flow:
    POST /api/message
        ↓  trace setup (optional)
        ↓  load session → _raw_session (unfiltered)
        ↓  resolve-or-create tenant customer → session.customer_id
        ↓  filter session_state only for capability context
    ConversationEngine.process_turn(_raw_session)
        ↓  plan → OFF_TOPIC render / browse short-circuit → eligibility → execution → rendering
        → result { success, outcome, plan?, text, ... }
    post_message()
        ↓  capability boundary (only if outcome.status == AWAITING_CAPABILITY)
            → early return if still pending (no persist)
            → else merge facts and continue
        ↓  handler boundary (only if HANDLER_DELEGATED — extension RAG)
        ↓  SessionProjector + save_session (selected statuses)
        → MessageResponse

Session contract:
    - HTTP loads the raw session from the store as _raw_session.
    - ConversationEngine receives _raw_session (unfiltered) as session_state.
    - TurnPlanner owns interpreting session contents (merge eligibility, confirmation gate).
    - HTTP filters session_state to NEEDS_CLARIFICATION or AWAITING_CAPABILITY only
      for apply_capability_to_result context; that filtered value is never forwarded
      to the engine. _raw_session is still used for engine input, handler delegation,
      and persistence.
    - Persistence runs for outcome statuses: NEEDS_CLARIFICATION,
      AWAITING_CONFIRMATION, AWAITING_CAPABILITY, READY, EXECUTED, success.
"""

import json
import copy
import logging
import os
import uuid
from typing import Mapping, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

# Ensure environment variables are loaded at startup
# Import app module which loads .env files
import core.app  # noqa: F401
from core.api.capability_boundary import apply_capability_to_result
from core.adapters.customer_resolver import resolve_tenant_customer_projection
from core.adapters.clients.customer_client import CustomerClient
from core.adapters.errors import ContractViolation, UpstreamError
from core.execution.clients.availability_client import AvailabilityClient
from core.execution.clients.booking_client import BookingClient
from core.engine.conversation_engine import ConversationEngine
from core.session.session_manager import clear_session, get_session
from core.session.session_schema_v2 import prepare_session_for_load
from core.session.turn_persistence import project_and_persist_turn_result, resolve_projection_status
from core.rendering.llm_renderer import LlmRenderRequest, render_handler_response, render_llm
from core.session.assistant_proposals import create_assistant_proposals

# Module-level execution clients — core owns these; callers pass text only.
_availability_client = AvailabilityClient()
_booking_client = BookingClient()
_customer_client = CustomerClient()

# Production orchestration engine — single instance, reused across requests.
_engine = ConversationEngine()

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
    organization_id: int = Field(..., gt=0)
    customer_id: Optional[int] = Field(default=None, gt=0)
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    customer_name: Optional[str] = None
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
    # Bootstrap tenant-neutral extensions once per process.
    global _BOOTSTRAPPED
    if not _BOOTSTRAPPED and _extensions_available and register_default_extensions:
        try:
            register_default_extensions()
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
        loaded_session = get_session(request.organization_id, request.user_id)
        previous_session = copy.deepcopy(loaded_session) if loaded_session else None
        working_session = (
            loaded_session
            if loaded_session is not None
            else prepare_session_for_load(None)
        )
        session_state = working_session
        # Keep the unfiltered session so HANDLER_DELEGATED can preserve booking state
        _raw_session = working_session

        from core.customer_identification import (
            customer_channel_fingerprint,
            reusable_authoritative_contact,
        )

        incoming_channel_fingerprint = customer_channel_fingerprint(
            phone=request.customer_phone,
            email=request.customer_email,
        )
        reusable_contact = reusable_authoritative_contact(_raw_session)
        stored_channel_fingerprint = (
            reusable_contact.get("channel_fingerprint")
            if isinstance(reusable_contact, Mapping)
            else None
        )
        if (
            isinstance(_raw_session, dict)
            and incoming_channel_fingerprint is not None
            and stored_channel_fingerprint != incoming_channel_fingerprint
        ):
            # Re-resolve legacy, malformed, or differently-bound projections.
            _raw_session["customer_id"] = None
            _raw_session["customer_contact"] = None

        # Resolve or create tenant customer at ingress (never invent at booking dispatch).
        # Chat user_id is a session key only — not a commerce customer primary key.
        resolved_customer = resolve_tenant_customer_projection(
            organization_id=request.organization_id,
            customer_client=_customer_client,
            session=_raw_session if isinstance(_raw_session, dict) else None,
            customer_id=request.customer_id,
            phone=request.customer_phone,
            email=request.customer_email,
            name=request.customer_name,
        )
        resolved_customer_id = (
            resolved_customer.get("id")
            if isinstance(resolved_customer, Mapping)
            else None
        )
        if resolved_customer_id is not None and isinstance(_raw_session, dict):
            _raw_session["customer_id"] = resolved_customer_id
            existing_contact = _raw_session.get("customer_contact")
            if (
                isinstance(existing_contact, dict)
                and existing_contact.get("customer_id") != resolved_customer_id
            ):
                _raw_session["customer_contact"] = None
        if isinstance(_raw_session, dict):
            from core.customer_identification import authoritative_contact
            persisted_name = (
                resolved_customer.get("name")
                if isinstance(resolved_customer, Mapping)
                else None
            )
            contact = authoritative_contact(
                resolved_customer_id,
                persisted_name,
                channel_fingerprint=incoming_channel_fingerprint,
            )
            if contact is not None:
                _raw_session["customer_contact"] = contact

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
        # Pass the unfiltered request-scoped working session through the turn.
        result = _engine.process_turn(
            user_id=request.user_id,
            text=request.text,
            session_state=_raw_session,
            availability_client=_availability_client,
            booking_client=_booking_client,
            customer_client=_customer_client,
            customer_phone=request.customer_phone,
            customer_email=request.customer_email,
            organization_id=request.organization_id,
            customer_id=resolved_customer_id,
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
            # Resume uses the same workflow_resume mechanism as OFF_TOPIC.
            if handler_result.render_instruction:
                from core.rendering.workflow_resume import (
                    attach_resume_to_handler_render,
                    compose_pending_confirmation_resume,
                )

                conversation_history = (_raw_session or {}).get("messages", [])
                confirmation_suffix = compose_pending_confirmation_resume(
                    _raw_session or {}
                )
                render_instruction, render_facts = attach_resume_to_handler_render(
                    handler_result.render_instruction,
                    session_state=_raw_session or {},
                    facts=dict(handler_result.facts or {}),
                )
                handler_render_result = render_handler_response(
                    LlmRenderRequest(
                        render_instruction=render_instruction,
                        facts=render_facts,
                        conversation_history=conversation_history,
                        user_request=request.text,
                    )
                )
                rendered_text = handler_render_result.text
                if confirmation_suffix:
                    rendered_text = compose_pending_confirmation_resume(
                        _raw_session or {}, rendered_text
                    )
                structured_context = render_facts.get("structured_context")
                try:
                    result["_handler_assistant_proposals"] = create_assistant_proposals(
                        handler_render_result,
                        structured_context=(
                            structured_context
                            if isinstance(structured_context, dict)
                            else {}
                        ),
                        handler_name=handler_name,
                        transaction_id=transaction_id,
                    )
                except ValueError as exc:
                    logger.warning("Discarding invalid handler proposal metadata: %s", exc)
                    result["_handler_assistant_proposals"] = []
            else:
                rendered_text = "I'm unable to respond right now."

            outcome["text"] = rendered_text
            result["outcome"] = outcome

            # Update conversation memory in the request-scoped working session.
            from core.adapters.nlu.conversation_memory import update_conversation

            _conv_base = result.get("_working_session") or _raw_session or {}
            _updated_session = update_conversation(
                _conv_base,
                user_text=request.text,
                intent=outcome.get("intent_name", "UNKNOWN"),
                search_query=outcome.get("search_query"),
                assistant_text=rendered_text,
            )
            result["_working_session"] = _updated_session
            result["_handler_conversation_update"] = _updated_session.get(
                "conversation"
            )

        # Handle session persistence after response
        session_saved = False
        saved_session_state = None
        if outcome and isinstance(outcome, dict):
            with measure_stage("persistence"):
                outcome_status = outcome.get("status")
                merged_luma_response = result.get("_merged_luma_response")
                current_working_session = (
                    result.get("_working_session") or working_session
                )
                from core.customer_identification import apply_pending_profile_projection
                apply_pending_profile_projection(current_working_session, outcome)
                projection_status = resolve_projection_status(outcome, result=result)

                if outcome_status in (
                    "NEEDS_CLARIFICATION",
                    "AWAITING_CONFIRMATION",
                    "AWAITING_CAPABILITY",
                    "READY",
                    "EXECUTED",
                    "success",
                    "succeeded",
                    "HANDLER_DELEGATED",
                    "OFF_TOPIC",
                ):
                    assistant_text = result.get("text") or outcome.get("text")
                    conversation_messages = [
                        {"role": "user", "text": request.text}
                    ]
                    if assistant_text:
                        conversation_messages.append(
                            {"role": "assistant", "text": assistant_text}
                        )

                    capability_result = None
                    outcome_facts = outcome.get("facts")
                    if isinstance(outcome_facts, dict):
                        payment_satisfied = outcome_facts.get(
                            "payment_satisfied"
                        )
                        active_capability = outcome.get("active_capability")
                        if payment_satisfied is not None or active_capability:
                            capability_result = {
                                "payment_satisfied": payment_satisfied,
                                "active": active_capability,
                            }

                    new_session_state = project_and_persist_turn_result(
                        result=result,
                        outcome=outcome,
                        outcome_status=projection_status,
                        organization_id=request.organization_id,
                        previous_session_state=previous_session,
                        user_id=request.user_id,
                        working_session_state=current_working_session,
                        capability_result=capability_result,
                        handler_conversation_update=result.get(
                            "_handler_conversation_update"
                        ),
                        conversation_messages=conversation_messages,
                        assistant_proposals=(
                            result.get("_handler_assistant_proposals") or None
                        ),
                        assistant_proposal_updates=(
                            (result.get("_merged_luma_response") or {}).get(
                                "_assistant_proposal_updates"
                            )
                            if isinstance(result.get("_merged_luma_response"), dict)
                            else None
                        ),
                        fallback_session_state=current_working_session or {},
                    )

                    if new_session_state is not None:
                        session_saved = True
                        saved_session_state = new_session_state
                        if is_trace_enabled():
                            _stages = StageRunner()
                            _stages.save_session(
                                new_session_state=new_session_state,
                                user_id=request.user_id,
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
                    if session_saved and saved_session_state is not None:
                        reloaded_state = get_session(
                            request.organization_id, request.user_id
                        )
                        emit_reload_verify(
                            saved_state=saved_session_state,
                            reloaded_state=reloaded_state,
                        )
                    else:
                        emit_reload_verify(
                            saved_state=None,
                            reloaded_state=None,
                        )
        elif decision_trace_enabled:
            emit_persist_save_for_outcome(
                outcome_status=None,
                saved=False,
            )
            emit_reload_verify(saved_state=None, reloaded_state=None)

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


@router.get("/organizations/{organization_id}/sessions/{user_id}")
async def get_user_session(organization_id: int, user_id: str):
    """Return persisted session for a user (debug / chat REPL status command)."""
    if organization_id <= 0:
        raise HTTPException(status_code=422, detail="organization_id must be positive")
    return {"session": get_session(organization_id, user_id)}


@router.delete("/organizations/{organization_id}/sessions/{user_id}")
async def delete_user_session(organization_id: int, user_id: str):
    """Clear persisted session for a user (chat REPL reset command)."""
    if organization_id <= 0:
        raise HTTPException(status_code=422, detail="organization_id must be positive")
    clear_session(organization_id, user_id)
    return {"success": True}
