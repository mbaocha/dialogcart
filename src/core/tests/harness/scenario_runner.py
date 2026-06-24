"""Multi-turn scenario runner for execution and smoke tests."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from core.orchestration.orchestrator import handle_message
from core.tests.harness.clients import TestCatalogClient, TestLumaClient
from core.tests.harness.mock_clients import (
    create_mock_availability_client,
    create_mock_booking_client,
    create_mock_organization_client,
)
from core.tests.harness.org_setup import setup_test_org_domain
from core.tests.harness.session_store import MockSessionStore
from core.tests.planning.adapter import normalize_planning_outcome


def extract_plan_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Extract plan dict from handle_message result (planning or post-execution)."""
    plan = result.get("plan")
    if not plan:
        result_data = result.get("result", {})
        if isinstance(result_data, dict):
            if "plan" in result_data:
                plan = result_data.get("plan")
            elif "action" in result_data or "status" in result_data:
                plan = result_data
    return plan or {}


def assert_turn_expectations(
    result: Dict[str, Any],
    expectations: Dict[str, Any],
    turn_number: int,
    scenario_name: str,
    *,
    assert_missing_slots: bool = False,
    assert_execution_calls: bool = False,
    mock_booking_client: Any = None,
) -> None:
    """Assert turn expectations against normalized planning outcome."""
    assert result.get("success") is True, (
        f"[{scenario_name}] Turn {turn_number}: Expected success=True, "
        f"got {result.get('success')} with error: {result.get('error')}"
    )

    normalized = normalize_planning_outcome(result)
    plan = normalized.get("plan", {}) or normalized

    if "intent" in expectations:
        expected_intent = expectations["intent"]
        actual_intent = normalized.get("intent") or plan.get("intent_name")
        assert actual_intent == expected_intent, (
            f"[{scenario_name}] Turn {turn_number}: Expected intent {expected_intent}, "
            f"got {actual_intent}"
        )

    if "status" in expectations:
        expected_status = expectations["status"]
        actual_status = normalized.get("status")
        assert actual_status == expected_status, (
            f"[{scenario_name}] Turn {turn_number}: Expected status {expected_status}, "
            f"got {actual_status}"
        )

    if "action" in expectations:
        expected_action = expectations["action"]
        actual_action = plan.get("action")
        assert actual_action == expected_action, (
            f"[{scenario_name}] Turn {turn_number}: Expected action {expected_action}, "
            f"got {actual_action}"
        )

    if assert_missing_slots and "missing_slots" in expectations:
        expected_missing = expectations["missing_slots"]
        actual_missing = normalized.get("missing_slots", [])
        assert actual_missing == expected_missing, (
            f"[{scenario_name}] Turn {turn_number}: Expected missing_slots "
            f"{expected_missing}, got {actual_missing}"
        )

    if assert_execution_calls and mock_booking_client is not None:
        expected_action = expectations.get("action")
        if expected_action == "CONFIRM_APPOINTMENT":
            assert mock_booking_client.create_booking.called, (
                f"[{scenario_name}] Turn {turn_number}: Expected create_booking to be called"
            )
        elif expected_action == "CONFIRM_CANCELLATION":
            assert mock_booking_client.cancel_booking.called, (
                f"[{scenario_name}] Turn {turn_number}: Expected cancel_booking to be called"
            )
        elif expected_action == "FINALIZE_RESERVATION":
            assert mock_booking_client.confirm_booking.called, (
                f"[{scenario_name}] Turn {turn_number}: Expected confirm_booking to be called"
            )
        elif expected_action == "APPLY_MODIFICATION":
            assert mock_booking_client.update_booking.called, (
                f"[{scenario_name}] Turn {turn_number}: Expected update_booking to be called"
            )


def _persist_session_for_next_turn(
    result: Dict[str, Any],
    normalized: Dict[str, Any],
    session_store: MockSessionStore,
    user_id: str,
    current_intent: Optional[str],
) -> None:
    """Mirror session fields required for multi-turn execution flows."""
    plan_obj = normalized.get("plan", {})
    execution_result = result.get("result", {})
    slots = dict(normalized.get("slots", {}))
    plan_slots = plan_obj.get("slots", {})
    if isinstance(plan_slots, dict):
        slots.update(plan_slots)
    if isinstance(execution_result, dict):
        exec_booking_id = execution_result.get("booking_id")
        if exec_booking_id:
            slots["booking_id"] = exec_booking_id

    session_state: Dict[str, Any] = {
        "intent_name": current_intent or "",
        "slots": slots,
        "missing_slots": normalized.get("missing_slots", []),
        "status": normalized.get("status"),
    }
    if "stage" in plan_obj:
        session_state["stage"] = plan_obj.get("stage")
    if "action" in plan_obj:
        session_state["action"] = plan_obj.get("action")

    if isinstance(execution_result, dict):
        if execution_result.get("availability_fingerprint"):
            session_state["availability_fingerprint"] = execution_result[
                "availability_fingerprint"
            ]
        elif plan_obj.get("availability_fingerprint"):
            session_state["availability_fingerprint"] = plan_obj[
                "availability_fingerprint"
            ]
        if execution_result.get("resolved_datetime_range"):
            session_state["resolved_datetime_range"] = execution_result[
                "resolved_datetime_range"
            ]
        elif plan_obj.get("resolved_datetime_range"):
            session_state["resolved_datetime_range"] = plan_obj[
                "resolved_datetime_range"
            ]

    previous_session = session_store.get_session(user_id)
    if previous_session:
        if (
            "availability_fingerprint" not in session_state
            and previous_session.get("availability_fingerprint")
        ):
            session_state["availability_fingerprint"] = previous_session[
                "availability_fingerprint"
            ]
        if (
            "resolved_datetime_range" not in session_state
            and previous_session.get("resolved_datetime_range")
        ):
            session_state["resolved_datetime_range"] = previous_session[
                "resolved_datetime_range"
            ]

    session_store.save_session(user_id, session_state)


def run_multi_turn_scenario(
    scenario: Dict[str, Any],
    *,
    frozen_time: Optional[datetime] = None,
    user_id_prefix: str = "test_scenario",
    inject_execution_clients: bool = True,
    assert_missing_slots: bool = False,
    assert_execution_calls: bool = False,
    catalog_client: Any = None,
) -> None:
    """
    Run a multi-turn YAML-style scenario through handle_message.

    Args:
        scenario: Dict with name, domain, aliases, turns
        frozen_time: Fixed clock for relative dates (tomorrow)
        inject_execution_clients: Pass mock booking/availability clients
        assert_missing_slots: Assert missing_slots when specified in expect
        assert_execution_calls: Assert booking client calls for commit actions
        catalog_client: Optional catalog client (defaults to TestCatalogClient)
    """
    scenario_name = scenario.get("name", "unnamed")
    turns = scenario.get("turns", [])
    domain = scenario.get("domain", "service")
    aliases = scenario.get("aliases", {"haircut": "haircut"})

    setup_test_org_domain(domain)
    from core.orchestration.cache.catalog_cache import catalog_cache
    import os

    test_org_id = int(os.getenv("ORG_ID", "1"))
    catalog_cache._mem_cache.pop((test_org_id, domain), None)

    luma_client = TestLumaClient(test_aliases=aliases)
    if catalog_client is None:
        catalog_client = TestCatalogClient(test_aliases=aliases, domain=domain)

    mock_booking_client = None
    mock_availability_client = None
    mock_org_client = create_mock_organization_client(
        business_category_id=1 if domain == "service" else 2
    )
    if inject_execution_clients:
        mock_booking_client = create_mock_booking_client()
        mock_availability_client = create_mock_availability_client(
            frozen_time=frozen_time
        )

    session_store = MockSessionStore()
    user_id = f"{user_id_prefix}_{scenario_name}_{id(scenario)}"
    previous_intent = None

    for turn_idx, turn in enumerate(turns, start=1):
        sentence = turn["sentence"]
        expectations = turn.get("expect", {})

        kwargs: Dict[str, Any] = {
            "text": sentence,
            "user_id": user_id,
            "luma_client": luma_client,
            "catalog_client": catalog_client,
            "organization_client": mock_org_client,
            "domain": domain,
            "timezone": "UTC",
            "organization_id": test_org_id,
        }
        if inject_execution_clients:
            kwargs["booking_client"] = mock_booking_client
            kwargs["availability_client"] = mock_availability_client
        if frozen_time is not None:
            kwargs["frozen_time"] = frozen_time
        if turn_idx > 1:
            kwargs["session_store"] = session_store

        result = handle_message(**kwargs)

        assert_turn_expectations(
            result,
            expectations,
            turn_idx,
            scenario_name,
            assert_missing_slots=assert_missing_slots,
            assert_execution_calls=assert_execution_calls,
            mock_booking_client=mock_booking_client,
        )

        normalized = normalize_planning_outcome(result)
        current_intent = normalized.get("intent") or normalized.get("plan", {}).get(
            "intent_name"
        )

        if turn_idx > 1 and previous_intent:
            if expectations.get("intent_switched"):
                assert current_intent != previous_intent, (
                    f"[{scenario_name}] Turn {turn_idx}: Expected intent switch "
                    f"from {previous_intent}, got {current_intent}"
                )
            elif expectations.get("intent_preserved", False):
                assert current_intent == previous_intent or current_intent != "", (
                    f"[{scenario_name}] Turn {turn_idx}: Expected intent preserved "
                    f"({previous_intent}), got {current_intent}"
                )

        if current_intent:
            previous_intent = current_intent

        if turn_idx < len(turns):
            _persist_session_for_next_turn(
                result, normalized, session_store, user_id, current_intent
            )
