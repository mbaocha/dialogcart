"""
Execution-tier tests with mocked booking/availability clients.

Exercises handle_message through SEARCH_AVAILABILITY and CONFIRM_APPOINTMENT
with stateful mock booking endpoints (core.tests.mocks).

Requires Luma (same as planning tests). Does not require RUN_REAL_LUMA_E2E.

Usage:
  python core/tests/test.py --category execution
  pytest core/tests/execution/test_execution_mock_booking.py -v
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

src_path = Path(__file__).resolve().parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

os.environ.setdefault("CORE_EXECUTION_MODE", "test")

from core.tests.harness.scenario_loader import load_yaml_scenarios, scenario_param_id
from core.tests.harness.scenario_runner import run_multi_turn_scenario

_SCENARIOS_FILE = (
    Path(__file__).resolve().parent.parent / "scenarios" / "execution" / "mock_booking_flows.yaml"
)
_ALL_SCENARIOS = load_yaml_scenarios(_SCENARIOS_FILE)

if not _ALL_SCENARIOS:
    pytest.skip(
        f"No execution scenarios found at {_SCENARIOS_FILE}",
        allow_module_level=True,
    )

_FROZEN_TIME = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "scenario",
    _ALL_SCENARIOS,
    ids=[scenario_param_id(s, _ALL_SCENARIOS) for s in _ALL_SCENARIOS],
)
def test_execution_mock_booking_flow(scenario):
    """Run a mock-booking execution scenario end-to-end through handle_message."""
    run_multi_turn_scenario(
        scenario,
        frozen_time=_FROZEN_TIME,
        user_id_prefix="test_execution",
        inject_execution_clients=True,
        assert_missing_slots=True,
        assert_execution_calls=True,
    )
