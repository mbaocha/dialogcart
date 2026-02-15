"""
Rendering Module - Main Renderer

Renders text from decision objects for clarification states.
"""

from typing import Any, Dict, Optional

from .clarification_renderer import render_clarification
from .mapper.clarification_mapper import derive_clarification_reason


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
    # Try facts first, then plan, then decision level (defensive fallback)
    missing_slots = facts.get("missing_slots", [])
    if not missing_slots:
        missing_slots = plan.get("missing_slots", [])
    if not missing_slots:
        missing_slots = decision.get("missing_slots", [])

    # Check if clarification is needed
    is_clarification = plan_status == "NEEDS_CLARIFICATION" or (
        isinstance(missing_slots, list) and len(missing_slots) > 0
    )

    if not is_clarification:
        return None

    # Build decision dict for mapper
    rendering_decision = {
        "status": "NEEDS_CLARIFICATION",
        "missing_slots": missing_slots if isinstance(missing_slots, list) else [],
    }

    # Derive clarification reason
    print(f"[RENDER_DEBUG] rendering_decision: {rendering_decision}")
    print(f"[RENDER_DEBUG] missing_slots: {missing_slots}")
    reason = derive_clarification_reason(rendering_decision)
    print(f"[RENDER_DEBUG] reason from mapper: {reason!r}")
    if not reason:
        # Fallback to generic template
        reason = "NEEDS_CLARIFICATION"
        print(f"[RENDER_DEBUG] reason after fallback: {reason!r}")

    # Get slots for template interpolation
    slots = facts.get("slots", {})

    # Extract slot_attempts and get attempt count for first missing slot
    # Prefer decision.get("slot_attempts") (top-level) if present, otherwise fallback to facts.slot_attempts
    slot_attempts = decision.get("slot_attempts")
    if not isinstance(slot_attempts, dict):
        slot_attempts = facts.get("slot_attempts", {})
    first_missing = (
        missing_slots[0] if isinstance(missing_slots, list) and missing_slots else None
    )
    attempt_count = slot_attempts.get(first_missing, 0) if first_missing else 0

    print(
        f"[RENDER_DEBUG] Calling render_clarification with reason={reason!r}, attempt_count={attempt_count}"
    )
    try:
        # Render with slots and attempt_count
        render_spec = render_clarification(reason, slots, attempt_count)
        clarification_text = render_spec.text

        # Phase 2: Optionally prefix acknowledgement if last_filled_slot is set
        # Only add ack if: last_filled_slot is set, differs from current missing slot, and attempt_count < 1
        session = decision.get("_session", {})
        last_filled_slot = (
            session.get("last_filled_slot") if isinstance(session, dict) else None
        )

        if last_filled_slot and attempt_count < 1:
            # Get current missing slot from decision["plan"]["missing_slots"][0] if present
            current_missing_slot = None
            plan_obj = decision.get("plan", {})
            if isinstance(plan_obj, dict):
                plan_missing_slots = plan_obj.get("missing_slots", [])
                if isinstance(plan_missing_slots, list) and plan_missing_slots:
                    current_missing_slot = plan_missing_slots[0]

            # Only add acknowledgement if last_filled_slot differs from current missing slot
            if last_filled_slot != current_missing_slot:
                # Prefix short acknowledgement (e.g., "Got it. " or "Okay. ")
                acknowledgement = f"Got it. "
                return acknowledgement + clarification_text

        return clarification_text
    except Exception:
        # Fallback to generic template
        try:
            render_spec = render_clarification("NEEDS_CLARIFICATION", {}, 0)
            return render_spec.text
        except Exception:
            # Last resort: return None (shouldn't happen if templates are valid)
            return None
