"""Clarification-readiness evidence for Stage 08 Decision.

Planning / Evaluate projects Stage 04 slot-ask inputs (missing, promptable,
declined, default ask_next, planning-evidence gates). Stage 08 consumes this
for action/status/stage/awaiting selection — Decision does not re-derive the
default clarification envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from core.planning.pipeline.types import SlotTurnState
from core.planning.planner.missing_slots import derive_ask_next
from core.planning.planning_evidence import require_planning_evidence


@dataclass(frozen=True)
class ClarificationReadinessEvidence:
    """Typed clarification inputs Decision consumes when selecting outcomes.

    Does not select ``action`` / ``status`` / ``stage`` / ``awaiting``.
    """

    missing_slots: Tuple[str, ...] = ()
    promptable_slots: Tuple[str, ...] = ()
    declined_slots: Tuple[str, ...] = ()
    needs_clarification: bool = False
    default_ask_next: Optional[str] = None
    has_planning_evidence: bool = False
    turn_understanding: Optional[str] = None
    block_auto_reshow: bool = False


def awaiting_from_ask_next(ask_next: Optional[str]) -> Optional[str]:
    """Awaiting for unanswered ask_next is the ask_next slot itself.

    Distinct from TIME_SELECTION (reserved for TIME_MATCH_MISMATCH presentation).
    """
    if not ask_next:
        return None
    return str(ask_next)


def _turn_understanding_from_payload(payload: Dict[str, Any]) -> Optional[str]:
    turn = payload.get("turn")
    if isinstance(turn, dict):
        value = turn.get("understanding")
        if isinstance(value, str) and value:
            return value
    value = payload.get("understanding")
    if isinstance(value, str) and value:
        return value
    return None


def build_clarification_readiness_evidence(
    *,
    slot_state: SlotTurnState,
    payload: Dict[str, Any],
) -> ClarificationReadinessEvidence:
    """Project Stage 04 clarification fields into typed readiness evidence."""
    missing_slots = list(slot_state.missing_slots)
    promptable_slots = list(getattr(slot_state, "promptable_slots", None) or [])
    declined_slots = list(getattr(slot_state, "declined_slots", None) or [])
    needs_clarification = bool(slot_state.needs_clarification)

    ask_next = slot_state.ask_next
    if ask_next is None:
        ask_next = derive_ask_next(missing_slots, promptable_slots)

    has_planning_evidence = require_planning_evidence(payload)
    turn_understanding = _turn_understanding_from_payload(payload)
    # No current-turn planning evidence with open slots must not auto-reshow
    # availability; reconciliation owns clarify/recovery precedence.
    block_auto_reshow = (not has_planning_evidence) and bool(missing_slots)

    return ClarificationReadinessEvidence(
        missing_slots=tuple(missing_slots),
        promptable_slots=tuple(promptable_slots),
        declined_slots=tuple(declined_slots),
        needs_clarification=needs_clarification,
        default_ask_next=ask_next,
        has_planning_evidence=has_planning_evidence,
        turn_understanding=turn_understanding,
        block_auto_reshow=block_auto_reshow,
    )
