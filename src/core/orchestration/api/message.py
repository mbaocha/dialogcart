"""
Orchestration Layer - Message API Endpoint

FastAPI endpoint for processing user messages.

This is the public API entry point for the orchestration layer.
It receives HTTP requests and delegates to the orchestrator.
"""

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Ensure environment variables are loaded at startup
# Import app module which loads .env files
import core.app  # noqa: F401
from core.orchestration.api.capability_boundary import apply_capability_to_result
from core.orchestration.api.session_merge import build_session_state_from_outcome
from core.orchestration.errors import ContractViolation, UpstreamError
from core.orchestration.orchestrator import handle_message
from core.orchestration.session import clear_session, get_session, save_session
from core.rendering.llm_renderer import LlmRenderRequest, render_llm

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

# Bootstrap flag to ensure adapters are registered exactly once
_BOOTSTRAPPED = False


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
    error: Optional[str] = None
    message: Optional[str] = None


@router.post("/message", response_model=MessageResponse)
async def post_message(request: MessageRequest):
    """
    Process a user message through the orchestration pipeline.

    Session handling:
    - Loads session at request start (if status == "NEEDS_CLARIFICATION")
    - Merges session state with Luma response (handled in handle_message)
    - Saves session if outcome.status == "NEEDS_CLARIFICATION" or "AWAITING_CAPABILITY"
    - Preserves session if outcome.status == "READY"

    Capability handling:
    - If core emits AWAITING_CAPABILITY, routes to capability runner
    - Runner manages adapter lifecycle and returns facts when complete
    - Facts are merged into outcome.facts and saved to session
    - On next turn, core reads facts from session and proceeds

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
        # Generate transaction_id if not provided (per-request tracing only)
        transaction_id = request.transaction_id or str(uuid.uuid4())

        # Load session at request start
        session_state = get_session(request.user_id)
        # Keep the unfiltered session so HANDLER_DELEGATED can preserve booking state
        _raw_session = session_state

        # Explicit session load logging
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

        # Call handle_message with session state (merge happens inside)
        result = handle_message(
            user_id=request.user_id,
            text=request.text,
            domain=request.domain,
            timezone=request.timezone,
            organization_id=request.organization_id,
            session_state=session_state,
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
                return MessageResponse(**early)
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
        if outcome and isinstance(outcome, dict):
            outcome_status = outcome.get("status")

            if outcome_status in ("NEEDS_CLARIFICATION", "AWAITING_CAPABILITY"):
                # Save session state for follow-up
                # Extract merged Luma response from result (private field)
                merged_luma_response = result.get("_merged_luma_response")
                # Pass previous session state for context (intent change detection, etc.)
                new_session_state = build_session_state_from_outcome(
                    outcome,
                    outcome_status,
                    merged_luma_response,
                    session_state,
                    request.user_id,
                )
                if new_session_state:
                    from core.orchestration.nlu.conversation_memory import (
                        append_messages_turn,
                    )

                    new_session_state = append_messages_turn(
                        new_session_state, request.text, result.get("text") or outcome.get("text")
                    )
                    save_session(request.user_id, new_session_state)
                    logger.info(
                        "[session] save",
                        extra={
                            "user_id": request.user_id,
                            "transaction_id": transaction_id,
                            "intent": new_session_state.get("intent"),
                            "status": new_session_state.get("status"),
                            "missing_slots": new_session_state.get("missing_slots", []),
                        },
                    )

                    # Wire slot_attempts into decision for test/API access
                    # slot_attempts is incremented in build_session_state_from_outcome
                    # and stored in outcome.facts, but decision was set earlier
                    # Update decision.facts to match outcome.facts for consistency
                    decision = result.get("_decision")
                    if decision and isinstance(decision, dict):
                        if "facts" not in decision:
                            decision["facts"] = {}
                        if not isinstance(decision["facts"], dict):
                            decision["facts"] = {}
                        # Copy slot_attempts from outcome.facts (updated by build_session_state_from_outcome)
                        if "facts" in outcome and isinstance(outcome["facts"], dict):
                            slot_attempts = outcome["facts"].get("slot_attempts")
                            if slot_attempts is not None:
                                decision["facts"]["slot_attempts"] = (
                                    slot_attempts.copy()
                                    if isinstance(slot_attempts, dict)
                                    else slot_attempts
                                )
            elif outcome_status == "READY":
                # Preserve intent + slots for follow-up modifications ("make it 4pm").
                # Also append messages to the existing session so conversation history
                # is available on the next turn regardless of outcome type.
                from core.orchestration.nlu.conversation_memory import append_messages_turn

                _ready_base = _raw_session or {}
                _ready_session = append_messages_turn(
                    _ready_base, request.text, result.get("text") or outcome.get("text")
                )
                save_session(request.user_id, _ready_session)
                logger.info(
                    f"session_preserved_on_ready user_id={request.user_id} transaction_id={transaction_id} "
                    f"(session preserved for follow-up modifications)"
                )

        # Convert to response model
        return MessageResponse(
            success=result.get("success", False),
            outcome=result.get("outcome"),
            error=result.get("error"),
            message=result.get("message"),
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
