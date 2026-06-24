"""Load YAML scenario files for smoke and execution tests."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


def load_yaml_scenarios(
    scenarios_file: Path,
    *,
    env_index_var: str = "E2E_TEST_INDEX",
) -> List[Dict[str, Any]]:
    """
    Load scenarios from a YAML file with optional E2E_TEST_INDEX filtering.

    YAML shape: top-level key ``scenarios`` -> list of scenario dicts.
    """
    if not scenarios_file.exists():
        return []

    with open(scenarios_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    scenarios = data.get("scenarios", [])
    test_index_env = os.getenv(env_index_var)
    if test_index_env is None:
        return scenarios

    try:
        index_strings = [s.strip() for s in test_index_env.split(",")]
        selected_indices = []
        for index_str in index_strings:
            if not index_str:
                continue
            index = int(index_str)
            if 0 <= index < len(scenarios):
                selected_indices.append(index)
        if selected_indices:
            return [scenarios[i] for i in sorted(set(selected_indices))]
    except ValueError:
        pass

    return scenarios


def scenario_param_id(scenario: Dict[str, Any], all_scenarios: List[Dict[str, Any]]) -> str:
    """Pytest param id: index-name."""
    try:
        idx = all_scenarios.index(scenario)
        return f"{idx}-{scenario.get('name', 'unnamed')}"
    except ValueError:
        return scenario.get("name", "unnamed")
