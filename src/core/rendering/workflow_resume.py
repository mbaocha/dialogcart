"""Workflow-owned resume instructions for digression returns.

Reads persisted planner/session conversational step only. Does not re-plan,
re-run clarification, or invent booking progression. Handlers request resume;
rendering uses this instruction for wording.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.planning.policy.base_intents import is_core_intent
from core.session.confirmation_gate import get_confirmation_state


@dataclass(frozen=True)
class ResumeInstruction:
    """Generic resume guidance derived from workflow/session state."""

    text: str


def _session_booking_intent(session: Dict[str, Any]) -> str:
    intent = session.get("intent_name") or session.get("intent") or ""
    if isinstance(intent, dict):
        return str(intent.get("name") or "")
    if intent:
        return str(intent)
    planning = session.get("planning") if isinstance(session.get("planning"), dict) else {}
    planning_intent = planning.get("intent_name") or planning.get("intent") or ""
    if isinstance(planning_intent, dict):
        planning_intent = planning_intent.get("name") or ""
    return str(planning_intent) if planning_intent else ""


def _has_active_booking(session: Dict[str, Any]) -> bool:
    intent = _session_booking_intent(session)
    return bool(intent and is_core_intent(intent))


def _missing_slots(session: Dict[str, Any]) -> List[str]:
    missing = session.get("missing_slots")
    if isinstance(missing, list) and missing:
        return [str(s) for s in missing if s]
    planning = session.get("planning") if isinstance(session.get("planning"), dict) else {}
    planning_missing = planning.get("missing_slots")
    if isinstance(planning_missing, list):
        return [str(s) for s in planning_missing if s]
    return []


def _service_candidate_labels(session: Dict[str, Any]) -> List[str]:
    raw = session.get("service_candidates")
    if not isinstance(raw, list):
        return []
    labels: List[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            labels.append(item.strip())
        elif isinstance(item, dict):
            text = item.get("text") or item.get("name") or item.get("service_id")
            if isinstance(text, str) and text.strip():
                labels.append(text.strip())
    return labels


def _primary_ask_slots(missing: List[str]) -> List[str]:
    """Mirror clarification focus: service disambiguation first, else remaining missing."""
    if "service_id" in missing:
        return ["service_id"]
    return list(missing)


def _slot_ask_clause(slot: str, *, candidates: List[str]) -> str:
    if slot == "service_id":
        if candidates:
            options = ", ".join(f'"{c}"' for c in candidates)
            return (
                f"Ask which service they want to book and present these options: {options}."
            )
        return "Ask which service they would like to book."
    if slot == "date" or slot == "date_range":
        return "Ask which date they would like."
    if slot == "time":
        return "Ask which time works for them."
    return f"Ask for the missing field: {slot}."


def build_resume_instruction(
    session_state: Optional[Dict[str, Any]],
) -> Optional[ResumeInstruction]:
    """Build a wording instruction to resume the paused conversational step.

    Returns a soft booking invite when no durable booking workflow is active.
    """
    if not isinstance(session_state, dict):
        return ResumeInstruction(
            text=(
                "After answering, briefly invite the user to book a service or "
                "appointment with this business. Do not invent services or prices."
            )
        )

    if not _has_active_booking(session_state):
        return ResumeInstruction(
            text=(
                "After answering, briefly invite the user to book a service or "
                "appointment with this business. Do not invent services or prices."
            )
        )

    awaiting = session_state.get("awaiting")
    planning = (
        session_state.get("planning")
        if isinstance(session_state.get("planning"), dict)
        else {}
    )
    if not awaiting and isinstance(planning, dict):
        awaiting = planning.get("awaiting")

    if (
        get_confirmation_state(session_state) == "pending"
        or awaiting == "USER_CONFIRMATION"
    ):
        return ResumeInstruction(
            text=(
                "After answering, briefly continue the booking: ask the user to "
                "confirm the appointment. Do not invent booking details."
            )
        )

    if awaiting == "TIME_SELECTION":
        return ResumeInstruction(
            text=(
                "After answering, briefly continue the booking: ask the user to "
                "choose a time. You may refer to times already offered in the "
                "conversation. Do not invent availability."
            )
        )

    awaiting_slot = session_state.get("awaiting_slot")
    missing = _missing_slots(session_state)
    if awaiting_slot and awaiting_slot not in missing:
        missing = [str(awaiting_slot)] + missing

    ask_slots = _primary_ask_slots(missing)
    candidates = _service_candidate_labels(session_state)

    if ask_slots:
        clauses = [
            _slot_ask_clause(slot, candidates=candidates if slot == "service_id" else [])
            for slot in ask_slots
        ]
        return ResumeInstruction(
            text=(
                "After answering, briefly continue the booking where it left off. "
                + " ".join(clauses)
                + " Do not ask for other booking fields yet. Do not invent facts."
            )
        )

    return ResumeInstruction(
        text=(
            "After answering, briefly continue the user's booking where it left off. "
            "Do not invent which field to ask for beyond the conversation so far."
        )
    )
