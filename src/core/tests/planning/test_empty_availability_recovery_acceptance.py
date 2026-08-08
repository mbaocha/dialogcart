from copy import deepcopy

from core.planning.pipeline.orchestrator import run_planning_pipeline


class _YesLuma:
    def resolve(self, **_kwargs):
        return {
            "intent": {"name": "CONFIRM_ACTION", "confidence": 0.95},
            "response_act": "CONFIRM_ACTION",
            "facts": {"dates": [], "times": [], "date_time_pairs": []},
            "temporal": {"mode": "none", "start_date": None, "start_time": None},
            "turn": {"understanding": "UNDERSTOOD"},
        }


def _session(*, empty_recovery: bool):
    presented = {
        "search_date": "2026-07-02",
        "slots": [],
        "times": [],
        "recovery_actions": (
            [{"type": "choose_another_date"}] if empty_recovery else []
        ),
    }
    search_result = {
        "type": "availability",
        "status": "success",
        "search_date": "2026-07-02",
        "slots": [],
    }
    return {
        "intent_name": "CREATE_APPOINTMENT",
        "intent": "CREATE_APPOINTMENT",
        "status": "READY",
        "slots": {"service_id": "premium haircut", "date": "2026-07-02"},
        "date_proposal": {"mode": "single_day", "start": "2026-07-02"},
        "missing_slots": ["time"],
        "confirmation_state": None,
        "availability": {
            "cache": {"search_result": search_result},
            "presentation": {"presented": presented, "page_index": 0},
        },
    }


def _plan(session):
    result = run_planning_pipeline(
        user_id="recovery-test",
        text="yes",
        organization_id=1,
        derived_domain="service",
        session_state=deepcopy(session),
        luma_client=_YesLuma(),
    )
    return result["outcome"]


def test_yes_accepts_empty_availability_another_date_recovery_without_research():
    plan = _plan(_session(empty_recovery=True))

    assert plan["status"] == "NEEDS_CLARIFICATION"
    assert plan["action"] is None
    assert plan["ask_next"] == "date"
    assert "date" in plan["missing_slots"]
    assert "service_id" not in plan["missing_slots"]
    assert "date" not in plan["slots"]


def test_bare_yes_without_empty_availability_recovery_is_unchanged():
    plan = _plan(_session(empty_recovery=False))

    assert plan.get("_accepted_empty_availability_recovery") is not True
    assert plan["status"] == "READY"
    assert plan["action"] == "SEARCH_AVAILABILITY"
