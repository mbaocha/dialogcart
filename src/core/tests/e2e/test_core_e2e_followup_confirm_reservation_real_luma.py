"""
Legacy wrapper: reservation confirmation with real Luma + mock booking.

Prefer: RUN_REAL_LUMA_E2E=true python core/tests/test.py --category smoke
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

if not os.getenv("RUN_REAL_LUMA_E2E"):
    pytest.skip(
        "Real Luma E2E tests disabled. Set RUN_REAL_LUMA_E2E=true to enable.",
        allow_module_level=True,
    )

from core.tests.harness.legacy_e2e import load_e2e_scenarios, run_legacy_e2e_scenario
from core.tests.harness.scenario_loader import scenario_param_id

_SCENARIOS_DIR = Path(__file__).parent / "scenarios"
_SCENARIOS = load_e2e_scenarios(
    _SCENARIOS_DIR, "followup_confirm_reservation_real_luma.yaml"
)


@pytest.mark.parametrize(
    "scenario",
    _SCENARIOS,
    ids=[scenario_param_id(s, _SCENARIOS) for s in _SCENARIOS],
)
def test_followup_confirm_reservation_real_luma(scenario: Dict[str, Any]):
    run_legacy_e2e_scenario(
        scenario,
        user_id_prefix="test_confirm_reservation",
        assert_execution_calls=True,
    )
