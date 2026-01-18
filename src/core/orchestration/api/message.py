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

# Ensure environment variables are loaded at startup
# Import app module which loads .env files
import core.app  # noqa: F401

from core.orchestration.orchestrator import handle_message
from core.orchestration.errors import ContractViolation, UpstreamError
from core.orchestration.session import get_session, save_session, clear_session
from core.orchestration.api.session_merge import build_session_state_from_outcome

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
    error: Optional[str] = None
    message: Optional[str] = None


@router.post("/message", response_model=MessageResponse)
async def post_message(request: MessageRequest):
    """
    Process a user message through the orchestration pipeline.
    
    Session handling:
    - Loads session at request start (if status == "NEEDS_CLARIFICATION")
    - Merges session state with Luma response (handled in handle_message)
    - Saves session if outcome.status == "NEEDS_CLARIFICATION"
    - Clears session if outcome.status == "READY"
    
    Args:
        request: Message request with user_id, text, domain, timezone
        
    Returns:
        Message response with success status and outcome or error
    """
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
        
        # Only consider session if status == "NEEDS_CLARIFICATION"
        if session_state and session_state.get("status") != "NEEDS_CLARIFICATION":
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
        
        # Handle session persistence after response
        outcome = result.get("outcome")
        if outcome and isinstance(outcome, dict):
            outcome_status = outcome.get("status")
            
            if outcome_status == "NEEDS_CLARIFICATION":
                # Save session state for follow-up
                # Extract merged Luma response from result (private field)
                merged_luma_response = result.get("_merged_luma_response")
                # Pass previous session state for context (intent change detection, etc.)
                new_session_state = build_session_state_from_outcome(
                    outcome, outcome_status, merged_luma_response, session_state
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
            elif outcome_status == "READY":
                # Clear session on completion
                clear_session(request.user_id)
                logger.info(
                    f"session_cleared user_id={request.user_id} transaction_id={transaction_id}"
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

