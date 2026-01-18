"""
End-to-End Test Script for Orchestrator

Simple script to test the orchestrator with real Luma and business API calls.
Make sure Luma and internal APIs are running before executing this script.

Usage:
    # Run all 10 examples
    python3 -m core.tests.orchestration.test_orchestrator_e2e

    # Run a specific example (1-10)
    python3 -m core.tests.orchestration.test_orchestrator_e2e 4
"""

from core.orchestration.orchestrator import handle_message
from core.orchestration.nlu import LumaClient
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional

# Set execution mode to test for deterministic E2E tests
os.environ["CORE_EXECUTION_MODE"] = "test"

# Add src/ to Python path so we can import core modules
# __file__ = src/core/tests/orchestration/test_orchestrator_e2e.py
# parent.parent.parent.parent = src/
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


# Load environment variables
try:
    from dotenv import load_dotenv
    # Project root is two levels up from tests/orchestration/ (where this script now runs from)
    project_root = Path(__file__).parent.parent.parent.parent
    # Also check for .env in src/core/
    core_env_file = Path(__file__).parent.parent.parent / ".env"
    env_file = project_root / ".env"
    env_local_file = project_root / ".env.local"

    # Debug: Print paths
    print(f"DEBUG ENV: __file__={__file__}")
    print(f"DEBUG ENV: project_root={project_root}")
    print(
        f"DEBUG ENV: core_env_file={core_env_file} (exists={core_env_file.exists()})")
    print(f"DEBUG ENV: env_file={env_file} (exists={env_file.exists()})")

    # Load core/.env last so it takes precedence over project root .env
    # Load order: project_root/.env -> core/.env -> project_root/.env.local
    if env_file.exists():
        load_dotenv(env_file, override=False)
        print(f"DEBUG ENV: Loaded {env_file}")
    if core_env_file.exists():
        load_dotenv(core_env_file, override=True)  # Override project root .env
        print(f"DEBUG ENV: Loaded {core_env_file}")
    if env_local_file.exists():
        load_dotenv(env_local_file, override=True)  # Override everything
        print(f"DEBUG ENV: Loaded {env_local_file}")
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
            print(f"DEBUG ENV: Error loading {env_path}: {e}")

    # Updated paths for new location: tests/orchestration/
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
    print(f"DEBUG ENV: Error loading .env files: {e}")


# Define 10 test examples
# Each example explicitly defines tenant aliases (like followup_scenarios.py and booking_scenarios.py)
TEST_EXAMPLES = [
    {
        "name": "Resolved Booking - Full Details",
        "description": "Complete booking with explicit tenant service name, date, and time",
        "text": "book premium haircut tomorrow at 2pm",
        "user_id": "test_user_001",
        "aliases": {
            "premium haircut": "beauty_and_wellness.haircut",
            "flexi haircut + prunning": "beauty_and_wellness.haircut"
        }
    },
    {
        "name": "Partial Booking - Missing Time",
        "description": "Explicit service and date provided, but time is missing",
        "text": "book premium haircut tomorrow",
        "user_id": "test_user_002",
        "aliases": {
            "premium haircut": "beauty_and_wellness.haircut",
            "flexi haircut + prunning": "beauty_and_wellness.haircut"
        }
    },
    {
        "name": "Partial Booking - Missing Date",
        "description": "Explicit service and time provided, but date is missing",
        "text": "book premium haircut at 2pm",
        "user_id": "test_user_003",
        "aliases": {
            "premium haircut": "beauty_and_wellness.haircut",
            "flexi haircut + prunning": "beauty_and_wellness.haircut"
        }
    },
    {
        "name": "Ambiguous Service - Multiple Matches",
        "description": "Generic service name matches multiple options (should trigger MULTIPLE_MATCHES)",
        "text": "book haircut tomorrow at 2pm",
        "user_id": "test_user_004",
        "aliases": {
            "premium haircut": "beauty_and_wellness.haircut",
            "flexi haircut + prunning": "beauty_and_wellness.haircut"
        }
    },
    {
        "name": "Missing Service",
        "description": "Only date and time provided, service is missing",
        "text": "book tomorrow at 2pm",
        "user_id": "test_user_005",
        "aliases": {
            "premium haircut": "beauty_and_wellness.haircut",
            "flexi haircut + prunning": "beauty_and_wellness.haircut"
        }
    },
    {
        "name": "Vague Time Reference",
        "description": "Explicit service and date provided with vague time",
        "text": "book premium haircut tomorrow afternoon",
        "user_id": "test_user_006",
        "aliases": {
            "premium haircut": "beauty_and_wellness.haircut",
            "flexi haircut + prunning": "beauty_and_wellness.haircut"
        }
    },
    {
        "name": "Complex Booking Request",
        "description": "Multiple details with natural language using explicit service",
        "text": "I'd like to book a premium haircut next Friday at 3:30pm",
        "user_id": "test_user_007",
        "aliases": {
            "premium haircut": "beauty_and_wellness.haircut",
            "flexi haircut + prunning": "beauty_and_wellness.haircut"
        }
    },
    {
        "name": "Minimal Request",
        "description": "Only generic service name provided (triggers ambiguity)",
        "text": "book haircut",
        "user_id": "test_user_008",
        "aliases": {
            "premium haircut": "beauty_and_wellness.haircut",
            "flexi haircut + prunning": "beauty_and_wellness.haircut"
        }
    },
    {
        "name": "Weekday Reference",
        "description": "Using weekday name with explicit service",
        "text": "book premium haircut on Monday at 10am",
        "user_id": "test_user_009",
        "aliases": {
            "premium haircut": "beauty_and_wellness.haircut",
            "flexi haircut + prunning": "beauty_and_wellness.haircut"
        }
    },
    {
        "name": "Time Range Request",
        "description": "Request with time range using explicit service",
        "text": "book premium haircut tomorrow between 2pm and 4pm",
        "user_id": "test_user_010",
        "aliases": {
            "premium haircut": "beauty_and_wellness.haircut",
            "flexi haircut + prunning": "beauty_and_wellness.haircut"
        }
    }
]


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
    """Custom LumaClient that injects tenant_context from test example aliases."""

    def __init__(self, test_aliases: Optional[Dict[str, str]] = None):
        """Initialize with test aliases to inject."""
        super().__init__()
        self.test_aliases = test_aliases or {}

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

        Test aliases completely override any tenant_context from orchestrator.
        """
        # Build tenant_context from test aliases (completely override)
        if self.test_aliases:
            tenant_context = {"aliases": self.test_aliases}

        return super().resolve(user_id, text, domain, timezone, tenant_context)


def run_example(example_num: int, example: Dict[str, Any], verbose: bool = True) -> Dict[str, Any]:
    """
    Run a single test example.

    Args:
        example_num: Example number (1-based)
        example: Example dictionary with name, description, text, user_id
        verbose: Whether to print detailed output

    Returns:
        Result dictionary from handle_message
    """
    if verbose:
        print("\n" + "="*60)
        print(f"EXAMPLE {example_num}: {example['name']}")
        print("="*60)
        print(f"Description: {example['description']}")
        print(f"User ID: {example['user_id']}")
        print(f"Text: {example['text']}")
        print("-"*60)

    customer_details = get_customer_details()

    if verbose and example_num == 1:
        # Print customer details only for first example
        if customer_details['customer_id']:
            print(f"Using test customer ID: {customer_details['customer_id']}")
        elif customer_details['phone_number'] or customer_details['email']:
            print(
                f"Using test customer: phone={customer_details['phone_number']}, email={customer_details['email']}")
        else:
            print("WARNING: No customer_id, phone, or email found in environment")

    # Create custom LumaClient with test aliases if provided
    test_aliases = example.get("aliases")
    luma_client = None
    if test_aliases:
        luma_client = TestLumaClient(test_aliases=test_aliases)
        if verbose:
            print(f"Using test aliases: {test_aliases}")

    result = handle_message(
        user_id=example['user_id'],
        text=example['text'],
        domain="service",
        timezone="UTC",
        phone_number=customer_details['phone_number'],
        email=customer_details['email'],
        customer_id=customer_details['customer_id'],
        luma_client=luma_client
    )

    if verbose:
        print("\nResult:")
        print(json.dumps(result, indent=2))

        if result.get("success"):
            outcome = result.get("outcome", {})
            if outcome.get("type") == "BOOKING_CREATED":
                print("\n[SUCCESS] Booking created!")
                print(f"   Booking Code: {outcome.get('booking_code')}")
                print(f"   Status: {outcome.get('status')}")
            elif outcome.get("type") == "CLARIFY":
                print("\n[CLARIFICATION] More info needed")
                print(f"   Template: {outcome.get('template_key')}")
                data = outcome.get('data', {})
                if data.get('missing'):
                    print(f"   Missing: {data.get('missing')}")
                if data.get('ambiguous'):
                    print(f"   Ambiguous: {data.get('ambiguous')}")
                if data.get('reason'):
                    print(f"   Reason: {data.get('reason')}")
        else:
            print(f"\n[ERROR] {result.get('error')}")
            print(f"   Message: {result.get('message')}")

    return result


def run_all_examples(verbose: bool = True) -> None:
    """Run all test examples."""
    print("\n" + "="*60)
    print("RUNNING ALL TEST EXAMPLES")
    print("="*60)
    print(f"Total examples: {len(TEST_EXAMPLES)}")

    results = []
    for i, example in enumerate(TEST_EXAMPLES, start=1):
        try:
            result = run_example(i, example, verbose=verbose)
            results.append({
                "example_num": i,
                "name": example['name'],
                "success": result.get("success", False),
                "outcome_type": result.get("outcome", {}).get("type", "UNKNOWN")
            })
        except Exception as e:
            print(f"\n[ERROR] Example {i} failed with exception: {e}")
            results.append({
                "example_num": i,
                "name": example['name'],
                "success": False,
                "error": str(e)
            })

    # Print summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for result in results:
        status = "✓" if result['success'] else "✗"
        outcome = result.get('outcome_type', result.get('error', 'UNKNOWN'))
        print(
            f"{status} Example {result['example_num']:2d}: {result['name']:<40} -> {outcome}")

    successful = sum(1 for r in results if r['success'])
    print(f"\nTotal: {successful}/{len(results)} successful")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Test dialogcart-core orchestrator with 10 examples",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all 10 examples
  python3 -m core.tests.orchestration.test_orchestrator_e2e

  # Run a specific example (1-10)
  python3 -m core.tests.orchestration.test_orchestrator_e2e 4

  # Run with minimal output
  python3 -m core.tests.orchestration.test_orchestrator_e2e --quiet
        """
    )
    parser.add_argument(
        "example_num",
        nargs="?",
        type=int,
        help="Example number to run (1-10). If not provided, runs all examples."
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Minimal output (only summary)"
    )
    # Keep backward compatibility
    parser.add_argument(
        "--test",
        choices=["resolved", "partial", "custom"],
        help="Legacy: Which test to run (deprecated, use example_num instead)"
    )
    parser.add_argument("--user-id", help="User ID for custom test (legacy)")
    parser.add_argument("--text", help="Message text for custom test (legacy)")

    args = parser.parse_args()

    verbose = not args.quiet

    # Handle positional argument (example number)
    if args.example_num is not None:
        if args.example_num < 1 or args.example_num > len(TEST_EXAMPLES):
            print(
                f"Error: Example number must be between 1 and {len(TEST_EXAMPLES)}")
            sys.exit(1)
        example = TEST_EXAMPLES[args.example_num - 1]
        run_example(args.example_num, example, verbose=verbose)
    # Handle legacy --test argument
    elif args.test:
        if args.test == "resolved":
            example = TEST_EXAMPLES[0]  # First example is resolved booking
            run_example(1, example, verbose=verbose)
        elif args.test == "partial":
            example = TEST_EXAMPLES[1]  # Second example is partial booking
            run_example(2, example, verbose=verbose)
        elif args.test == "custom":
            if not args.text:
                print("Error: --text is required for custom test")
                sys.exit(1)
            print("\n" + "="*60)
            print("TEST: Custom Message")
            print("="*60)
            customer_details = get_customer_details()
            result = handle_message(
                user_id=args.user_id or "test_user_custom",
                text=args.text,
                domain="service",
                timezone="UTC",
                phone_number=customer_details['phone_number'],
                email=customer_details['email'],
                customer_id=customer_details['customer_id']
            )
            print("\nResult:")
            print(json.dumps(result, indent=2))
    # Default: run all examples
    else:
        run_all_examples(verbose=verbose)

    print("\n" + "="*60)
    print("Test completed!")
    print("="*60)
