"""
Orchestration Layer - Message API Endpoint

FastAPI endpoint for processing user messages.

This is the public API entry point for the orchestration layer.
It receives HTTP requests and delegates to the orchestrator.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import logging
import uuid
import json

# Ensure environment variables are loaded at startup
# Import app module which loads .env files
import core.app  # noqa: F401

from core.orchestration.orchestrator import handle_message
from core.orchestration.errors import ContractViolation, UpstreamError
from core.orchestration.session import get_session, save_session, clear_session
from core.orchestration.api.session_merge import build_session_state_from_outcome

# Capability runner (optional - only used when AWAITING_CAPABILITY)
# This is the SINGLE integration point between core and capabilities.
# Core never imports adapters or branches on capability names.
try:
    from capabilities.runner import CapabilityRunner
    _capability_runner = CapabilityRunner()
    _capability_runner_available = True
except ImportError:
    # Capabilities module not available - runner will not be used
    # Removing capabilities restores exact previous behavior (no crashes, no side effects)
    _capability_runner = None
    _capability_runner_available = False

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
    # Bootstrap capability adapters (once per process)
    global _BOOTSTRAPPED
    if not _BOOTSTRAPPED and _capability_runner_available:
        try:
            from capabilities.bootstrap import register_default_adapters
            register_default_adapters(organization_id=request.organization_id)
            _BOOTSTRAPPED = True
        except ImportError:
            # Bootstrap module not available - adapters will not be registered
            # This is fine - runner will passthrough if adapter not found
            logger.debug("Capability bootstrap module not available - adapters not registered")
        except Exception as e:
            # Bootstrap failed - log but don't crash
            logger.warning(f"Failed to bootstrap capability adapters: {e}. Continuing without capabilities.")
    
    try:
        # Generate transaction_id if not provided (per-request tracing only)
        transaction_id = request.transaction_id or str(uuid.uuid4())
        
        # Load session at request start
        session_state = get_session(request.user_id)
        
        # Explicit session load logging
        logger.info("[session] load", extra={
            "user_id": request.user_id,
            "transaction_id": transaction_id,
            "found": session_state is not None,
            "status": session_state.get("status") if session_state else None,
            "intent": session_state.get("intent") if session_state else None
        })
        
        # Only consider session if status == "NEEDS_CLARIFICATION" or "AWAITING_CAPABILITY"
        # AWAITING_CAPABILITY sessions need to be loaded to preserve active_capability
        if session_state and session_state.get("status") not in ("NEEDS_CLARIFICATION", "AWAITING_CAPABILITY"):
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
            transaction_id=transaction_id
        )
        
        # Handle capability activation (if core emits AWAITING_CAPABILITY)
        outcome = result.get("outcome")
        if outcome and isinstance(outcome, dict) and outcome.get("status") == "AWAITING_CAPABILITY":
            if _capability_runner_available and _capability_runner:
                # Build context for adapter (read-only access to session)
                context = {
                    "user_id": request.user_id,
                    "session_slots": session_state.get("slots", {}) if session_state else {},
                    "session_facts": outcome.get("facts", {}),
                    "domain": request.domain,
                    "timezone": request.timezone,
                    "organization_id": request.organization_id,
                    "transaction_id": transaction_id
                }
                
                # Route to capability runner
                runner_result = _capability_runner.handle(
                    user_input=request.text,
                    core_outcome=outcome,
                    context=context
                )
                
                if not runner_result.passthrough:
                    # Adapter is active → return adapter prompt
                    # Do not proceed to session persistence or core execution
                    return MessageResponse(
                        success=True,
                        outcome={
                            "status": "AWAITING_CAPABILITY",
                            "text": runner_result.text,
                            "active_capability": runner_result.active_capability,
                            "awaiting": "CAPABILITY"
                        }
                    )
                
                # Adapter completed → merge facts into session
                if runner_result.facts:
                    # Merge adapter facts into outcome.facts
                    if "facts" not in outcome:
                        outcome["facts"] = {}
                    if not isinstance(outcome["facts"], dict):
                        outcome["facts"] = {}
                    
                    # Merge adapter facts (e.g., payment_satisfied: True)
                    outcome["facts"].update(runner_result.facts)
                    
                    # Clear active_capability (adapter completed)
                    outcome["active_capability"] = None
                    
                    # Update status to allow core to proceed on next turn
                    # Status will be re-evaluated by core with merged facts
                    outcome["status"] = "READY"
                    outcome["awaiting"] = None
                    
                    # Update result with merged outcome
                    result["outcome"] = outcome
                    
                    logger.info(
                        f"[capability] Adapter completed, merged facts: {list(runner_result.facts.keys())} "
                        f"user_id={request.user_id} transaction_id={transaction_id}"
                    )
                    
                    # Re-enter core normally on next turn
                    # Facts are merged into outcome.facts, which will be available
                    # when core runs again (via session merge or direct outcome)
        
        # Handle session persistence after response
        if outcome and isinstance(outcome, dict):
            outcome_status = outcome.get("status")
            
            if outcome_status in ("NEEDS_CLARIFICATION", "AWAITING_CAPABILITY"):
                # Save session state for follow-up
                # Extract merged Luma response from result (private field)
                merged_luma_response = result.get("_merged_luma_response")
                # Pass previous session state for context (intent change detection, etc.)
                new_session_state = build_session_state_from_outcome(
                    outcome, outcome_status, merged_luma_response, session_state, request.user_id
                )
                if new_session_state:
                    save_session(request.user_id, new_session_state)
                    logger.info("[session] save", extra={
                        "user_id": request.user_id,
                        "transaction_id": transaction_id,
                        "intent": new_session_state.get("intent"),
                        "status": new_session_state.get("status"),
                        "missing_slots": new_session_state.get("missing_slots", [])
                    })
                    
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
                                decision["facts"]["slot_attempts"] = slot_attempts.copy() if isinstance(slot_attempts, dict) else slot_attempts
            elif outcome_status == "READY":
                # FIX: Do NOT clear session on READY unless execution has occurred
                # Sessions should only be cleared after confirmed execution event
                # This preserves intent + slots for follow-up modification turns (e.g. "make it 4pm")
                # Keep session state unchanged - clearing will happen after execution
                logger.info(
                    f"session_preserved_on_ready user_id={request.user_id} transaction_id={transaction_id} "
                    f"(session preserved for follow-up modifications)"
                )
        
        # Convert to response model
        return MessageResponse(
            success=result.get("success", False),
            outcome=result.get("outcome"),
            error=result.get("error"),
            message=result.get("message")
        )
        
    except ContractViolation as e:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "contract_violation",
                "message": str(e)
            }
        )
    except UpstreamError as e:
        raise HTTPException(
            status_code=502,
            detail={
                "success": False,
                "error": "upstream_error",
                "message": str(e)
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "internal_error",
                "message": str(e)
            }
        )

