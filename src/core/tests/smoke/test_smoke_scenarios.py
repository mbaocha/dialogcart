"""
Unified smoke test runner for YAML scenarios under core/tests/scenarios/smoke/.

Gated by RUN_REAL_LUMA_E2E=true.

Usage:
  RUN_REAL_LUMA_E2E=true python core/tests/test.py --category smoke
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

src_path = Path(__file__).resolve().parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

if not os.getenv("RUN_REAL_LUMA_E2E"):
    pytest.skip(
        "Smoke tests disabled. Set RUN_REAL_LUMA_E2E=true to enable.",
        allow_module_level=True,
    )

from core.tests.harness.legacy_e2e import run_legacy_e2e_scenario
from core.tests.harness.scenario_loader import load_yaml_scenarios, scenario_param_id

_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios" / "smoke"

# prefix, yaml filename, inject clients, assert execution mock calls
SMOKE_SCENARIO_FILES: List[Tuple[str, str, bool, bool]] = [
    ("confirm-appt", "followup_confirm_appointment_real_luma.yaml", True, True),
    ("confirm-cancel", "followup_confirm_cancellation_real_luma.yaml", True, True),
    ("confirm-reservation", "followup_confirm_reservation_real_luma.yaml", True, True),
    ("apply-modification", "followup_apply_modification_real_luma.yaml", True, True),
    ("availability", "followup_availability_real_luma.yaml", True, False),
    ("cross-intent", "cross_intent_messy_real_world_flow.yaml", True, False),
]


def _build_smoke_params() -> List[Tuple[str, Dict[str, Any], bool, bool]]:
    params: List[Tuple[str, Dict[str, Any], bool, bool]] = []
    for prefix, filename, inject_clients, assert_exec in SMOKE_SCENARIO_FILES:
        path = _SCENARIOS_DIR / filename
        scenarios = load_yaml_scenarios(path)
        for scenario in scenarios:
            sid = scenario_param_id(scenario, scenarios)
            params.append((f"{prefix}/{sid}", scenario, inject_clients, assert_exec))
    return params


_SMOKE_PARAMS = _build_smoke_params()

if not _SMOKE_PARAMS:
    pytest.skip(
        f"No smoke scenarios found under {_SCENARIOS_DIR}",
        allow_module_level=True,
    )


@pytest.mark.parametrize(
    "scenario_id,scenario,inject_clients,assert_execution_calls",
    _SMOKE_PARAMS,
    ids=[p[0] for p in _SMOKE_PARAMS],
)
def test_smoke_scenario(scenario_id, scenario, inject_clients, assert_execution_calls):
    run_legacy_e2e_scenario(
        scenario,
        user_id_prefix=f"smoke_{scenario_id.replace('/', '_')}",
        inject_execution_clients=inject_clients,
        assert_execution_calls=assert_execution_calls,
    )
