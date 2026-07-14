"""
TEMPORARY migration parity tests for PR2 (SEARCH_AVAILABILITY business facts).

This module exists only to compare pre-PR2 SEARCH gating against the new
availability_check_required policy path before merge. It embeds a minimal
copy of the legacy SEARCH skip rules — do NOT treat this as a permanent spec.

Remove this file once:
- PR2 is merged and stable, and
- permanent golden planner contract tests cover the same scenarios
  (see test_search_business_facts_parity.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import pytest

from core.workflows.availability.fingerprint import compute_availability_fingerprint
from core.planning.facts import build_policy_execution_flags
from core.policy.intent_policy import (
    get_execution_steps,
    get_planning_required_slots,
    select_next_execution_step,
)

_SEARCH = "SEARCH_AVAILABILITY"


@dataclass(frozen=True)
class _PlannerScenario:
    name: str
    intent_name: str
    slots: Dict[str, Any]
    session_state: Optional[Dict[str, Any]] = None
    availability_resolved: bool = False
    confirmation_state: Optional[str] = None
    luma_response: Optional[Dict[str, Any]] = None


def _legacy_would_select_search(
    intent_name: str,
    slots: Dict[str, Any],
    *,
    availability_resolved: bool,
    confirmation_state: Optional[str],
) -> bool:
    """Pre-PR2 selector: True iff SEARCH_AVAILABILITY is the first eligible step.

    Local copy of pre-PR2 SEARCH skip rules only — delete with this module.
    """
    intent_upper = (intent_name or "").upper()
    if not intent_upper:
        return False

    steps = get_execution_steps(intent_name)
    flags = {
        "availability_resolved": availability_resolved,
        "confirmation_state": confirmation_state,
        "booking_hold_created": bool(slots.get("booking_id")),
    }
    collected_slot_names = {
        name for name, value in slots.items() if value is not None
    }
    planning_required = set(get_planning_required_slots(intent_name))

    for step in steps:
        action = step.get("action")
        requires = step.get("requires") or []
        mode = step.get("mode", "exploratory")
        step_required = set(step.get("required_slots") or [])

        if mode == "committing":
            if not planning_required.issubset(collected_slot_names):
                continue
        else:
            if action == "FETCH_BOOKING":
                if "booking_id" in collected_slot_names:
                    continue
            elif not step_required.issubset(collected_slot_names):
                continue

        legacy_requires = [
            req for req in requires if req != "availability_check_required"
        ]
        if "availability_resolved" in legacy_requires and not availability_resolved:
            continue
        if "booking_hold_created" in legacy_requires:
            if not flags["booking_hold_created"]:
                continue
        if "booking_id_resolved" in legacy_requires:
            if "booking_id" not in collected_slot_names:
                continue
        if "confirmation_state_confirmed" in legacy_requires:
            if confirmation_state != "confirmed":
                continue

        if action == "CREATE_BOOKING_HOLD" and "booking_id" in collected_slot_names:
            continue

        if intent_upper in ("MODIFY_BOOKING", "MODIFY_RESERVATION"):
            if action == "SEARCH_AVAILABILITY" and confirmation_state == "confirmed":
                continue
            if action == "APPLY_MODIFICATION" and confirmation_state != "confirmed":
                continue
        elif intent_upper == "CREATE_APPOINTMENT":
            if action == "SEARCH_AVAILABILITY" and availability_resolved:
                continue
            if action == "CONFIRM_APPOINTMENT" and confirmation_state != "confirmed":
                continue
        else:
            if action == "SEARCH_AVAILABILITY" and availability_resolved:
                continue

        return action == _SEARCH

    return False


def _current_selected_action(
    scenario: _PlannerScenario,
) -> Optional[str]:
    flags = build_policy_execution_flags(
        intent_name=scenario.intent_name,
        slots=scenario.slots,
        session_state=scenario.session_state,
        luma_response=scenario.luma_response or {},
        availability_resolved=scenario.availability_resolved,
        confirmation_state=scenario.confirmation_state,
    )
    step = select_next_execution_step(scenario.intent_name, scenario.slots, flags)
    return step.get("action") if step else None


def _current_would_select_search(scenario: _PlannerScenario) -> bool:
    return _current_selected_action(scenario) == _SEARCH


def _assert_search_parity(scenario: _PlannerScenario) -> None:
    legacy = _legacy_would_select_search(
        scenario.intent_name,
        scenario.slots,
        availability_resolved=scenario.availability_resolved,
        confirmation_state=scenario.confirmation_state,
    )
    current = _current_would_select_search(scenario)
    assert legacy == current, (
        f"[{scenario.name}] SEARCH parity mismatch: "
        f"legacy={legacy}, current={current}, "
        f"selected_action={_current_selected_action(scenario)!r}"
    )


# Representative fixtures — behavioural contract established pre-PR2 for SEARCH.
_PARITY_SCENARIOS = [
    _PlannerScenario(
        name="initial_booking",
        intent_name="CREATE_APPOINTMENT",
        slots={"service_id": "svc-haircut", "organization_id": "org-1"},
        availability_resolved=False,
    ),
    _PlannerScenario(
        name="existing_valid_availability",
        intent_name="CREATE_APPOINTMENT",
        slots={
            "service_id": "svc-haircut",
            "date": "2026-07-10",
            "organization_id": "org-1",
        },
        session_state={
            "availability_fingerprint": compute_availability_fingerprint(
                {
                    "service_id": "svc-haircut",
                    "date": "2026-07-10",
                    "organization_id": "org-1",
                },
                intent_name="CREATE_APPOINTMENT",
            )
        },
        availability_resolved=True,
    ),
    _PlannerScenario(
        name="service_change",
        intent_name="CREATE_APPOINTMENT",
        slots={
            "service_id": "svc-spa",
            "date": "2026-07-10",
            "organization_id": "org-1",
        },
        session_state={
            "availability_fingerprint": compute_availability_fingerprint(
                {
                    "service_id": "svc-haircut",
                    "date": "2026-07-10",
                    "organization_id": "org-1",
                },
                intent_name="CREATE_APPOINTMENT",
            )
        },
        availability_resolved=False,
    ),
    _PlannerScenario(
        name="date_change",
        intent_name="CREATE_APPOINTMENT",
        slots={
            "service_id": "svc-haircut",
            "date": "2026-07-15",
            "organization_id": "org-1",
        },
        session_state={
            "availability_fingerprint": compute_availability_fingerprint(
                {
                    "service_id": "svc-haircut",
                    "date": "2026-07-10",
                    "organization_id": "org-1",
                },
                intent_name="CREATE_APPOINTMENT",
            )
        },
        availability_resolved=False,
    ),
    _PlannerScenario(
        name="reservation_after_search",
        intent_name="CREATE_RESERVATION",
        slots={
            "service_id": "svc-room",
            "date_range": {"start": "2026-07-10", "end": "2026-07-12"},
            "organization_id": "org-1",
        },
        session_state={
            "availability_fingerprint": compute_availability_fingerprint(
                {
                    "service_id": "svc-room",
                    "date_range": {"start": "2026-07-10", "end": "2026-07-12"},
                    "organization_id": "org-1",
                },
                intent_name="CREATE_RESERVATION",
            )
        },
        availability_resolved=True,
    ),
    _PlannerScenario(
        name="modify_preview_not_confirmed",
        intent_name="MODIFY_BOOKING",
        slots={"booking_id": "bk-1", "date": "2026-07-10"},
        availability_resolved=False,
        confirmation_state=None,
    ),
]


@pytest.mark.parametrize(
    "scenario",
    _PARITY_SCENARIOS,
    ids=[s.name for s in _PARITY_SCENARIOS],
)
def test_pr2_search_migration_parity(scenario: _PlannerScenario) -> None:
    """Legacy SEARCH gating must match business-fact-driven selector (PR2)."""
    _assert_search_parity(scenario)


def test_pr2_search_migration_parity_modify_confirmed_skips_search() -> None:
    """MODIFY when confirmed: legacy and current both skip SEARCH."""
    slots = {
        "booking_id": "bk-1",
        "date": "2026-07-10",
        "time": "14:00",
    }
    session = {
        "availability_fingerprint": compute_availability_fingerprint(
            slots, intent_name="MODIFY_BOOKING"
        )
    }
    scenario = _PlannerScenario(
        name="modify_confirmed",
        intent_name="MODIFY_BOOKING",
        slots=slots,
        session_state=session,
        availability_resolved=True,
        confirmation_state="confirmed",
    )
    _assert_search_parity(scenario)
    assert _current_selected_action(scenario) == "APPLY_MODIFICATION"
