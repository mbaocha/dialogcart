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


def _planning(session: Dict[str, Any]) -> Dict[str, Any]:
    planning = session.get("planning")
    return planning if isinstance(planning, dict) else {}


def _missing_slots(session: Dict[str, Any]) -> List[str]:
    missing = session.get("missing_slots")
    if isinstance(missing, list) and missing:
        return [str(s) for s in missing if s]
    planning_missing = _planning(session).get("missing_slots")
    if isinstance(planning_missing, list):
        return [str(s) for s in planning_missing if s]
    return []


def _candidate_labels(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    labels: List[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            labels.append(item.strip())
        elif isinstance(item, dict):
            text = (
                item.get("text")
                or item.get("name")
                or item.get("service_id")
                or item.get("id")
            )
            if isinstance(text, str) and text.strip():
                labels.append(text.strip())
    return labels


def _service_candidate_labels(session: Dict[str, Any]) -> List[str]:
    raw = session.get("service_candidates")
    if not isinstance(raw, list):
        raw = _planning(session).get("service_candidates")
    return _candidate_labels(raw)


def _catalog_candidate_labels(
    session: Dict[str, Any],
    slot: str,
) -> List[str]:
    """Candidates for the active missing catalog slot (service_id unchanged)."""
    from core.adapters.nlu.entity_schema_builder import catalog_candidates_for_slot

    entity_schema = session.get("_entity_schema")
    if not isinstance(entity_schema, dict):
        entity_schema = _planning(session).get("_entity_schema")
    if not isinstance(entity_schema, dict):
        entity_schema = None

    if slot == "service_id":
        return _service_candidate_labels(session)

    raw = catalog_candidates_for_slot(
        session, slot, entity_schema=entity_schema
    )
    if not raw:
        planning = _planning(session)
        raw = catalog_candidates_for_slot(
            planning, slot, entity_schema=entity_schema
        )
    return _candidate_labels(raw)


def _presented_availability(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Read already-presented offers from canonical availability accessors."""
    from core.workflows.availability.presentation import (
        presented_availability_from_session,
    )

    return presented_availability_from_session(session)


def _presented_time_labels(session: Dict[str, Any]) -> List[str]:
    presented = _presented_availability(session)
    if not presented:
        return []
    labels: List[str] = []
    times = presented.get("times")
    if isinstance(times, list):
        for item in times:
            if isinstance(item, str) and item.strip():
                labels.append(item.strip())
            elif isinstance(item, dict):
                text = (
                    item.get("label")
                    or item.get("time")
                    or item.get("start_time")
                    or item.get("display")
                )
                if isinstance(text, str) and text.strip():
                    labels.append(text.strip())
    if labels:
        return labels
    slots = presented.get("slots")
    if isinstance(slots, list):
        for item in slots:
            if not isinstance(item, dict):
                continue
            text = (
                item.get("label")
                or item.get("time")
                or item.get("start_time")
                or item.get("display")
            )
            if isinstance(text, str) and text.strip():
                labels.append(text.strip())
    return labels


def _has_presented_offers(session: Dict[str, Any]) -> bool:
    presented = _presented_availability(session)
    if not presented:
        return False
    times = presented.get("times")
    slots = presented.get("slots")
    has_times = isinstance(times, list) and any(times)
    has_slots = isinstance(slots, list) and bool(slots)
    return has_times or has_slots


def _awaiting(session: Dict[str, Any]) -> Optional[str]:
    awaiting = session.get("awaiting")
    if awaiting:
        return str(awaiting)
    planning_awaiting = _planning(session).get("awaiting")
    if planning_awaiting:
        return str(planning_awaiting)
    return None


def _time_selection_pending(
    session: Dict[str, Any],
    *,
    awaiting: Optional[str],
    missing: List[str],
) -> bool:
    """True when the pending obligation is choosing from already-presented times."""
    if awaiting == "TIME_SELECTION":
        return True
    if not _has_presented_offers(session):
        return False
    awaiting_slot = session.get("awaiting_slot")
    if awaiting_slot == "time" or "time" in missing:
        return True
    return False


def _primary_ask_slots(
    missing: List[str],
    *,
    presented_offers: bool = False,
    ask_next: Optional[str] = None,
) -> List[str]:
    """Ask the planning ask_next (first missing in policy order); offers may pin to time."""
    if presented_offers and "time" in missing:
        return ["time"]
    if isinstance(ask_next, str) and ask_next.strip():
        return [ask_next.strip()]
    if missing:
        return [str(missing[0])]
    return []


def _slot_ask_clause(
    slot: str,
    *,
    candidates: List[str],
    description: Optional[str] = None,
) -> str:
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
    label = description or slot.replace("_", " ")
    if candidates:
        options = ", ".join(f'"{c}"' for c in candidates)
        return f"Ask for {label} and present these options: {options}."
    return f"Ask for {label}."


def _time_selection_resume(session: Dict[str, Any]) -> ResumeInstruction:
    times = _presented_time_labels(session)
    if times:
        listed = ", ".join(times[:8])
        return ResumeInstruction(
            text=(
                "After answering, briefly continue the pending step: restate these "
                f"already-presented times ({listed}) and ask which works best. "
                "Do not invent times. Do not search for new availability. "
                "Do not restart the booking."
            )
        )
    return ResumeInstruction(
        text=(
            "After answering, briefly continue the pending step: ask the user to "
            "choose a time from those already offered in the conversation. "
            "Do not invent availability. Do not search for new times. "
            "Do not restart the booking."
        )
    )


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

    awaiting = _awaiting(session_state)

    if (
        get_confirmation_state(session_state) == "pending"
        or awaiting == "USER_CONFIRMATION"
    ):
        return ResumeInstruction(
            text=(
                "After answering, briefly continue the pending confirmation: ask the "
                "user to confirm the appointment. Do not invent booking details. "
                "Do not restart the booking."
            )
        )

    missing = _missing_slots(session_state)
    awaiting_slot = session_state.get("awaiting_slot")
    if awaiting_slot and awaiting_slot not in missing:
        missing = [str(awaiting_slot)] + missing

    if _time_selection_pending(
        session_state, awaiting=awaiting, missing=missing
    ):
        return _time_selection_resume(session_state)

    presented_offers = _has_presented_offers(session_state)
    ask_next = session_state.get("ask_next")
    if not isinstance(ask_next, str) or not ask_next.strip():
        ask_next = missing[0] if missing else None
    ask_slots = _primary_ask_slots(
        missing,
        presented_offers=presented_offers,
        ask_next=ask_next if isinstance(ask_next, str) else None,
    )
    candidates_by_slot = {
        slot: _catalog_candidate_labels(session_state, slot) for slot in ask_slots
    }
    entity_schema = session_state.get("_entity_schema")
    if not isinstance(entity_schema, dict):
        entity_schema = _planning(session_state).get("_entity_schema")
    if not isinstance(entity_schema, dict):
        entity_schema = None

    if ask_slots:
        from core.adapters.nlu.entity_schema_builder import (
            description_for_planning_slot,
        )

        clauses = [
            _slot_ask_clause(
                slot,
                candidates=candidates_by_slot.get(slot) or [],
                description=description_for_planning_slot(entity_schema, slot),
            )
            for slot in ask_slots
        ]
        return ResumeInstruction(
            text=(
                "After answering, briefly continue the pending clarification where it "
                "left off. "
                + " ".join(clauses)
                + " Do not ask for other fields yet. Do not invent facts. "
                "Do not restart the booking."
            )
        )

    return ResumeInstruction(
        text=(
            "After answering, briefly continue the user's pending step where it left "
            "off. Do not invent which field to ask for beyond the conversation so far. "
            "Do not restart the booking."
        )
    )


def attach_resume_to_handler_render(
    render_instruction: str,
    *,
    session_state: Optional[Dict[str, Any]],
    facts: Optional[Dict[str, Any]] = None,
) -> tuple[str, Dict[str, Any]]:
    """Append shared workflow_resume to a handler render instruction + facts.

    Does not alter the handler's informational answer content — only adds resume
    guidance after answering (same mechanism OFF_TOPIC uses).
    """
    out_facts = dict(facts or {})
    instruction = render_instruction or ""
    resume = build_resume_instruction(session_state)
    if resume and resume.text:
        out_facts["resume_instruction"] = resume.text
        instruction = (
            f"{instruction}\n\n"
            "After answering the user's question using the Facts, "
            f"follow this resume guidance: {resume.text}"
        )
    return instruction, out_facts
