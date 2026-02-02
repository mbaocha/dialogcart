"""
Pytest configuration and hooks for E2E tests.
"""
import os
import yaml
from pathlib import Path


def get_all_scenarios():
    """Load all scenarios (for failure summary)."""
    scenarios_file = Path(__file__).parent / "scenarios" / \
        "followup_availability_real_luma.yaml"
    if not scenarios_file.exists():
        return []
    
    with open(scenarios_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    return data.get("scenarios", [])


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print summary of failing tests with their indices."""
    if not os.getenv("RUN_REAL_LUMA_E2E"):
        return
    
    failed = terminalreporter.stats.get("failed", [])
    if not failed:
        return
    
    all_scenarios = get_all_scenarios()
    if not all_scenarios:
        return
    
    # Build scenario name to index mapping
    scenario_name_to_index = {}
    for idx, scenario in enumerate(all_scenarios):
        scenario_name = scenario.get("name", f"unnamed_{idx}")
        scenario_name_to_index[scenario_name] = idx
    
    print("\n" + "=" * 80)
    print("FAILING E2E TESTS SUMMARY")
    print("=" * 80)
    print(f"\nTotal failures: {len(failed)}")
    print("\nFailing tests (index and name):")
    print("-" * 80)
    
    failing_indices = []
    for test_report in failed:
        # Extract scenario name from test report
        test_name = test_report.nodeid
        # Test name format: test_core_e2e_followup_availability_real_luma.py::test_real_luma_followup_scenario[index-name]
        # Extract scenario identifier from parametrize
        scenario_id = None
        test_index = None
        
        if "[" in test_name and "]" in test_name:
            # Extract the scenario identifier (format: "index-name")
            scenario_id = test_name.split("[")[-1].split("]")[0]
            
            # Extract index directly from ID (format: "index-name")
            if "-" in scenario_id:
                try:
                    # Try to extract index from the beginning
                    index_str = scenario_id.split("-")[0]
                    test_index = int(index_str)
                    scenario_name = scenario_id.split("-", 1)[1]
                except (ValueError, IndexError):
                    # Fallback: try to find by name
                    scenario_name = scenario_id.split("-", 1)[1] if "-" in scenario_id else scenario_id
                    test_index = scenario_name_to_index.get(scenario_name)
            else:
                # No dash - try to find by name
                scenario_name = scenario_id
                test_index = scenario_name_to_index.get(scenario_name)
        
        if test_index is not None:
            failing_indices.append(test_index)
            scenario = all_scenarios[test_index]
            scenario_name_display = scenario.get("name", f"unnamed_{test_index}")
            print(f"  [{test_index}] {scenario_name_display}")
        else:
            # Fallback: print what we have
            print(f"  [???] {scenario_id or test_name}")
    
    if failing_indices:
        print("\n" + "-" * 80)
        print("To run a specific failing test, use:")
        for idx in sorted(failing_indices):
            scenario_name = all_scenarios[idx].get("name", f"unnamed_{idx}")
            # Use relative path for cleaner output
            test_file = "core/tests/e2e/test_core_e2e_followup_availability_real_luma.py"
            print(f"  E2E_TEST_INDEX={idx} RUN_REAL_LUMA_E2E=true pytest --tb=short {test_file}  # {scenario_name}")
        
        # Also show how to run multiple failing tests
        if len(failing_indices) > 1:
            indices_str = ",".join(str(idx) for idx in sorted(failing_indices))
            test_file = "core/tests/e2e/test_core_e2e_followup_availability_real_luma.py"
            print(f"\nTo run all {len(failing_indices)} failing tests:")
            print(f"  E2E_TEST_INDEX={indices_str} RUN_REAL_LUMA_E2E=true pytest --tb=short {test_file}")
        
        print("\nTip: Use --tb=short (or --tb=line) to reduce traceback verbosity")
        print("=" * 80 + "\n")

