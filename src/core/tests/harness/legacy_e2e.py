"""Helpers for thin legacy E2E wrapper modules under core/tests/e2e/."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from core.tests.harness.scenario_loader import load_yaml_scenarios
from core.tests.harness.scenario_runner import run_multi_turn_scenario

FROZEN_TIME = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def load_e2e_scenarios(scenarios_dir: Path, yaml_name: str) -> List[Dict[str, Any]]:
    """Load scenarios from e2e/scenarios/ with standard skip if missing."""
    path = scenarios_dir / yaml_name
    scenarios = load_yaml_scenarios(path)
    if not scenarios:
        pytest.skip(f"Scenarios file not found or empty: {path}", allow_module_level=True)
    return scenarios


def run_legacy_e2e_scenario(
    scenario: Dict[str, Any],
    *,
    user_id_prefix: str,
    inject_execution_clients: bool = True,
    assert_missing_slots: bool = False,
    assert_execution_calls: bool = False,
) -> None:
    """Run one YAML scenario through the shared multi-turn runner."""
    run_multi_turn_scenario(
        scenario,
        frozen_time=FROZEN_TIME,
        user_id_prefix=user_id_prefix,
        inject_execution_clients=inject_execution_clients,
        assert_missing_slots=assert_missing_slots,
        assert_execution_calls=assert_execution_calls,
    )
