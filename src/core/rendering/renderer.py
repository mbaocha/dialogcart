"""
Rendering Module - Main Renderer

Renders text from decision objects for clarification states.
"""

from typing import Dict, Any, Optional

from .mapper.clarification_mapper import derive_clarification_reason
from .clarification_renderer import render_clarification


def render(decision: Dict[str, Any]) -> Optional[str]:
    """
    Render text from decision object for clarification states.
    
    This is a pure post-processing step that:
    - Detects clarification state from decision
    - Derives clarification reason
    - Renders text using templates
    - Falls back to generic template if rendering returns None
    
    Args:
        decision: Decision dictionary with:
            - plan: Plan dictionary with status
            - facts: Facts dictionary with missing_slots and slots
    
    Returns:
        Rendered text string if clarification is detected, None otherwise.
        Falls back to generic template if specific rendering fails.
    """
    # Detect clarification state
    plan = decision.get("plan", {})
    facts = decision.get("facts", {})
    
    plan_status = plan.get("status")
    missing_slots = facts.get("missing_slots", [])
    
    # Check if clarification is needed
    is_clarification = (
        plan_status == "NEEDS_CLARIFICATION" or
        (isinstance(missing_slots, list) and len(missing_slots) > 0)
    )
    
    if not is_clarification:
        return None
    
    # Build decision dict for mapper
    rendering_decision = {
        "status": "NEEDS_CLARIFICATION",
        "missing_slots": missing_slots if isinstance(missing_slots, list) else []
    }
    
    # Derive clarification reason
    reason = derive_clarification_reason(rendering_decision)
    if not reason:
        # Fallback to generic template
        reason = "NEEDS_CLARIFICATION"
    
    # Get slots for template interpolation
    slots = facts.get("slots", {})
    
    try:
        # Render with slots
        render_spec = render_clarification(reason, slots)
        return render_spec.text
    except Exception:
        # Fallback to generic template
        try:
            render_spec = render_clarification("NEEDS_CLARIFICATION", {})
            return render_spec.text
        except Exception:
            # Last resort: return None (shouldn't happen if templates are valid)
            return None

