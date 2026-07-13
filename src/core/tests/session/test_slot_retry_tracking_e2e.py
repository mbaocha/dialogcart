"""
End-to-End Test: Slot Retry Tracking

Under executable_with=[service_id], incomplete CREATE_APPOINTMENT turns are READY
with exploratory SEARCH_AVAILABILITY. When availability_client is omitted, assert
the planning/missing-client response shape rather than clarification/slot_attempts.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

os.environ["CORE_EXECUTION_MODE"] = "test"

src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

try:
    from dotenv import load_dotenv

    project_root = Path(__file__).parent.parent.parent.parent
    core_env_file = Path(__file__).parent.parent.parent / ".env"
    env_file = project_root / ".env"
    env_local_file = project_root / ".env.local"

    if env_file.exists():
        load_dotenv(env_file, override=False)
    if core_env_file.exists():
        load_dotenv(core_env_file, override=True)
    if env_local_file.exists():
        load_dotenv(env_local_file, override=True)
except ImportError:
    pass
except Exception:
    pass

from core.orchestration.api.session_merge import build_session_state_from_outcome
from core.orchestration.orchestrator import handle_message
from core.orchestration.session import clear_session, get_session, save_session

from core.tests.harness.clients import ScriptedLumaClient, TestCatalogClient, TestLumaClient
from core.tests.harness.org_setup import get_customer_details, setup_test_org_domain

_TEMPORAL_SLOT_KEYS = frozenset(
    {"date", "time", "date_range", "datetime_range", "start_date", "end_date"}
)
_APPOINTMENT_DATE = "2026-01-16"
_APPOINTMENT_TIME = "14:00"


def _missing_slots(outcome: Dict[str, Any]) -> list:
    missing = outcome.get("missing_slots")
    if missing is None:
        missing = (outcome.get("facts") or {}).get("missing_slots", [])
    return list(missing) if isinstance(missing, list) else []


def _assert_ready_missing_client(result: Dict[str, Any], expected_missing: list) -> None:
    assert result is not None
    assert result.get("success") is True, f"Expected success, got: {result.get('error')}"
    outcome = result.get("outcome") or {}
    assert outcome.get("status") == "READY", (
        f"Expected READY for executable_with=[service_id], got {outcome.get('status')}"
    )
    missing = _missing_slots(outcome)
    assert set(expected_missing).issubset(set(missing)), (
        f"Expected missing_slots to include {expected_missing}, got {missing}"
    )
    allowed = outcome.get("allowed_actions") or []
    assert "SEARCH_AVAILABILITY" in allowed, (
        f"Expected SEARCH_AVAILABILITY in allowed_actions, got {allowed}"
    )
    assert "text" not in result, (
        f"Expected no clarification text on missing-client READY response, "
        f"got keys={list(result.keys())}"
    )


def _create_appointment_response(
    *,
    slots: Dict[str, Any],
    missing_slots: list,
    date: Optional[str] = None,
    time: Optional[str] = None,
) -> Dict[str, Any]:
    """Scripted Luma payload that keeps the durable booking intent.

    Unconfirmed date/time use facts + proposals (canonical). Durable
    slots.date / slots.time stay absent until bind/confirm.
    """
    raw = dict(slots or {})
    date_val = date or raw.pop("date", None)
    time_val = time or raw.pop("time", None)
    for key in _TEMPORAL_SLOT_KEYS:
        raw.pop(key, None)

    durable_slots = raw
    service_id = durable_slots.get("service_id", "haircut")
    facts: Dict[str, Any] = {"service_id": service_id}

    response: Dict[str, Any] = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT", "confidence": 0.95},
        "needs_clarification": False,
        "booking": {
            "booking_type": "service",
            "services": [
                {"text": "haircut", "canonical": "beauty_and_wellness.haircut"}
            ],
            "booking_state": "RESOLVED",
        },
        "facts": facts,
        "slots": durable_slots,
        "missing_slots": list(missing_slots),
        "context": {},
    }

    if date_val:
        facts["dates"] = [date_val]
        response["date_proposal"] = {"mode": "single_day", "start": date_val}

    if time_val:
        facts["times"] = [time_val]
        response["time_proposal"] = {"mode": "exact", "value": time_val}
        response["time_constraint"] = {
            "mode": "exact",
            "start": time_val,
            "end": time_val,
        }

    return response


def _seed_presented_availability_for_time_bind(user_id: str) -> None:
    """Ensure the 2pm turn can bind against a presented 14:00 offer."""
    session = get_session(user_id) or {}
    session["presented_availability"] = {
        "search_date": _APPOINTMENT_DATE,
        "slots": [
            {
                "start": f"{_APPOINTMENT_DATE}T{_APPOINTMENT_TIME}:00Z",
                "end": f"{_APPOINTMENT_DATE}T14:30:00Z",
                "starts_at": f"{_APPOINTMENT_DATE}T{_APPOINTMENT_TIME}:00Z",
            }
        ],
    }
    if "date_proposal" not in session:
        session["date_proposal"] = {
            "mode": "single_day",
            "start": _APPOINTMENT_DATE,
        }
    facts = session.get("facts")
    if not isinstance(facts, dict):
        facts = {}
    facts.setdefault("dates", [_APPOINTMENT_DATE])
    session["facts"] = facts
    save_session(user_id, session)


def test_e2e_slot_retry_tracking_increment_and_reset():
    """
    Incomplete booking turns without availability_client follow READY exploratory
    policy (service_id present → SEARCH_AVAILABILITY allowed, no clarification text).

    Off-topic-looking utterances are scripted as CREATE_APPOINTMENT so this test
    validates durable booking continuation, not live NLU / HANDLER_DELEGATED.
    """
    user_id = "test_slot_retry_tracking_001"
    domain = "service"

    clear_session(user_id)
    setup_test_org_domain(domain)
    aliases = {"haircut": "haircut"}
    fallback = TestLumaClient(test_aliases=aliases)
    luma_client = ScriptedLumaClient(
        {
            "book haircut": _create_appointment_response(
                slots={"service_id": "haircut"},
                missing_slots=["date", "time"],
            ),
            "still thinking": _create_appointment_response(
                slots={"service_id": "haircut"},
                missing_slots=["date", "time"],
            ),
            "tomorrow": _create_appointment_response(
                slots={"service_id": "haircut"},
                missing_slots=["time"],
                date=_APPOINTMENT_DATE,
            ),
            "2pm": _create_appointment_response(
                slots={"service_id": "haircut"},
                missing_slots=[],
                date=_APPOINTMENT_DATE,
                time=_APPOINTMENT_TIME,
            ),
        },
        fallback=fallback,
    )
    _ = TestCatalogClient(test_aliases=aliases, domain=domain)
    _ = get_customer_details()

    class SessionStoreWrapper:
        def __init__(self, user_id):
            self.user_id = user_id

        def get_session(self, user_id):
            return get_session(user_id)

        def save_session(self, user_id, session_state):
            save_session(user_id, session_state)

    session_store = SessionStoreWrapper(user_id)

    # TURN 1: service only
    result_turn1 = handle_message(
        text="book haircut",
        user_id=user_id,
        luma_client=luma_client,
        organization_client=None,
        session_store=session_store,
    )
    _assert_ready_missing_client(result_turn1, ["date", "time"])

    outcome_turn1 = result_turn1.get("outcome") or {}
    new_session_state_turn1 = build_session_state_from_outcome(
        outcome=outcome_turn1,
        outcome_status=outcome_turn1.get("status"),
        merged_luma_response=result_turn1.get("_merged_luma_response"),
        previous_session_state=get_session(user_id),
        user_id=user_id,
    )
    if new_session_state_turn1:
        save_session(user_id, new_session_state_turn1)

    # TURN 2: no new slots (scripted CREATE_APPOINTMENT — not live "still thinking" NLU)
    result_turn2 = handle_message(
        text="still thinking",
        user_id=user_id,
        luma_client=luma_client,
        organization_client=None,
        session_store=session_store,
    )
    _assert_ready_missing_client(result_turn2, ["date", "time"])

    outcome_turn2 = result_turn2.get("outcome") or {}
    new_session_state_turn2 = build_session_state_from_outcome(
        outcome=outcome_turn2,
        outcome_status=outcome_turn2.get("status"),
        merged_luma_response=result_turn2.get("_merged_luma_response"),
        previous_session_state=get_session(user_id),
        user_id=user_id,
    )
    if new_session_state_turn2:
        save_session(user_id, new_session_state_turn2)

    # TURN 3: date filled (canonical proposal), time still missing → still READY exploratory
    result_turn3 = handle_message(
        text="tomorrow",
        user_id=user_id,
        luma_client=luma_client,
        organization_client=None,
        session_store=session_store,
    )
    _assert_ready_missing_client(result_turn3, ["time"])

    outcome_turn3 = result_turn3.get("outcome") or {}
    new_session_state_turn3 = build_session_state_from_outcome(
        outcome=outcome_turn3,
        outcome_status=outcome_turn3.get("status"),
        merged_luma_response=result_turn3.get("_merged_luma_response"),
        previous_session_state=get_session(user_id),
        user_id=user_id,
    )
    if new_session_state_turn3:
        save_session(user_id, new_session_state_turn3)

    # Seed presented 14:00 offer so the time-selection turn can bind deterministically.
    _seed_presented_availability_for_time_bind(user_id)

    # TURN 4: time filled → complete booking path
    result_turn4 = handle_message(
        text="2pm",
        user_id=user_id,
        luma_client=luma_client,
        organization_client=None,
        session_store=session_store,
    )
    assert result_turn4.get("success") is True
    outcome_turn4 = result_turn4.get("outcome") or {}
    status_turn4 = outcome_turn4.get("status")
    assert status_turn4 in ("READY", "AWAITING_CONFIRMATION"), (
        f"Expected READY or AWAITING_CONFIRMATION after time filled, got {status_turn4}"
    )
    missing_turn4 = _missing_slots(outcome_turn4)
    assert missing_turn4 == [], (
        f"Expected empty missing_slots after time filled, got {missing_turn4}"
    )
