"""
End-to-End Followup Conversation Test

Tests multi-turn conversations with shared user_id to verify conversational continuity.

This test exercises:
- Real handle_message entrypoint with shared user_id across turns
- Real environment-based clients (no mocks)
- Real renderer to generate user-facing output
- Context preservation across multiple turns
- Progressive slot filling
- All followup scenarios from core.tests.integration.followup_scenarios

Domain mapping:
- "service" = appointments (CREATE_APPOINTMENT)
- "reservation" = reservations (CREATE_RESERVATION)

Usage:
    python3 -m core.tests.integration.test_followup_e2e
    python3 -m core.tests.integration.test_followup_e2e 0  # Run scenario 0
    python3 -m core.tests.integration.test_followup_e2e 0 --verbose  # With details
"""

from core.rendering.whatsapp_renderer import render_outcome_to_whatsapp
from core.orchestration.clients.luma_client import LumaClient
from core.orchestration.clients.catalog_client import CatalogClient
from core.orchestration.orchestrator import handle_message
from core.execution.test_backend import TestExecutionBackend
from core.tests.integration.followup_scenarios import core_followup_scenarios
from core.tests.integration.test_appointment_e2e import (
    TestLumaClient,
    TestCatalogClient,
    _setup_test_org_domain,
    get_customer_details,
    _validate_rendered_response
)
from core.tests.integration.booking_scenarios import (
    STATUS_EXECUTED,
    STATUS_AWAITING_CONFIRMATION,
    STATUS_NEEDS_CLARIFICATION
)
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

# Set execution mode to test for deterministic E2E tests
os.environ["CORE_EXECUTION_MODE"] = "test"

# Add src/ to Python path so we can import core modules
src_path = Path(__file__).resolve().parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Load environment variables (reuse logic from test_appointment_e2e.py)
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
    # Fallback: manually parse .env files
    def load_env_file(env_path: Path, override: bool = False):
        """Manually parse and load .env file."""
        if not env_path.exists():
            return
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        if value.startswith('"') and value.endswith('"'):
                            value = value[1:-1]
                        elif value.startswith("'") and value.endswith("'"):
                            value = value[1:-1]
                        if override or key not in os.environ:
                            os.environ[key] = value
        except Exception as e:
            print(f"Error loading {env_path}: {e}")

    project_root = Path(__file__).parent.parent.parent.parent
    core_env_file = Path(__file__).parent.parent.parent / ".env"
    env_file = project_root / ".env"
    env_local_file = project_root / ".env.local"

    if env_file.exists():
        load_env_file(env_file, override=False)
    if core_env_file.exists():
        load_env_file(core_env_file, override=True)
    if env_local_file.exists():
        load_env_file(env_local_file, override=True)
except Exception as e:
    print(f"Error loading .env files: {e}")


def test_followup_scenario_e2e(
    scenario: Dict[str, Any],
    scenario_index: int,
    customer_details: Dict[str, Optional[Any]],
    verbose: bool = False
) -> Tuple[bool, Optional[str]]:
    """
    Test a multi-turn followup scenario end-to-end.

    Args:
        scenario: Scenario dict with name, domain, aliases, turns
        scenario_index: Index of scenario in core_followup_scenarios list
        customer_details: Customer details dict
        verbose: Whether to print detailed output

    Returns:
        Tuple of (success: bool, error_message: Optional[str])
    """
    scenario_name = scenario.get("name", f"scenario_{scenario_index}")
    aliases = scenario.get("aliases", {})
    domain = scenario.get("domain", "service")
    turns = scenario.get("turns", [])

    if not turns:
        return False, "Scenario has no turns"

    # Create unique user_id for this scenario (shared across all turns)
    user_id = f"test_followup_{scenario_index:03d}"

    # Create LumaClient with scenario-specific aliases
    luma_client = TestLumaClient(test_aliases=aliases)

    # Create CatalogClient that returns test aliases as catalog data
    catalog_client = TestCatalogClient(test_aliases=aliases, domain=domain)

    # Set up org domain cache to use the scenario's domain
    _setup_test_org_domain(domain)

    # Clear catalog cache to ensure fresh data from TestCatalogClient
    from core.orchestration.cache.catalog_cache import catalog_cache
    test_org_id = int(os.getenv("ORG_ID", "1"))
    catalog_cache._mem_cache.pop((test_org_id, domain), None)

    if verbose:
        print(f"\n{'='*60}")
        print(f"Followup Scenario {scenario_index}: {scenario_name}")
        print(f"{'='*60}")
        print(f"Domain: {domain}, Turns: {len(turns)}")

    try:
        # Execute each turn in sequence with the same user_id
        for turn_index, turn in enumerate(turns):
            sentence = turn.get("sentence", "")
            expected = turn.get("expected", {})
            expected_intent = expected.get("intent")
            expected_status = expected.get("status")
            expected_reason = expected.get("clarification_reason")

            if verbose:
                print(f"\n--- Turn {turn_index + 1}/{len(turns)}: {sentence} ---")
                print(f"Expected: intent={expected_intent}, status={expected_status}")
                if expected_reason:
                    print(f"Expected clarification_reason: {expected_reason}")

            # Call handle_message with the same user_id (preserves context)
            result = handle_message(
                user_id=user_id,
                text=sentence,
                domain=domain,
                timezone="UTC",
                phone_number=customer_details['phone_number'],
                email=customer_details['email'],
                customer_id=customer_details['customer_id'],
                luma_client=luma_client,
                catalog_client=catalog_client,
                verbose=verbose
            )

            if verbose:
                print(f"\nResult:")
                print(json.dumps(result, indent=2, default=str))

            # Verify success
            if not result.get("success"):
                error_msg = result.get("error", "Unknown error")
                return False, f"Turn {turn_index + 1} failed: handle_message returned success=false: {error_msg}"

            outcome = result.get("outcome", {})
            actual_status = outcome.get("status")

            # Extract intent from multiple possible sources
            actual_intent = (
                outcome.get("intent_name") or
                outcome.get("intent", {}).get("name") or
                None
            )

            # If intent not in outcome, try to get from Luma response
            if not actual_intent and hasattr(luma_client, 'last_response') and luma_client.last_response:
                luma_response = luma_client.last_response
                if isinstance(luma_response, dict):
                    intent_obj = luma_response.get("intent", {})
                    if isinstance(intent_obj, dict):
                        actual_intent = intent_obj.get("name")

            # Verify intent matches expected
            if expected_intent:
                if actual_intent:
                    if actual_intent != expected_intent:
                        return False, f"Turn {turn_index + 1} intent mismatch: expected {expected_intent}, got {actual_intent}"
                elif expected_status != STATUS_NEEDS_CLARIFICATION:
                    return False, f"Turn {turn_index + 1} intent not found in outcome (expected {expected_intent})"

            # Verify status matches expected (with flexible handling for EXECUTED/AWAITING_CONFIRMATION)
            if expected_status:
                if expected_status == STATUS_EXECUTED and actual_status == "AWAITING_CONFIRMATION":
                    # If we expected EXECUTED but got AWAITING_CONFIRMATION, check if there's a next turn
                    is_last_turn = (turn_index + 1) >= len(turns)
                    if is_last_turn:
                        # Last turn - auto-confirm to get to EXECUTED
                        if verbose:
                            print(f"Status is AWAITING_CONFIRMATION (expected EXECUTED), auto-confirming...")
                        
                        # Validate rendered response before confirming
                        success, error_msg = _validate_rendered_response(outcome, expected, verbose)
                        if not success:
                            return False, f"Turn {turn_index + 1} {error_msg}"
                        
                        # Send confirmation
                        confirm_result = handle_message(
                            user_id=user_id,
                            text="yes, confirm it",
                            domain=domain,
                            timezone="UTC",
                            phone_number=customer_details['phone_number'],
                            email=customer_details['email'],
                            customer_id=customer_details['customer_id'],
                            luma_client=luma_client,
                            catalog_client=catalog_client
                        )
                        
                        if not confirm_result.get("success"):
                            return False, f"Turn {turn_index + 1} confirmation failed: {confirm_result.get('error', 'Unknown error')}"
                        
                        confirm_outcome = confirm_result.get("outcome", {})
                        confirm_status = confirm_outcome.get("status")
                        
                        if confirm_status != "EXECUTED":
                            # Try repeating the original sentence
                            confirm_result = handle_message(
                                user_id=user_id,
                                text=sentence,
                                domain=domain,
                                timezone="UTC",
                                phone_number=customer_details['phone_number'],
                                email=customer_details['email'],
                                customer_id=customer_details['customer_id'],
                                luma_client=luma_client,
                                catalog_client=catalog_client
                            )
                            confirm_outcome = confirm_result.get("outcome", {})
                            confirm_status = confirm_outcome.get("status")
                        
                        if confirm_status != "EXECUTED":
                            return False, f"Turn {turn_index + 1} after confirmation, expected EXECUTED, got {confirm_status}"
                        
                        # Validate rendered response for executed outcome
                        success, error_msg = _validate_rendered_response(confirm_outcome, expected, verbose)
                        if not success:
                            return False, f"Turn {turn_index + 1} (after confirmation) {error_msg}"
                        
                        if verbose:
                            print(f"✓ Confirmed and executed successfully")
                    else:
                        # Not last turn - accept AWAITING_CONFIRMATION (next turn will handle confirmation)
                        if verbose:
                            print(f"Status is AWAITING_CONFIRMATION (expected EXECUTED), next turn will confirm")
                        # Validate rendered response for AWAITING_CONFIRMATION
                        success, error_msg = _validate_rendered_response(outcome, expected, verbose)
                        if not success:
                            return False, f"Turn {turn_index + 1} {error_msg}"
                elif expected_status == STATUS_AWAITING_CONFIRMATION:
                    # Expected AWAITING_CONFIRMATION - accept it
                    if actual_status != "AWAITING_CONFIRMATION":
                        return False, f"Turn {turn_index + 1} status mismatch: expected {expected_status}, got {actual_status}"
                elif actual_status != expected_status:
                    # Strict match for other statuses
                    return False, f"Turn {turn_index + 1} status mismatch: expected {expected_status}, got {actual_status}"

            # Verify clarification reason if specified
            if expected_reason:
                actual_reason = outcome.get("clarification_reason")
                if actual_reason != expected_reason:
                    return False, f"Turn {turn_index + 1} clarification_reason mismatch: expected {expected_reason}, got {actual_reason}"

            # Validate rendered response for all outcomes (unless we already validated after confirmation)
            if not (expected_status == STATUS_EXECUTED and actual_status == "AWAITING_CONFIRMATION" and (turn_index + 1) >= len(turns)):
                success, error_msg = _validate_rendered_response(outcome, expected, verbose)
                if not success:
                    return False, f"Turn {turn_index + 1} {error_msg}"

            if verbose:
                print(f"✓ Turn {turn_index + 1} passed")

        # All turns passed
        if verbose:
            print(f"\n✓ All {len(turns)} turns passed for scenario: {scenario_name}")
        return True, None

    except Exception as e:
        import traceback
        error_msg = f"Exception in followup scenario: {str(e)}\n{traceback.format_exc()}"
        if verbose:
            print(f"❌ Exception: {error_msg}")
        return False, error_msg


def run_all_followup_scenarios(
    scenarios: List[Dict[str, Any]],
    customer_details: Dict[str, Optional[Any]],
    verbose: bool = False,
    scenario_indices: Optional[List[int]] = None
) -> Tuple[int, int, int, List[Tuple[int, str]]]:
    """
    Run all followup scenarios and return statistics.

    Args:
        scenarios: List of scenario dicts
        customer_details: Customer details dict
        verbose: Whether to print detailed output
        scenario_indices: Optional list of specific scenario indices to run

    Returns:
        Tuple of (passed, failed, skipped, failures_list)
    """
    passed = 0
    failed = 0
    skipped = 0
    failures = []

    # Reset test backend counter for clean state
    TestExecutionBackend.reset_counter()

    if scenario_indices is not None:
        indices_to_run = scenario_indices
        for original_idx in indices_to_run:
            if original_idx >= len(core_followup_scenarios):
                skipped += 1
                continue

            scenario = core_followup_scenarios[original_idx]
            success, error_msg = test_followup_scenario_e2e(
                scenario, original_idx, customer_details, verbose)

            if success:
                passed += 1
                if verbose:
                    print(f"✓ Scenario {original_idx}: {scenario.get('name', '')[:50]}...")
            else:
                failed += 1
                failures.append((original_idx, error_msg or "Unknown error"))
                print(f"✗ Scenario {original_idx}: {scenario.get('name', '')[:50]}...")
                if error_msg:
                    print(f"  Error: {error_msg}")
    else:
        for list_idx in range(len(scenarios)):
            scenario = scenarios[list_idx]
            success, error_msg = test_followup_scenario_e2e(
                scenario, list_idx, customer_details, verbose)

            if success:
                passed += 1
                if verbose:
                    print(f"✓ Scenario {list_idx}: {scenario.get('name', '')[:50]}...")
            else:
                failed += 1
                failures.append((list_idx, error_msg or "Unknown error"))
                print(f"✗ Scenario {list_idx}: {scenario.get('name', '')[:50]}...")
                if error_msg:
                    print(f"  Error: {error_msg}")

    return passed, failed, skipped, failures


def main():
    """Main test runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run E2E tests for followup conversation scenarios",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m core.tests.integration.test_followup_e2e
  python -m core.tests.integration.test_followup_e2e 0
  python -m core.tests.integration.test_followup_e2e 0 --verbose
  python -m core.tests.integration.test_followup_e2e --range 0-5
        """
    )
    parser.add_argument("scenario", type=int, nargs="?",
                        help="Run specific scenario by index (positional argument)")
    parser.add_argument("--scenario", type=int, dest="scenario_flag",
                        help="Run specific scenario by index (alternative to positional)")
    parser.add_argument("--range", type=str,
                        help="Run scenarios in range (e.g., '0-5')")
    parser.add_argument("--verbose", "-v",
                        action="store_true", help="Verbose output")
    args = parser.parse_args()

    scenario_num = args.scenario if args.scenario is not None else args.scenario_flag

    customer_details = get_customer_details()
    if not customer_details['customer_id'] and not customer_details['phone_number'] and not customer_details['email']:
        if args.verbose:
            print("WARNING: No customer_id, phone, or email found in environment")
            print("Test may fail if customer creation is required")

    # Filter scenarios if needed
    scenarios_to_run = core_followup_scenarios
    scenario_indices = None

    if scenario_num is not None:
        scenario_indices = [scenario_num]
        scenarios_to_run = [core_followup_scenarios[scenario_num]
                            ] if scenario_num < len(core_followup_scenarios) else []
        if args.verbose:
            print(f"Running single scenario: {scenario_num}")
    elif args.range:
        start, end = map(int, args.range.split('-'))
        scenario_indices = list(range(start, end + 1))
        scenarios_to_run = [core_followup_scenarios[i]
                            for i in scenario_indices if i < len(core_followup_scenarios)]
        if args.verbose:
            print(f"Running scenarios: {args.range}")

    # Print header
    if not args.verbose:
        scenarios_count = len(scenarios_to_run)
        print(
            f"Running E2E followup scenarios test ({scenarios_count} scenario{'s' if scenarios_count != 1 else ''})...")
    else:
        print("="*70)
        print("E2E FOLLOWUP SCENARIOS TEST")
        print("="*70)
        print(f"Total scenarios: {len(core_followup_scenarios)}")
        if len(scenarios_to_run) != len(core_followup_scenarios):
            print(
                f"Running: {len(scenarios_to_run)} scenario{'s' if len(scenarios_to_run) != 1 else ''}")

    if not scenarios_to_run:
        print("No scenarios to run!")
        sys.exit(1)

    # Run scenarios
    passed, failed, skipped, failures = run_all_followup_scenarios(
        scenarios_to_run,
        customer_details,
        verbose=args.verbose,
        scenario_indices=scenario_indices
    )

    # Print summary
    if args.verbose:
        print("\n" + "="*70)
        print("TEST SUMMARY")
        print("="*70)
    else:
        print()

    print(
        f"Total: {len(scenarios_to_run)} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")

    if failures:
        print("\nFailures:")
        for idx, error_msg in failures:
            scenario = core_followup_scenarios[idx] if idx < len(
                core_followup_scenarios) else {}
            print(f"  Scenario {idx}: {scenario.get('name', 'N/A')}")
            print(f"    Error: {error_msg}")

    if args.verbose:
        print("="*70)

    if failed > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()

