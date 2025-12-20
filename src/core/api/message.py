"""
Message API Endpoint

FastAPI endpoint for processing user messages.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Ensure environment variables are loaded at startup
# Import app module which loads .env files
import core.app  # noqa: F401

from core.orchestration.orchestrator import handle_message
from core.errors.exceptions import ContractViolation, UpstreamError

router = APIRouter()


class MessageRequest(BaseModel):
    """Request model for /message endpoint."""
    user_id: str
    text: str
    domain: Optional[str] = "service"
    timezone: Optional[str] = "UTC"


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
    
    Args:
        request: Message request with user_id, text, domain, timezone
        
    Returns:
        Message response with success status and outcome or error
    """
    try:
        result = handle_message(
            user_id=request.user_id,
            text=request.text,
            domain=request.domain,
            timezone=request.timezone
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

