"""Pytest hooks for smoke test failure summaries."""

import os
from pathlib import Path

import yaml


def get_all_scenarios():
    scenarios_file = (
        Path(__file__).parent.parent
        / "scenarios"
        / "smoke"
        / "followup_availability_real_luma.yaml"
    )
    if not scenarios_file.exists():
        return []

    with open(scenarios_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return data.get("scenarios", [])


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not os.getenv("RUN_REAL_LUMA_E2E"):
        return

    failed = terminalreporter.stats.get("failed", [])
    if not failed:
        return

    all_scenarios = get_all_scenarios()
    if not all_scenarios:
        return

    scenario_name_to_index = {}
    for idx, scenario in enumerate(all_scenarios):
        scenario_name = scenario.get("name", f"unnamed_{idx}")
        scenario_name_to_index[scenario_name] = idx

    print("\n" + "=" * 80)
    print("FAILING SMOKE TESTS SUMMARY")
    print("=" * 80)
    print(f"\nTotal failures: {len(failed)}")
    print("\nFailing tests (index and name):")
    print("-" * 80)

    failing_indices = []
    for test_report in failed:
        test_name = test_report.nodeid
        scenario_id = None
        test_index = None

        if "[" in test_name and "]" in test_name:
            scenario_id = test_name.split("[")[-1].split("]")[0]
            if "-" in scenario_id:
                try:
                    index_str = scenario_id.rsplit("-", 1)[0]
                    if "/" in index_str:
                        index_str = index_str.split("/")[-1]
                    test_index = int(index_str)
                except (ValueError, IndexError):
                    test_index = scenario_name_to_index.get(
                        scenario_id.split("-", 1)[-1]
                    )

        if test_index is not None and test_index < len(all_scenarios):
            failing_indices.append(test_index)
            scenario = all_scenarios[test_index]
            print(f"  [{test_index}] {scenario.get('name', 'unnamed')}")

    if failing_indices:
        print("\n" + "-" * 80)
        print("To re-run failing availability scenarios:")
        for idx in sorted(set(failing_indices)):
            print(
                f"  E2E_TEST_INDEX={idx} RUN_REAL_LUMA_E2E=true "
                f"pytest core/tests/smoke/test_smoke_scenarios.py -k availability"
            )
        print("=" * 80 + "\n")
