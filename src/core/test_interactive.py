"""
Interactive Integration Test for Orchestrator

Run this script to interactively test booking requests.
No mocks - uses real Luma and business API clients.
"""

import json
import os
import sys
from pathlib import Path

from core.orchestration.orchestrator import handle_message
from core.clients.catalog_client import CatalogClient
from core.cache.catalog_cache import catalog_cache
from core.clients.organization_client import OrganizationClient
from core.cache.org_domain_cache import org_domain_cache


def check_services():
    """Check if required services are running."""
    import httpx

    luma_url = os.getenv("LUMA_BASE_URL", "http://localhost:9001")
    internal_api_url = os.getenv(
        "INTERNAL_API_BASE_URL", "http://localhost:3000")

    print("\n" + "="*60)
    print("Service Availability Check")
    print("="*60)

    # Check Luma
    try:
        response = httpx.get(f"{luma_url}/health", timeout=2.0)
        if response.status_code == 200:
            print(f"✅ Luma service: {luma_url} - RUNNING")
        else:
            print(
                f"⚠️  Luma service: {luma_url} - Responded with {response.status_code}")
    except Exception as e:
        print(f"❌ Luma service: {luma_url} - NOT RUNNING")
        print(f"   Error: {str(e)}")

    # Check Internal API
    try:
        response = httpx.get(f"{internal_api_url}/health", timeout=2.0)
        if response.status_code == 200:
            print(f"✅ Internal API: {internal_api_url} - RUNNING")
        else:
            print(
                f"⚠️  Internal API: {internal_api_url} - Responded with {response.status_code}")
    except Exception:
        # Try a different endpoint
        try:
            response = httpx.get(f"{internal_api_url}/api/health", timeout=2.0)
            if response.status_code == 200:
                print(f"✅ Internal API: {internal_api_url} - RUNNING")
            else:
                print(f"❌ Internal API: {internal_api_url} - NOT RUNNING")
                print(f"   (Health check returned {response.status_code})")
        except Exception as e:
            print(f"❌ Internal API: {internal_api_url} - NOT RUNNING")
            print(f"   Error: Connection refused - service not available")
            print(f"   Make sure the booking service is started on port 3000")

    print("="*60 + "\n")


_CATALOG_CLIENT = CatalogClient()


def _get_org_id() -> int:
    value = os.getenv("ORG_ID", "1")
    try:
        org_id = int(value)
        return org_id if org_id > 0 else 1
    except Exception:
        return 1


def print_catalog_snapshot(org_id: int):
    """Fetch and display catalog snapshot via cache + CatalogClient."""
    try:
        data = catalog_cache.get_catalog(org_id, _CATALOG_CLIENT)
        print("\n" + "="*60)
        print(f"Catalog Snapshot (org_id={org_id})")
        print("="*60)
        print(
            f"catalog_last_updated_at: {data.get('catalog_last_updated_at')}")
        services = [
            s for s in data.get("services", [])
            if isinstance(s, dict) and s.get("is_active", True) is not False
        ]
        if services:
            print("Active services:")
            for svc in services:
                name = svc.get("name")
                fam = svc.get("service_family_id") or svc.get(
                    "canonical") or svc.get("slug")
                print(f" - {name}  (service_family_id={fam})")
        else:
            print("Active services: none")
        print("="*60 + "\n")
    except Exception as e:
        print("\n❌ Failed to fetch catalog snapshot")
        print(f"   Error: {type(e).__name__}: {e}\n")


def build_tenant_context(domain: str, org_id: int) -> dict | None:
    """Build tenant_context aliases similar to orchestrator for visibility."""
    try:
        data = catalog_cache.get_catalog(
            org_id, _CATALOG_CLIENT, domain=domain)
    except Exception:
        return None

    alias_map = {}
    if domain == "service":
        services = [
            s for s in data.get("services", [])
            if isinstance(s, dict) and s.get("is_active", True) is not False
        ]
        for svc in services:
            name = svc.get("name")
            if not name:
                continue
            canonical_key = (
                svc.get("service_family_id")
                or svc.get("canonical")
                or svc.get("slug")
                or name.lower().replace(" ", "_")
            )
            if canonical_key:
                alias_map[name.lower()] = canonical_key
    elif domain == "reservation":
        room_types = [
            r for r in data.get("room_types", [])
            if isinstance(r, dict) and r.get("is_active", True) is not False
        ]
        extras = [
            e for e in data.get("extras", [])
            if isinstance(e, dict) and e.get("is_active", True) is not False
        ]
        for rt in room_types:
            name = rt.get("name")
            if not name:
                continue
            canonical_key = rt.get("canonical") or rt.get(
                "slug") or name.lower().replace(" ", "_")
            if canonical_key:
                alias_map[name.lower()] = canonical_key
        for ex in extras:
            name = ex.get("name")
            if not name:
                continue
            canonical_key = ex.get("canonical") or ex.get(
                "slug") or name.lower().replace(" ", "_")
            if canonical_key:
                alias_map[name.lower()] = canonical_key

    return {"aliases": alias_map} if alias_map else None


def get_derived_domain(org_id: int) -> str:
    """Derive domain from org details (cached, long TTL)."""
    org_client = OrganizationClient()
    domain, _ = org_domain_cache.get_domain(
        org_id, org_client, force_refresh=False)
    return domain


# Load environment variables
try:
    from dotenv import load_dotenv
    project_root = Path(__file__).parent.parent.parent
    core_env_file = Path(__file__).parent / ".env"
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
        except Exception:
            pass

    project_root = Path(__file__).parent.parent.parent
    core_env_file = Path(__file__).parent / ".env"
    env_file = project_root / ".env"
    env_local_file = project_root / ".env.local"

    if env_file.exists():
        load_env_file(env_file, override=False)
    if core_env_file.exists():
        load_env_file(core_env_file, override=True)
    if env_local_file.exists():
        load_env_file(env_local_file, override=True)


def print_result(result):
    """Pretty print the result."""
    print("\n" + "="*60)
    print("RESULT")
    print("="*60)
    print(json.dumps(result, indent=2))
    print("="*60)

    if result.get("success"):
        outcome = result.get("outcome", {})
        outcome_type = outcome.get("type")

        if outcome_type == "BOOKING_CREATED":
            print("\n✅ BOOKING CREATED SUCCESSFULLY!")
            print(f"   Booking Code: {outcome.get('booking_code', 'N/A')}")
            print(f"   Status: {outcome.get('status', 'N/A')}")
        elif outcome_type == "BOOKING_CANCELLED":
            print("\n✅ BOOKING CANCELLED SUCCESSFULLY!")
            print(f"   Booking Code: {outcome.get('booking_code', 'N/A')}")
            print(f"   Status: {outcome.get('status', 'N/A')}")
        elif outcome_type == "CLARIFY":
            print("\n❓ CLARIFICATION NEEDED")
            print(f"   Template: {outcome.get('template_key', 'N/A')}")
            print(f"   Reason: {outcome.get('data', {}).get('reason', 'N/A')}")
            if outcome.get('booking'):
                print(
                    f"   Partial Booking: {json.dumps(outcome.get('booking'), indent=6)}")
    else:
        error = result.get("error", "unknown")
        message = result.get("message", "No message")
        print(f"\n❌ ERROR: {error}")
        print(f"   Message: {message}")


def interactive_test():
    """Interactive test loop."""
    print("\n" + "="*60)
    print("Interactive Orchestrator Test")
    print("="*60)
    print("\nEnter booking requests to test the orchestrator.")
    print("Type 'quit' or 'exit' to stop.")
    print("Type 'check' to verify services are running.")
    print("Type 'catalog' to view current catalog snapshot (cached, TTL ~60s).\n")
    org_id = _get_org_id()
    org_domain = get_derived_domain(org_id)
    print(f"Active organization_id: {org_id} (from ORG_ID env, default 1)")
    print(f"Derived domain for org: {org_domain}")
    print("Tip: Try: book me in for premium haircut tomorow by 9am")
    print("     Expect: resolves to the Premium Haircut service without clarification.\n")

    # Load customer details from environment (optional)
    phone_number = os.getenv("TEST_CUSTOMER_PHONE")
    email = os.getenv("TEST_CUSTOMER_EMAIL")
    customer_id_str = os.getenv("TEST_CUSTOMER_ID")
    customer_id = int(customer_id_str) if customer_id_str else None

    if customer_id or phone_number or email:
        print("Customer Info (from environment):")
        if customer_id:
            print(f"  Customer ID: {customer_id}")
        if phone_number:
            print(f"  Phone: {phone_number}")
        if email:
            print(f"  Email: {email}")
        print()

    while True:
        try:
            # Get user input
            user_input = input(
                "Enter booking request (or 'quit' to exit): ").strip()

            if not user_input:
                continue

            if user_input.lower() in ('quit', 'exit', 'q'):
                print("\nGoodbye!")
                break

            if user_input.lower() == 'check':
                check_services()
                continue

            if user_input.lower() == 'catalog':
                print_catalog_snapshot(org_id)
                continue

            # Optional: Get user_id
            user_id = input(
                "User ID (press Enter for default 'test_user'): ").strip()
            if not user_id:
                user_id = "test_user"

            # Optional: Get domain (will be overridden by derived org domain)
            domain_input = input(
                "Domain (press Enter for default 'service'): ").strip()
            if not domain_input:
                domain_input = "service"

            # Optional: Get timezone
            timezone = input(
                "Timezone (press Enter for default 'UTC'): ").strip()
            if not timezone:
                timezone = "UTC"

            # Use derived domain from org, ignore caller input
            domain = org_domain

            # Build tenant_context to show what will be sent to Luma
            tenant_context = build_tenant_context(domain, org_id)
            planned_payload = {
                "user_id": user_id,
                "text": user_input,
                "domain": domain,
                "timezone": timezone,
            }
            if tenant_context:
                planned_payload["tenant_context"] = tenant_context

            print(f"\nProcessing: '{user_input}'")
            print(
                f"User ID: {user_id}, Domain: {domain}, Timezone: {timezone}")
            print("\n[Planned Luma payload]")
            print(json.dumps(planned_payload, indent=2))
            print("\n[Flow] Resolving message (catalog → Luma → booking)...")

            # Call orchestrator
            try:
                result = handle_message(
                    user_id=user_id,
                    text=user_input,
                    domain=domain,
                    timezone=timezone,
                    organization_id=org_id,
                    phone_number=phone_number,
                    email=email,
                    customer_id=customer_id
                )

                # Print result
                print_result(result)

                # Additional debugging info
                if not result.get("success"):
                    error = result.get("error")
                    if error == "upstream_error":
                        print("\n💡 TROUBLESHOOTING:")
                        print(
                            "   The orchestrator successfully called Luma, but failed when")
                        print("   calling the internal booking API.")
                        print("\n   Make sure the internal booking API is running:")
                        print("   - Default URL: http://localhost:3000")
                        print("   - Set INTERNAL_API_BASE_URL env var if different")
                        print("   - Check that the booking service is started")
                    elif error == "luma_error":
                        print("\n💡 TROUBLESHOOTING:")
                        print("   Luma service returned an error.")
                        print("   Make sure Luma service is running:")
                        print("   - Default URL: http://localhost:9001")
                        print("   - Set LUMA_BASE_URL env var if different")

            except Exception as e:
                print(f"\n❌ EXCEPTION: {type(e).__name__}: {str(e)}")
                import traceback
                traceback.print_exc()

            print("\n")

        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ EXCEPTION: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            print()


def quick_test(text: str, user_id: str = "test_user", domain: str = "service", timezone: str = "UTC"):
    """Quick test with a single request."""
    org_id = _get_org_id()
    org_domain = get_derived_domain(org_id)
    print(f"\nProcessing: '{text}'")
    print(
        f"User ID: {user_id}, Domain (requested): {domain}, Derived Domain: {org_domain}, Timezone: {timezone}")
    print(f"Active organization_id: {org_id} (from ORG_ID env, default 1)")
    tenant_context = build_tenant_context(org_domain, org_id)
    planned_payload = {
        "user_id": user_id,
        "text": text,
        "domain": org_domain,
        "timezone": timezone,
    }
    if tenant_context:
        planned_payload["tenant_context"] = tenant_context
    print("\n[Planned Luma payload]")
    print(json.dumps(planned_payload, indent=2))
    print("\n[Flow] Resolving message (catalog → Luma → booking)...")

    # Load customer details from environment (optional)
    phone_number = os.getenv("TEST_CUSTOMER_PHONE")
    email = os.getenv("TEST_CUSTOMER_EMAIL")
    customer_id_str = os.getenv("TEST_CUSTOMER_ID")
    customer_id = int(customer_id_str) if customer_id_str else None

    try:
        result = handle_message(
            user_id=user_id,
            text=text,
            domain=org_domain,  # use derived domain
            timezone=timezone,
            organization_id=org_id,
            phone_number=phone_number,
            email=email,
            customer_id=customer_id
        )

        print_result(result)

        # Additional debugging info
        if not result.get("success"):
            error = result.get("error")
            if error == "upstream_error":
                print("\n💡 TROUBLESHOOTING:")
                print("   The orchestrator successfully called Luma, but failed when")
                print("   calling the internal booking API.")
                print("\n   Make sure the internal booking API is running:")
                print("   - Default URL: http://localhost:3000")
                print("   - Set INTERNAL_API_BASE_URL env var if different")
                print("   - Check that the booking service is started")
            elif error == "luma_error":
                print("\n💡 TROUBLESHOOTING:")
                print("   Luma service returned an error.")
                print("   Make sure Luma service is running:")
                print("   - Default URL: http://localhost:9001")
                print("   - Set LUMA_BASE_URL env var if different")

        return result
    except Exception as e:
        print(f"\n❌ EXCEPTION: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Interactive integration test for orchestrator")
    parser.add_argument(
        "--text",
        help="Booking request text (if provided, runs once and exits)")
    parser.add_argument(
        "--user-id",
        default="test_user",
        help="User ID (default: test_user)")
    parser.add_argument(
        "--domain",
        default="service",
        help="Domain (default: service)")
    parser.add_argument(
        "--timezone",
        default="UTC",
        help="Timezone (default: UTC)")
    parser.add_argument(
        "--check-services",
        action="store_true",
        help="Check if required services are running before testing")

    args = parser.parse_args()

    if args.check_services:
        check_services()
        if not args.text:
            sys.exit(0)

    if args.text:
        # Quick test mode
        quick_test(args.text, args.user_id, args.domain, args.timezone)
    else:
        # Interactive mode
        interactive_test()
