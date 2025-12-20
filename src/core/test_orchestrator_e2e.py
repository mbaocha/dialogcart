"""
End-to-End Test Script for Orchestrator

Simple script to test the orchestrator with real Luma and business API calls.
Make sure Luma and internal APIs are running before executing this script.
"""

import json
import os
import sys
from pathlib import Path

from core.orchestration.orchestrator import handle_message

# Load environment variables
try:
    from dotenv import load_dotenv
    # Project root is one level up from src/ (where this script runs from)
    project_root = Path(__file__).parent.parent.parent
    # Also check for .env in src/core/ (current directory)
    core_env_file = Path(__file__).parent / ".env"
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

    project_root = Path(__file__).parent.parent.parent
    core_env_file = Path(__file__).parent / ".env"
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


def test_resolved_booking():
    """Test a resolved booking flow."""
    print("\n" + "="*60)
    print("TEST: Resolved Booking Flow")
    print("="*60)

    # Load customer details from environment
    phone_number = os.getenv("TEST_CUSTOMER_PHONE")
    email = os.getenv("TEST_CUSTOMER_EMAIL")
    customer_id_str = os.getenv("TEST_CUSTOMER_ID")
    customer_id = int(customer_id_str) if customer_id_str else None

    # Debug: Print what was loaded
    print(
        f"DEBUG: customer_id_str={customer_id_str}, customer_id={customer_id}")
    print(f"DEBUG: phone_number={phone_number}, email={email}")

    if customer_id:
        print(f"Using test customer ID: {customer_id}")
    elif phone_number or email:
        print(f"Using test customer: phone={phone_number}, email={email}")
    else:
        print("WARNING: No customer_id, phone, or email found in environment")

    result = handle_message(
        user_id="test_user_123",
        text="book haircut tomorrow at 2pm",
        domain="service",
        timezone="UTC",
        phone_number=phone_number,
        email=email,
        customer_id=customer_id
    )

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
    else:
        print(f"\n[ERROR] {result.get('error')}")
        print(f"   Message: {result.get('message')}")


def test_partial_booking():
    """Test a partial booking (clarification) flow."""
    print("\n" + "="*60)
    print("TEST: Partial Booking Flow (Clarification)")
    print("="*60)

    # Load customer details from environment
    phone_number = os.getenv("TEST_CUSTOMER_PHONE")
    email = os.getenv("TEST_CUSTOMER_EMAIL")
    customer_id_str = os.getenv("TEST_CUSTOMER_ID")
    customer_id = int(customer_id_str) if customer_id_str else None

    result = handle_message(
        user_id="test_user_456",
        text="book haircut",
        domain="service",
        timezone="UTC",
        phone_number=phone_number,
        email=email,
        customer_id=customer_id
    )

    print("\nResult:")
    print(json.dumps(result, indent=2))

    if result.get("success"):
        outcome = result.get("outcome", {})
        if outcome.get("type") == "CLARIFY":
            print("\n[SUCCESS] Clarification returned")
            print(f"   Template: {outcome.get('template_key')}")
    else:
        print(f"\n[ERROR] {result.get('error')}")
        print(f"   Message: {result.get('message')}")


def test_custom_message(user_id: str, text: str):
    """Test with a custom message."""
    print("\n" + "="*60)
    print("TEST: Custom Message")
    print("="*60)
    print(f"User ID: {user_id}")
    print(f"Text: {text}")

    # Load customer details from environment
    phone_number = os.getenv("TEST_CUSTOMER_PHONE")
    email = os.getenv("TEST_CUSTOMER_EMAIL")
    customer_id_str = os.getenv("TEST_CUSTOMER_ID")
    customer_id = int(customer_id_str) if customer_id_str else None

    result = handle_message(
        user_id=user_id,
        text=text,
        domain="service",
        timezone="UTC",
        phone_number=phone_number,
        email=email,
        customer_id=customer_id
    )

    print("\nResult:")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Test dialogcart-core orchestrator")
    parser.add_argument(
        "--test",
        choices=["resolved", "partial", "custom"],
        default="resolved",
        help="Which test to run"
    )
    parser.add_argument("--user-id", default="test_user_123",
                        help="User ID for custom test")
    parser.add_argument("--text", help="Message text for custom test")

    args = parser.parse_args()

    if args.test == "resolved":
        test_resolved_booking()
    elif args.test == "partial":
        test_partial_booking()
    elif args.test == "custom":
        if not args.text:
            print("Error: --text is required for custom test")
            sys.exit(1)
        test_custom_message(args.user_id, args.text)

    print("\n" + "="*60)
    print("Test completed!")
    print("="*60)
