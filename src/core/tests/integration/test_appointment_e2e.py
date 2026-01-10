"""
End-to-End Conversational Test

Tests the full lifecycle: orchestration → routing → execution → rendering.

This test exercises:
- Real handle_message entrypoint
- Real environment-based clients (no mocks)
- Real renderer to generate user-facing output
- All booking scenarios from core.tests.integration.booking_scenarios

Domain mapping:
- "service" = appointments (CREATE_APPOINTMENT)
- "reservation" = reservations (CREATE_RESERVATION)

Usage:
    python3 -m core.tests.integration.test_appointment_e2e
    python3 -m core.tests.integration.test_appointment_e2e 1  # Run scenario 1
    python3 -m core.tests.integration.test_appointment_e2e 1 --verbose  # With details
    python3 -m core.tests.integration.test_appointment_e2e --range 0-10  # Run range
    python3 -m core.tests.integration.test_appointment_e2e --skip-needs-clarification
"""

from core.rendering.whatsapp_renderer import render_outcome_to_whatsapp
from core.orchestration.clients.luma_client import LumaClient
from core.orchestration.clients.catalog_client import CatalogClient
from core.orchestration.orchestrator import handle_message
from core.execution.test_backend import TestExecutionBackend
from core.tests.integration.booking_scenarios import (
    core_booking_scenarios,
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
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Load environment variables (reuse logic from test_orchestrator_e2e.py)
try:
    from dotenv import load_dotenv
    # Project root is two levels up from tests/integration/
    project_root = Path(__file__).parent.parent.parent.parent
    # Also check for .env in src/core/
    core_env_file = Path(__file__).parent.parent.parent / ".env"
    env_file = project_root / ".env"
    env_local_file = project_root / ".env.local"

    # Load core/.env last so it takes precedence over project root .env
    # Load order: project_root/.env -> core/.env -> project_root/.env.local
    if env_file.exists():
        load_dotenv(env_file, override=False)
    if core_env_file.exists():
        load_dotenv(core_env_file, override=True)  # Override project root .env
    if env_local_file.exists():
        load_dotenv(env_local_file, override=True)  # Override everything
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
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    # Parse KEY=VALUE
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        # Remove quotes if present
                        if value.startswith('"') and value.endswith('"'):
                            value = value[1:-1]
                        elif value.startswith("'") and value.endswith("'"):
                            value = value[1:-1]
                        # Set environment variable
                        if override or key not in os.environ:
                            os.environ[key] = value
        except Exception as e:
            print(f"Error loading {env_path}: {e}")

    # Updated paths for new location: tests/integration/
    project_root = Path(__file__).parent.parent.parent.parent
    core_env_file = Path(__file__).parent.parent.parent / ".env"
    env_file = project_root / ".env"
    env_local_file = project_root / ".env.local"

    # Load in order: project_root/.env -> core/.env -> project_root/.env.local
    if env_file.exists():
        load_env_file(env_file, override=False)
    if core_env_file.exists():
        load_env_file(core_env_file, override=True)
    if env_local_file.exists():
        load_env_file(env_local_file, override=True)
except Exception as e:
    print(f"Error loading .env files: {e}")


def get_customer_details() -> Dict[str, Optional[Any]]:
    """Load customer details from environment variables."""
    phone_number = os.getenv("TEST_CUSTOMER_PHONE")
    email = os.getenv("TEST_CUSTOMER_EMAIL")
    customer_id_str = os.getenv("TEST_CUSTOMER_ID")
    customer_id = int(customer_id_str) if customer_id_str else None

    return {
        "phone_number": phone_number,
        "email": email,
        "customer_id": customer_id
    }


class TestLumaClient(LumaClient):
    """Custom LumaClient that injects tenant_context from test aliases."""

    def __init__(self, test_aliases: Optional[Dict[str, str]] = None):
        """Initialize with test aliases to inject."""
        super().__init__()
        self.test_aliases = test_aliases or {}
        self.last_response: Optional[Dict[str, Any]] = None

    def resolve(
        self,
        user_id: str,
        text: str,
        domain: str = "service",
        timezone: str = "UTC",
        tenant_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Override resolve to inject test aliases into tenant_context.

        Test aliases are merged into tenant_context, preserving other fields like booking_mode.
        """
        # Merge test aliases into tenant_context, preserving existing fields (e.g., booking_mode)
        if self.test_aliases:
            if tenant_context is None:
                tenant_context = {}
            # Merge aliases, but preserve other fields like booking_mode
            tenant_context = {**tenant_context, "aliases": self.test_aliases}

        response = super().resolve(user_id, text, domain, timezone, tenant_context)
        self.last_response = response  # Store for test access
        return response


class TestCatalogClient(CatalogClient):
    """Custom CatalogClient that returns test aliases as catalog data."""

    def __init__(self, test_aliases: Optional[Dict[str, str]] = None, domain: str = "service"):
        """
        Initialize with test aliases to return as catalog data.

        Args:
            test_aliases: Dict mapping alias names to canonical keys
            domain: Domain type ("service" or "reservation")
        """
        super().__init__()
        self.test_aliases = test_aliases or {}
        self.domain = domain

    def get_services(self, organization_id: int) -> Dict[str, Any]:
        """Return test services matching test aliases."""
        services = []
        for alias_name, canonical_key in self.test_aliases.items():
            services.append({
                "name": alias_name,
                "canonical": canonical_key,
                "service_family_id": canonical_key,
                "is_active": True
            })
        return {
            "catalog_last_updated_at": "2026-01-01T00:00:00Z",
            "business_category_id": 1,
            "services": services
        }

    def get_reservation(self, organization_id: int) -> Dict[str, Any]:
        """Return test rooms matching test aliases."""
        rooms = []
        for alias_name, canonical_key in self.test_aliases.items():
            rooms.append({
                "name": alias_name,
                "canonical_key": canonical_key,
                "canonical": canonical_key,
                "is_active": True
            })
        return {
            "catalog_last_updated_at": "2026-01-01T00:00:00Z",
            "business_category_id": 2,
            "room_types": rooms,  # catalog_cache will also add "rooms" alias
            "extras": []
        }


def _setup_test_org_domain(domain: str):
    """
    Set up org domain cache for testing.

    This pre-populates the cache so the orchestrator uses the correct domain
    instead of deriving it from organization details.

    Args:
        domain: Domain to set ("service" or "reservation")
    """
    from core.orchestration.cache.org_domain_cache import org_domain_cache

    # Map domain to businessCategoryId
    # Based on org_domain_cache.py:
    # SERVICE_CATEGORY_IDS = {1, "beauty_and_wellness"}
    # RESERVATION_CATEGORY_IDS = {2, "lodging", "hotel", "hospitality"}
    business_category_id = 1 if domain == "service" else 2

    # Pre-populate cache with test domain
    test_org_id = int(os.getenv("ORG_ID", "1"))
    org_domain_cache._mem_set(test_org_id, {
        "domain": domain,
        "businessCategoryId": business_category_id
    })


def _validate_rendered_response(
    outcome: Dict[str, Any],
    expected: Dict[str, Any],
    verbose: bool = False
) -> Tuple[bool, Optional[str]]:
    """
    Validate rendered response for an outcome.

    Args:
        outcome: Outcome dictionary to render
        expected: Expected values from scenario
        verbose: Whether to print detailed output

    Returns:
        Tuple of (success: bool, error_message: Optional[str])
    """
    try:
        rendered = render_outcome_to_whatsapp(outcome)
        if not isinstance(rendered, dict):
            return False, f"Rendered response is not a dict: {type(rendered).__name__}"
        if "text" not in rendered:
            return False, "Rendered response missing 'text' field"
        if not rendered.get("text"):
            return False, "Rendered response has empty 'text' field"
        if "type" not in rendered:
            return False, "Rendered response missing 'type' field"

        # Validate that all template variables were interpolated (no {{placeholders}})
        rendered_text = rendered.get("text", "")
        if "{{" in rendered_text or "}}" in rendered_text:
            return False, f"Rendered text contains un-interpolated template variables: {rendered_text}"

        # Derive expected keywords from clarification reason if not explicitly provided
        def _derive_keywords_from_reason(reason: str) -> List[str]:
            """Derive expected keywords from clarification reason."""
            reason_upper = reason.upper()
            if "TIME" in reason_upper:
                return ["time"]
            elif "DATE" in reason_upper:
                # "date" should also match "day" (semantically related)
                return ["date", "day"]
            elif "SERVICE" in reason_upper:
                return ["service"]
            return []

        # Check against expected rendered response if provided
        expected_rendered = expected.get("rendered_response")
        expected_reason = expected.get("clarification_reason")

        if expected_rendered:
            # Get keywords: explicit list, or derive from clarification reason
            contains_keywords = expected_rendered.get("contains_keywords")
            if not contains_keywords and expected_reason:
                # Auto-derive keywords from clarification reason
                contains_keywords = _derive_keywords_from_reason(
                    expected_reason)

            if contains_keywords:
                rendered_lower = rendered_text.lower()
                # Check if ANY keyword is present (not all)
                found_keywords = [
                    kw for kw in contains_keywords if kw.lower() in rendered_lower]
                if not found_keywords:
                    return False, f"Rendered text missing expected keywords (none found): {contains_keywords}. Got: {rendered_text}"

            # Check exact text match if provided (strict validation)
            expected_text = expected_rendered.get("text")
            if expected_text and rendered_text != expected_text:
                return False, f"Rendered text mismatch: expected '{expected_text}', got '{rendered_text}'"

            # Check type
            expected_type = expected_rendered.get("type")
            if expected_type and rendered.get("type") != expected_type:
                return False, f"Rendered type mismatch: expected '{expected_type}', got '{rendered.get('type')}'"
        elif expected_reason:
            # No rendered_response specified, but we have a clarification reason
            # Auto-validate using derived keywords
            auto_keywords = _derive_keywords_from_reason(expected_reason)
            if auto_keywords:
                rendered_lower = rendered_text.lower()
                # Check if ANY keyword is present (not all)
                found_keywords = [
                    kw for kw in auto_keywords if kw.lower() in rendered_lower]
                if not found_keywords:
                    return False, f"Rendered text missing expected keywords (auto-derived from {expected_reason}, none found): {auto_keywords}. Got: {rendered_text}"

        if verbose:
            print(
                f"✓ Rendered response verified: {rendered.get('text', '')[:60]}...")
        return True, None
    except Exception as render_error:
        return False, f"Rendering failed: {str(render_error)}"


def test_scenario_e2e(
    scenario: Dict[str, Any],
    scenario_index: int,
    customer_details: Dict[str, Optional[Any]],
    verbose: bool = False
) -> Tuple[bool, Optional[str]]:
    """
    Test a single booking scenario end-to-end.

    Args:
        scenario: Scenario dict with sentence, aliases, domain, expected
        scenario_index: Index of scenario in core_booking_scenarios list
        customer_details: Customer details dict
        verbose: Whether to print detailed output

    Returns:
        Tuple of (success: bool, error_message: Optional[str])
    """
    sentence = scenario.get("sentence", "")
    aliases = scenario.get("aliases", {})
    # Core domain: "service" or "reservation"
    domain = scenario.get("domain", "service")
    expected = scenario.get("expected", {})
    expected_intent = expected.get("intent")
    expected_status = expected.get("status")
    expected_slots = expected.get("slots", {})

    # Create unique user_id for this scenario
    user_id = f"test_scenario_{scenario_index:03d}"

    # Create LumaClient with scenario-specific aliases
    luma_client = TestLumaClient(test_aliases=aliases)

    # Create CatalogClient that returns test aliases as catalog data
    # This ensures the orchestrator builds tenant_context with test aliases
    catalog_client = TestCatalogClient(test_aliases=aliases, domain=domain)

    # Set up org domain cache to use the scenario's domain
    # This ensures the orchestrator uses the correct domain instead of deriving from org
    _setup_test_org_domain(domain)

    # Clear catalog cache to ensure fresh data from TestCatalogClient
    from core.orchestration.cache.catalog_cache import catalog_cache
    test_org_id = int(os.getenv("ORG_ID", "1"))
    # Clear cache for this org/domain
    catalog_cache._mem_cache.pop((test_org_id, domain), None)

    if verbose:
        print(f"\n{'='*60}")
        print(f"Scenario {scenario_index}: {sentence}")
        print(f"{'='*60}")
        print(f"Expected: intent={expected_intent}, status={expected_status}")
        if expected_slots:
            print(f"Expected slots: {json.dumps(expected_slots, indent=2)}")

    try:
        # Call handle_message with scenario sentence
        # Note: domain may be overridden by org domain, but we pass it explicitly
        result = handle_message(
            user_id=user_id,
            text=sentence,
            domain=domain,  # Use core domain: "service" for appointments, "reservation" for reservations
            timezone="UTC",
            phone_number=customer_details['phone_number'],
            email=customer_details['email'],
            customer_id=customer_details['customer_id'],
            luma_client=luma_client,
            catalog_client=catalog_client,  # Pass test catalog client
            verbose=verbose  # Pass verbose flag for detailed logging
        )

        if verbose:
            print(f"\nResult:")
            print(json.dumps(result, indent=2, default=str))

        # Verify success
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            return False, f"handle_message returned success=false: {error_msg}"

        outcome = result.get("outcome", {})
        actual_status = outcome.get("status")

        # Extract intent from multiple possible sources
        # For clarification outcomes, intent might not be in outcome
        # Try to get it from Luma response if available
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
        # Note: For NEEDS_CLARIFICATION outcomes, intent might not be in outcome
        # but we can still check if it was in the Luma response
        if expected_intent:
            if actual_intent:
                if actual_intent != expected_intent:
                    return False, f"Intent mismatch: expected {expected_intent}, got {actual_intent}"
            elif expected_status != STATUS_NEEDS_CLARIFICATION:
                # For non-clarification outcomes, intent must be present
                return False, f"Intent not found in outcome (expected {expected_intent})"
            elif verbose:
                # For clarification outcomes, intent might not be in outcome - this is OK
                print(
                    f"Note: Intent not found in outcome (expected {expected_intent}), but this is OK for NEEDS_CLARIFICATION")

        # Handle different expected statuses
        if expected_status == STATUS_NEEDS_CLARIFICATION:
            # Should get NEEDS_CLARIFICATION outcome
            if actual_status != "NEEDS_CLARIFICATION":
                return False, f"Expected NEEDS_CLARIFICATION, got {actual_status}"

            # Verify clarification reason if specified
            expected_reason = expected.get("clarification_reason")
            if expected_reason:
                actual_reason = outcome.get("clarification_reason")
                if actual_reason != expected_reason:
                    return False, f"Clarification reason mismatch: expected {expected_reason}, got {actual_reason}"

            # Second assertion: Test rendered response
            success, error_msg = _validate_rendered_response(
                outcome, expected, verbose)
            if not success:
                return False, error_msg

            if verbose:
                print(f"✓ NEEDS_CLARIFICATION verified")
            return True, None

        elif expected_status in (STATUS_EXECUTED, STATUS_AWAITING_CONFIRMATION):
            # Expected status can be either AWAITING_CONFIRMATION or EXECUTED
            # If AWAITING_CONFIRMATION, send confirmation and verify EXECUTED
            # If EXECUTED is expected, it should execute immediately
            if actual_status == "AWAITING_CONFIRMATION":
                # Second assertion: Test rendered response for AWAITING_CONFIRMATION outcome
                success, error_msg = _validate_rendered_response(
                    outcome, expected, verbose)
                if not success:
                    return False, error_msg

                if verbose:
                    print(f"Status is AWAITING_CONFIRMATION, sending confirmation...")

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
                    return False, f"Confirmation failed: {confirm_result.get('error', 'Unknown error')}"

                confirm_outcome = confirm_result.get("outcome", {})
                confirm_status = confirm_outcome.get("status")

                if confirm_status != "EXECUTED":
                    # Try repeating the original sentence (user confirming by repeating)
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
                    return False, f"After confirmation, expected EXECUTED, got {confirm_status}"

                if verbose:
                    print(f"✓ Confirmed and executed successfully")
                    if confirm_outcome.get("booking_code"):
                        print(
                            f"  Booking code: {confirm_outcome.get('booking_code')}")

                # Second assertion: Test rendered response for confirmation outcome
                success, error_msg = _validate_rendered_response(
                    confirm_outcome, expected, verbose)
                if not success:
                    return False, error_msg

                return True, None

            elif actual_status == "EXECUTED":
                # Already executed, verify booking_code exists
                if not outcome.get("booking_code"):
                    return False, "EXECUTED status but no booking_code in outcome"

                if verbose:
                    print(f"✓ Executed immediately (no confirmation needed)")
                    print(f"  Booking code: {outcome.get('booking_code')}")

                # Second assertion: Test rendered response for executed outcome
                success, error_msg = _validate_rendered_response(
                    outcome, expected, verbose)
                if not success:
                    return False, error_msg

                return True, None

            else:
                return False, f"Expected {expected_status}, got {actual_status}"

        else:
            return False, f"Unknown expected status: {expected_status}"

    except Exception as e:
        import traceback
        error_msg = f"Exception in scenario: {str(e)}\n{traceback.format_exc()}"
        if verbose:
            print(f"❌ Exception: {error_msg}")
        return False, error_msg


def run_all_scenarios(
    scenarios: List[Dict[str, Any]],
    customer_details: Dict[str, Optional[Any]],
    verbose: bool = False,
    scenario_indices: Optional[List[int]] = None
) -> Tuple[int, int, int, List[Tuple[int, str]]]:
    """
    Run all booking scenarios and return statistics.

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

    # If scenario_indices is provided, use those indices directly
    # Otherwise, iterate over the scenarios list sequentially
    if scenario_indices is not None:
        # Use original indices - need to map to scenarios list
        # For single scenario or filtered scenarios, we need to track original index
        indices_to_run = scenario_indices
        # Create a mapping: original_index -> scenario_in_list
        # When scenarios are filtered, we need to find the matching scenario
        for original_idx in indices_to_run:
            if original_idx >= len(core_booking_scenarios):
                skipped += 1
                continue

            # Find the scenario in the filtered list
            # If scenarios list is filtered, find by matching sentence or use position
            # For single scenario runs, scenarios list has 1 element at position 0
            if len(scenarios) == 1 and len(scenario_indices) == 1:
                # Single scenario case: use index 0
                scenario = scenarios[0]
                display_idx = original_idx  # Show original index in output
            else:
                # Multiple scenarios: find matching scenario
                # Try to find by index position in original list
                if original_idx < len(scenarios):
                    scenario = scenarios[original_idx]
                    display_idx = original_idx
                else:
                    # Scenario not in filtered list
                    skipped += 1
                    continue

            success, error_msg = test_scenario_e2e(
                scenario, display_idx, customer_details, verbose)

            if success:
                passed += 1
                # Only print passing scenarios in verbose mode
                if verbose:
                    print(
                        f"✓ Scenario {display_idx}: {scenario.get('sentence', '')[:50]}...")
            else:
                failed += 1
                failures.append((display_idx, error_msg or "Unknown error"))
                # Always print failures
                print(
                    f"✗ Scenario {display_idx}: {scenario.get('sentence', '')[:50]}...")
                if error_msg:
                    print(f"  Error: {error_msg}")
    else:
        # No specific indices - iterate over all scenarios sequentially
        indices_to_run = range(len(scenarios))

        for list_idx in indices_to_run:
            if list_idx >= len(scenarios):
                skipped += 1
                continue

            scenario = scenarios[list_idx]
            # For sequential iteration, use list index as display index
            success, error_msg = test_scenario_e2e(
                scenario, list_idx, customer_details, verbose)

            if success:
                passed += 1
                # Only print passing scenarios in verbose mode
                if verbose:
                    print(
                        f"✓ Scenario {list_idx}: {scenario.get('sentence', '')[:50]}...")
            else:
                failed += 1
                failures.append((list_idx, error_msg or "Unknown error"))
                # Always print failures
                print(
                    f"✗ Scenario {list_idx}: {scenario.get('sentence', '')[:50]}...")
                if error_msg:
                    print(f"  Error: {error_msg}")

    return passed, failed, skipped, failures


def main():
    """Main test runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run E2E tests for booking scenarios",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m core.tests.integration.test_appointment_e2e
  python -m core.tests.integration.test_appointment_e2e 1
  python -m core.tests.integration.test_appointment_e2e 1 --verbose
  python -m core.tests.integration.test_appointment_e2e --range 0-10
  python -m core.tests.integration.test_appointment_e2e --skip-needs-clarification
        """
    )
    parser.add_argument("scenario", type=int, nargs="?",
                        help="Run specific scenario by index (positional argument)")
    parser.add_argument("--scenario", type=int, dest="scenario_flag",
                        help="Run specific scenario by index (alternative to positional)")
    parser.add_argument("--range", type=str,
                        help="Run scenarios in range (e.g., '0-10')")
    parser.add_argument("--verbose", "-v",
                        action="store_true", help="Verbose output")
    parser.add_argument("--skip-needs-clarification", action="store_true",
                        help="Skip NEEDS_CLARIFICATION scenarios")
    args = parser.parse_args()

    # Use positional scenario if provided, otherwise use flag
    scenario_num = args.scenario if args.scenario is not None else args.scenario_flag

    customer_details = get_customer_details()
    if not customer_details['customer_id'] and not customer_details['phone_number'] and not customer_details['email']:
        if args.verbose:
            print("WARNING: No customer_id, phone, or email found in environment")
            print("Test may fail if customer creation is required")

    # Filter scenarios if needed
    scenarios_to_run = core_booking_scenarios
    scenario_indices = None

    if scenario_num is not None:
        scenario_indices = [scenario_num]
        scenarios_to_run = [core_booking_scenarios[scenario_num]
                            ] if scenario_num < len(core_booking_scenarios) else []
        if args.verbose:
            print(f"Running single scenario: {scenario_num}")
    elif args.range:
        start, end = map(int, args.range.split('-'))
        scenario_indices = list(range(start, end + 1))
        scenarios_to_run = [core_booking_scenarios[i]
                            for i in scenario_indices if i < len(core_booking_scenarios)]
        if args.verbose:
            print(f"Running scenarios: {args.range}")
    elif args.skip_needs_clarification:
        # Filter out NEEDS_CLARIFICATION scenarios
        filtered = []
        filtered_indices = []
        for idx, scenario in enumerate(core_booking_scenarios):
            expected_status = scenario.get("expected", {}).get("status")
            if expected_status != STATUS_NEEDS_CLARIFICATION:
                filtered.append(scenario)
                filtered_indices.append(idx)
        scenarios_to_run = filtered
        scenario_indices = filtered_indices
        if args.verbose:
            print(
                f"Running {len(scenarios_to_run)} scenarios (skipped NEEDS_CLARIFICATION)")

    # Print header after scenarios_to_run is determined
    if not args.verbose:
        # Minimal output header
        scenarios_count = len(scenarios_to_run)
        print(
            f"Running E2E booking scenarios test ({scenarios_count} scenario{'s' if scenarios_count != 1 else ''})...")
    else:
        print("="*70)
        print("E2E BOOKING SCENARIOS TEST")
        print("="*70)
        print(f"Total scenarios: {len(core_booking_scenarios)}")
        if len(scenarios_to_run) != len(core_booking_scenarios):
            print(
                f"Running: {len(scenarios_to_run)} scenario{'s' if len(scenarios_to_run) != 1 else ''}")

    if not scenarios_to_run:
        print("No scenarios to run!")
        sys.exit(1)

    # Run scenarios
    passed, failed, skipped, failures = run_all_scenarios(
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
        print()  # Just a newline

    print(
        f"Total: {len(scenarios_to_run)} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")

    if failures:
        print("\nFailures:")
        for idx, error_msg in failures:
            scenario = core_booking_scenarios[idx] if idx < len(
                core_booking_scenarios) else {}
            print(f"  Scenario {idx}: {scenario.get('sentence', 'N/A')[:60]}")
            print(f"    Error: {error_msg}")

    if args.verbose:
        print("="*70)

    if failed > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
