"""Parameterized runner for all declarative booking conversation scenarios."""

from __future__ import annotations

import pytest

from core.tests.e2e.framework.conversation import ORG_ID, Expect, Scenario, Turn, coerce_turn
from core.tests.e2e.framework.fixtures import (
    SCRIPTED_FIXTURE_PARAMS,
    build_scripted_bundle,
    live_luma,
)
from core.tests.e2e.framework.runner import run_bundle
from core.tests.e2e.scenarios.booking import SCENARIOS
from core.session.session_manager import clear_session


def test_conversation_dsl_expect_aliases():
    checks = Expect(
        planner="READY",
        confirmation=None,
        execution="availability",
        time_match="EXACT",
    ).to_assert_turn_kwargs()
    assert checks["planner_status"] == "READY"
    assert checks["confirmation"] is None
    assert checks["execution_type"] == "availability"
    assert checks["time_match_outcome"] == "TIME_MATCH_EXACT"


def test_conversation_dsl_coerces_turn_shorthand():
    scenario = Scenario(
        "Demo",
        Turn("hi", Expect(planner="READY")),
        ("premium", Expect(action="SEARCH_AVAILABILITY")),
    )
    assert len(scenario.turns) == 2
    assert coerce_turn(("x", {"planner": "READY"})).expect.planner == "READY"


def _booking_scenario_params():
    params = []
    for scenario in SCENARIOS:
        marks = [live_luma] if scenario.fixture == "booking" else []
        params.append(
            pytest.param(scenario, id=scenario.pytest_id(), marks=marks)
        )
    return params


@pytest.mark.parametrize("scenario", _booking_scenario_params())
def test_booking_scenario(scenario, api_client, monkeypatch, request):
    if scenario.fixture == "booking":
        bundle = request.getfixturevalue("booking_conversation")
        run_bundle(scenario, bundle)
        return

    params = dict(SCRIPTED_FIXTURE_PARAMS.get(scenario.fixture) or {})
    conv, booking, availability, user_id = build_scripted_bundle(
        api_client, monkeypatch, **params
    )
    try:
        run_bundle(scenario, (conv, booking, availability))
    finally:
        clear_session(ORG_ID, user_id)
