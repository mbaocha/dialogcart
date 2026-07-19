"""Confirmation gate turn classification (yes / no / another request).

While a booking confirmation is pending, every turn is classified once.
Downstream code should branch on this decision rather than re-checking
raw Luma intents and session flags in multiple places.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ConfirmationGateTurn(str, Enum):
    YES = "YES"
    NO = "NO"
    ANOTHER_REQUEST = "ANOTHER_REQUEST"


def get_confirmation_state(session_state: Optional[Dict[str, Any]]) -> Optional[str]:
    """Read canonical top-level state, with a temporary nested-session fallback."""
    if not isinstance(session_state, dict):
        return None
    if "confirmation_state" in session_state:
        return session_state.get("confirmation_state")
    booking = session_state.get("booking")
    if isinstance(booking, dict) and booking.get("confirmation_state") is not None:
        return booking.get("confirmation_state")
    return None


def set_confirmation_state(
    state: Optional[Dict[str, Any]],
    value: Optional[str],
) -> Dict[str, Any]:
    """Set canonical top-level ``confirmation_state``.

    ``value`` is ``\"pending\"``, ``\"confirmed\"``, or ``None`` (cleared).
    Any legacy nested value is removed so fallback reads cannot resurrect it.
    Mutates ``state`` in place and returns it.
    """
    if not isinstance(state, dict):
        return {}

    booking = state.get("booking")
    if isinstance(booking, dict) and "confirmation_state" in booking:
        booking = dict(booking)
        booking.pop("confirmation_state", None)
        state["booking"] = booking

    if value is None:
        state.pop("confirmation_state", None)
        return state

    state["confirmation_state"] = value
    return state


def consume_confirmation_state(
    state: Optional[Dict[str, Any]],
    *,
    reason: str = "",
) -> Dict[str, Any]:
    """Consume canonical confirmation authorization and remove any legacy nested value."""
    if not isinstance(state, dict):
        return {}
    set_confirmation_state(state, None)
    if reason:
        logger.debug(
            "[BOOKING_CONFIRMATION] consume_confirmation_state reason=%s", reason)
    return state


def consume_create_appointment_confirmation(
    session_state: Dict[str, Any],
    merged_luma_response: Optional[Dict[str, Any]] = None,
    *,
    reason: str = "commit_consumed",
) -> None:
    """Successful CREATE_APPOINTMENT commit consumes pre-commit confirmation."""
    consume_confirmation_state(session_state, reason=reason)
    if isinstance(merged_luma_response, dict):
        consume_confirmation_state(merged_luma_response, reason=reason)


def normalize_confirmation_state(
    state: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Migrate a legacy nested value to the canonical top-level field."""
    if not isinstance(state, dict):
        return {}

    booking = state.get("booking")
    booking_state = (
        booking.get("confirmation_state") if isinstance(
            booking, dict) else None
    )
    has_top_state = "confirmation_state" in state
    top_state = state.get("confirmation_state")

    if has_top_state:
        return set_confirmation_state(state, top_state)
    if booking_state is not None:
        return set_confirmation_state(state, booking_state)
    return set_confirmation_state(state, None)


def is_confirmation_gate_open(session_state: Optional[Dict[str, Any]]) -> bool:
    """True only when confirmation authorization is explicitly pending."""
    return get_confirmation_state(session_state) == "pending"


def _raw_luma_intent_name(luma_response: Optional[Dict[str, Any]]) -> str:
    if not isinstance(luma_response, dict):
        return ""
    intent = luma_response.get("intent")
    if isinstance(intent, dict):
        return intent.get("name") or ""
    if isinstance(intent, str):
        return intent
    return ""


def classify_confirmation_gate_turn(
    luma_response: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]],
) -> Optional[ConfirmationGateTurn]:
    """Classify this turn relative to the booking confirmation gate.

    The gate only owns the user's relationship to the pending confirmation.
    It does not interpret booking revisions or the next request:
      1. YES — raw CONFIRM_ACTION
      2. NO — raw REJECT_ACTION
      3. ANOTHER_REQUEST — every other turn while the gate is open

    ``None`` means the confirmation gate is not active for this turn.
    """
    if not is_confirmation_gate_open(session_state):
        return None

    raw_intent = _raw_luma_intent_name(luma_response)
    if raw_intent == "CONFIRM_ACTION":
        return ConfirmationGateTurn.YES
    if raw_intent == "REJECT_ACTION":
        return ConfirmationGateTurn.NO

    return ConfirmationGateTurn.ANOTHER_REQUEST
