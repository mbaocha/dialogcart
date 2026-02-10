"""
Capability Renderer

Renders text for capability states (AWAITING_CAPABILITY).

This module provides deterministic rendering from semantic signals (status, active_capability, facts).
Currently runs in shadow mode - computes renderer text but adapter text remains source of truth.
"""

from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


def render_capability(
    status: str,
    active_capability: Optional[str],
    facts: Dict[str, Any],
    slots: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Render text for capability states.
    
    This function deterministically renders text from semantic signals:
    - status: Must be "AWAITING_CAPABILITY"
    - active_capability: Capability name (e.g., "payment")
    - facts: Facts dictionary (may contain capability-specific data)
    - slots: Slots dictionary (may contain booking info)
    - context: Optional context dictionary (for accessing external services if needed)
    
    Currently runs in shadow mode - adapter text is still the source of truth.
    This function replicates adapter text generation logic to validate equivalence.
    
    Args:
        status: Planning status (must be "AWAITING_CAPABILITY")
        active_capability: Active capability name (e.g., "payment")
        facts: Facts dictionary
        slots: Slots dictionary
        context: Optional context dictionary with session_slots, session_facts, etc.
    
    Returns:
        Rendered text string, or None if capability not supported or status invalid
    """
    # Only render for AWAITING_CAPABILITY status
    if status != "AWAITING_CAPABILITY":
        return None
    
    if not active_capability:
        return None
    
    # Route to capability-specific renderer
    if active_capability == "payment":
        return _render_payment_capability(facts, slots, context)
    
    # Unknown capability - return None
    logger.debug(f"Unknown capability for rendering: {active_capability}")
    return None


def _render_payment_capability(
    facts: Dict[str, Any],
    slots: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Render payment capability text.
    
    Replicates PaymentAdapter.start() text generation logic:
    - Extracts booking info from context/slots/facts
    - Gets payment URL from payment client
    - Returns payment link message
    
    Args:
        facts: Facts dictionary
        slots: Slots dictionary
        context: Context dictionary with session_slots, session_facts, etc.
    
    Returns:
        Payment link message string, or None if payment setup fails
    """
    # Extract booking info (same logic as PaymentAdapter._extract_booking_info)
    session_facts = {}
    session_slots = {}
    
    if context:
        session_facts = context.get("session_facts", {})
        session_slots = context.get("session_slots", {})
    
    # Merge with facts and slots from current turn
    if isinstance(facts, dict):
        session_facts = {**session_facts, **facts}
    if isinstance(slots, dict):
        session_slots = {**session_slots, **slots}
    
    # Extract booking_code (required for payment URL)
    booking_code = None
    if "booking_code" in session_slots:
        booking_code = session_slots["booking_code"]
    elif "booking_code" in session_facts:
        booking_code = session_facts["booking_code"]
    
    # Try to derive from booking_id if available
    if not booking_code:
        booking_id = session_slots.get("booking_id") or session_facts.get("booking_id")
        if booking_id:
            booking_code = f"booking_{booking_id}"
    
    if not booking_code:
        logger.warning("Payment capability rendering: booking_code not found")
        return None
    
    # Get payment URL from payment client (same as adapter)
    try:
        # Try to get payment client from registry (same instance adapter uses)
        from capabilities.registry import get_adapter
        
        try:
            # Get payment adapter to access its client
            payment_adapter = get_adapter("payment")
            if not hasattr(payment_adapter, "payment_client"):
                logger.debug("Payment adapter does not have payment_client")
                return None
            
            payment_client = payment_adapter.payment_client
        except KeyError:
            # Adapter not registered - shadow rendering cannot proceed
            logger.debug("Payment adapter not registered - skipping shadow rendering")
            return None
        
        # Get payment URL (same as adapter.start())
        url_response = payment_client.get_payment_url(booking_code)
        
        if not url_response.get("success") or not url_response["data"].get("has_payment_intent"):
            return "Payment link not available. Please try again."
        
        payment_url = url_response["data"].get("payment_url")
        if not payment_url:
            return "Payment link not available. Please try again."
        
        # Generate same text as PaymentAdapter.start()
        return (
            "Your booking is being held for 30 minutes.\n"
            "Please complete payment using the link below:\n\n"
            f"{payment_url}"
        )
    
    except ImportError:
        logger.debug("Payment client not available for shadow rendering")
        return None
    except Exception as e:
        logger.warning(f"Payment capability rendering error: {e}")
        return None
