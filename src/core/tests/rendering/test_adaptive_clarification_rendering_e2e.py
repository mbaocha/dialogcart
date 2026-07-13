"""
End-to-End Test: Adaptive Clarification Rendering

Under executable_with=[service_id], incomplete CREATE_APPOINTMENT turns are READY
with exploratory SEARCH_AVAILABILITY. When availability_client is omitted, assert
the planning/missing-client response shape (no clarification text).
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

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


def _assert_ready_missing_client(result: Dict[str, Any], expected_missing: list) -> None:
    assert result.get("success") is True
    outcome = result.get("outcome") or {}
    assert outcome.get("status") == "READY", (
        f"Expected READY for executable_with=[service_id], got {outcome.get('status')}"
    )
    missing = outcome.get("missing_slots")
    if missing is None:
        missing = (outcome.get("facts") or {}).get("missing_slots", [])
    assert set(missing) >= set(expected_missing), (
        f"Expected missing_slots to include {expected_missing}, got {missing}"
    )
    allowed = outcome.get("allowed_actions") or []
    assert "SEARCH_AVAILABILITY" in allowed
    assert "text" not in result, (
        f"Expected no clarification text on missing-client READY response, "
        f"got keys={list(result.keys())}"
    )


def _create_appointment_response(
    *,
    slots: Dict[str, Any],
    missing_slots: list,
) -> Dict[str, Any]:
    """Scripted durable CREATE_APPOINTMENT payload (no unconfirmed temporal slots)."""
    service_id = slots.get("service_id", "haircut")
    return {
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
        "facts": {"service_id": service_id},
        "slots": dict(slots),
        "missing_slots": list(missing_slots),
        "context": {},
    }


def test_adaptive_clarification_variant_after_retry():
    """
    service_id-only turns without availability_client stay READY (exploratory),
    with no clarification rendering text.

    Off-topic-looking utterances are scripted as CREATE_APPOINTMENT so this test
    validates booking retry continuation, not live NLU / HANDLER_DELEGATED.
    """
    user_id = "test_adaptive_clarification_001"
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

    with patch(
        "core.orchestration.orchestrator.render_llm",
        return_value="What date would you like for your haircut?",
    ):
        result1 = handle_message(
            text="book haircut",
            user_id=user_id,
            luma_client=luma_client,
            organization_client=None,
            session_store=session_store,
        )

    _assert_ready_missing_client(result1, ["date", "time"])

    outcome1 = result1.get("outcome") or {}
    merged1 = result1.get("_merged_luma_response")
    new_session1 = build_session_state_from_outcome(
        outcome=outcome1,
        outcome_status=outcome1.get("status"),
        merged_luma_response=merged1,
        previous_session_state=get_session(user_id),
        user_id=user_id,
    )
    if new_session1:
        save_session(user_id, new_session1)

    with patch(
        "core.orchestration.orchestrator.render_llm",
        return_value="Could you still tell me which date works?",
    ):
        result2 = handle_message(
            text="still thinking",
            user_id=user_id,
            luma_client=luma_client,
            organization_client=None,
            session_store=session_store,
        )

    _assert_ready_missing_client(result2, ["date", "time"])
