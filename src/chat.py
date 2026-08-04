#!/usr/bin/env python3
"""
chat.py - Interactive chat REPL for DialogCart orchestration.

Thin HTTP client to the core API (POST /api/message). Session persistence
lives on the server — same path real integrations and E2E tests should use.

Organization selection is driven by business category (developer UX only).
Core continues to resolve category → schema → booking_domain from the org.

Usage:
    # Terminal 1 — start core API
    python -m core.api.main

    # Terminal 2 — chat REPL
    python chat.py
    python chat.py --business-category beauty_salon
    python chat.py --business-category car_service --user-id alice
    python chat.py --business-category hotel --debug

In-session commands:
    quit / exit / q              - end the session
    reset                       - clear session state and start fresh
    switch <business_category>  - switch vertical, clear session, reprint banner
    debug                       - toggle verbose JSON output
    trace                       - toggle Decision Trace output
    status                      - show current session state
    catalog                     - show catalog collections for the active category
    help                        - list commands and business categories
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

# Add src/ to Python path (catalog / org resolve helpers)
src_path = Path(__file__).parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

try:
    from dotenv import load_dotenv

    for env_path in [
        src_path.parent / ".env.local",
        src_path.parent / ".env",
        src_path / ".env",
    ]:
        if env_path.exists():
            load_dotenv(env_path, override=False)
except ImportError:
    pass

try:
    from core.tracing.views import format_trace_view_text
except ImportError:
    format_trace_view_text = None

# Developer-tool mapping only — not a platform config surface.
# Live developer orgs: 1=beauty_salon, 2=car_service (org 3 may be absent).
BUSINESS_CATEGORY_TO_ORG: Dict[str, int] = {
    "beauty_salon": 1,
    "car_service": 2,
    "hotel": 3,
}

DEFAULT_BUSINESS_CATEGORY = "beauty_salon"

_COLLECTION_TITLES: Dict[str, str] = {
    "services": "Services",
    "staff": "Staff",
    "room_types": "Room Types",
    "extras": "Extras",
}

_CATEGORY_TITLES: Dict[str, str] = {
    "beauty_salon": "Beauty Salon",
    "car_service": "Car Service",
    "hotel": "Hotel",
}


def supported_business_categories() -> List[str]:
    return sorted(BUSINESS_CATEGORY_TO_ORG.keys())


def resolve_org_id(business_category: str) -> int:
    """Map a business category to the hardcoded developer org id."""
    if business_category not in BUSINESS_CATEGORY_TO_ORG:
        raise ValueError(
            f"Unknown business category {business_category!r}. "
            f"Supported: {', '.join(supported_business_categories())}"
        )
    return BUSINESS_CATEGORY_TO_ORG[business_category]


def _get_customer_id() -> int | None:
    value = os.getenv("CUSTOMER_ID") or os.getenv("TEST_CUSTOMER_ID")
    if not value:
        return None
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return None


def _get_customer_phone() -> str | None:
    value = os.getenv("CUSTOMER_PHONE") or os.getenv("TEST_CUSTOMER_PHONE")
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def _get_customer_email() -> str | None:
    value = os.getenv("CUSTOMER_EMAIL") or os.getenv("TEST_CUSTOMER_EMAIL")
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def _get_customer_name() -> str | None:
    value = os.getenv("CUSTOMER_NAME") or os.getenv("TEST_CUSTOMER_NAME")
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def _get_core_base_url() -> str:
    return os.getenv("CORE_BASE_URL", "http://localhost:8000").rstrip("/")


def resolve_booking_domain_for_org(org_id: int) -> str:
    """Booking domain from organization resolution (not from CLI category alone)."""
    try:
        from core.adapters.cache.org_domain_cache import org_domain_cache
        from core.adapters.clients.organization_client import OrganizationClient

        _category, booking_domain, _ = org_domain_cache.resolve(
            org_id, OrganizationClient(), force_refresh=False
        )
        if booking_domain:
            return str(booking_domain)
    except Exception:
        pass
    return "service"


def format_startup_banner(
    *,
    business_category: str,
    booking_domain: str,
    org_id: int,
) -> str:
    lines = [
        "=" * 50,
        f"Business Category : {business_category}",
        f"Booking Domain    : {booking_domain}",
        f"Organization ID   : {org_id}",
        "=" * 50,
    ]
    return "\n".join(lines)


def _collection_title(collection_key: str) -> str:
    if collection_key in _COLLECTION_TITLES:
        return _COLLECTION_TITLES[collection_key]
    return collection_key.replace("_", " ").title()


def _active_item_names(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    names: List[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("is_active") is False:
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def format_catalog_display(
    business_category: str,
    catalog_data: Dict[str, Any],
    collection_keys: List[str],
) -> str:
    """Render schema-referenced catalog collections (no salon hardcoding)."""
    title = _CATEGORY_TITLES.get(business_category, business_category)
    lines = [title, ""]
    if not collection_keys:
        lines.append("(no catalog collections declared in business schema)")
        return "\n".join(lines)

    for key in collection_keys:
        heading = _collection_title(key)
        lines.append(heading)
        lines.append("-" * len(heading))
        names = _active_item_names(catalog_data.get(key))
        if names:
            lines.extend(names)
        else:
            lines.append("(empty)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def load_catalog_for_category(
    org_id: int,
    business_category: str,
    *,
    force_refresh: bool = True,
) -> Tuple[Dict[str, Any], List[str], str]:
    """Fetch catalog for org booking_domain; return data, schema keys, domain."""
    from core.adapters.cache.catalog_cache import catalog_cache
    from core.adapters.clients.catalog_client import CatalogClient
    from core.config.business_category_loader import get_catalog_collection_keys

    booking_domain = resolve_booking_domain_for_org(org_id)
    data = catalog_cache.get_catalog(
        org_id,
        CatalogClient(),
        domain=booking_domain,
        force_refresh=force_refresh,
    )
    keys = get_catalog_collection_keys(business_category)
    return data, keys, booking_domain


def _print_catalog(org_id: int, business_category: str) -> None:
    try:
        data, keys, _domain = load_catalog_for_category(org_id, business_category)
        print("\n" + format_catalog_display(business_category, data, keys))
    except Exception as e:
        print(f"[catalog error] {e}")


def _print_banner(
    business_category: str,
    org_id: int,
    *,
    core_url: str,
    user_id: str,
    customer_id: int | None,
) -> None:
    booking_domain = resolve_booking_domain_for_org(org_id)
    print("\nDialogCart Chat (HTTP → core API)")
    print(
        format_startup_banner(
            business_category=business_category,
            booking_domain=booking_domain,
            org_id=org_id,
        )
    )
    luma_url = os.getenv("LUMA_BASE_URL", "http://localhost:9002")
    internal_url = os.getenv("INTERNAL_API_BASE_URL", "http://localhost:3000")
    print(
        f"  core={core_url}  user={user_id}  customer={customer_id or 'unset'}"
    )
    print(f"  luma={luma_url}  internal={internal_url}")
    print(
        "  Commands: quit  reset  switch <category>  debug  trace  "
        "status  catalog  help"
    )


def _print_help() -> None:
    cats = ", ".join(supported_business_categories())
    print(
        "\nCommands:\n"
        "  quit / exit / q              end the session\n"
        "  reset                        clear session for current org\n"
        "  switch <business_category>   switch vertical and clear session\n"
        "  debug                        toggle verbose JSON\n"
        "  trace                        toggle Decision Trace\n"
        "  status                       show session state\n"
        "  catalog                      show schema catalog collections\n"
        "  help                         show this help\n"
        f"\nBusiness categories: {cats}\n"
    )


def _condense_result(result: dict) -> dict:
    """Compact view of a result — only fields useful for turn-by-turn debugging."""
    outcome = result.get("outcome") or result.get("result") or {}

    out: dict = {}

    for key in ("intent_name", "status", "stage", "action"):
        val = outcome.get(key)
        if val is not None:
            out[key] = val

    slots = {
        k: v
        for k, v in (outcome.get("slots") or {}).items()
        if not k.startswith("_") and v is not None
    }
    if slots:
        out["slots"] = slots

    missing = outcome.get("missing_slots")
    if missing is not None:
        out["missing_slots"] = missing

    allowed = outcome.get("allowed_actions") or []
    if allowed:
        out["allowed_actions"] = allowed

    blocked = outcome.get("blocked_actions") or []
    if blocked:
        out["blocked_actions"] = blocked

    awaiting = outcome.get("awaiting")
    if awaiting is not None:
        out["awaiting"] = awaiting

    for prop in ("date_proposal", "time_proposal"):
        val = outcome.get(prop) or result.get(prop)
        if val:
            out[prop] = val

    candidates = (outcome.get("facts") or {}).get("service_candidates") or []
    if candidates:
        out["service_candidates"] = candidates

    text = result.get("text") or outcome.get("text")
    if text:
        out["bot"] = text

    return out


def _extract_text(result: dict) -> str:
    outcome = result.get("outcome") or {}
    text = result.get("text") or outcome.get("text")
    if text:
        return text
    if not result.get("success") and result.get("message"):
        return f"[{result.get('error', 'error')}] {result.get('message')}"
    status = outcome.get("status", "no-response")
    return f"[{status}] (no text — try a booking request)"


def build_message_payload(
    *,
    user_id: str,
    text: str,
    timezone: str,
    org_id: int,
    booking_domain: str,
    customer_id: int | None = None,
    customer_phone: str | None = None,
    customer_email: str | None = None,
    customer_name: str | None = None,
) -> dict:
    """Build /api/message JSON body (organization_id as today)."""
    payload = {
        "user_id": user_id,
        "text": text,
        "domain": booking_domain,
        "timezone": timezone,
        "organization_id": org_id,
    }
    if customer_id is not None:
        payload["customer_id"] = customer_id
    if customer_phone:
        payload["customer_phone"] = customer_phone
    if customer_email:
        payload["customer_email"] = customer_email
    if customer_name:
        payload["customer_name"] = customer_name
    return payload


def _post_message(
    client: httpx.Client,
    *,
    core_url: str,
    user_id: str,
    text: str,
    domain: str,
    timezone: str,
    org_id: int,
    customer_id: int | None = None,
    customer_phone: str | None = None,
    customer_email: str | None = None,
    customer_name: str | None = None,
    trace: bool = False,
    trace_view: str = "summary",
) -> dict:
    params = {}
    if trace:
        params["trace"] = trace_view
    payload = build_message_payload(
        user_id=user_id,
        text=text,
        timezone=timezone,
        org_id=org_id,
        booking_domain=domain,
        customer_id=customer_id,
        customer_phone=customer_phone,
        customer_email=customer_email,
        customer_name=customer_name,
    )

    response = client.post(
        f"{core_url}/api/message",
        json=payload,
        params=params,
        headers={"X-Debug-Decision-Trace": "true"} if trace else {},
    )
    if response.is_success:
        return response.json()

    detail = (
        response.json().get("detail")
        if response.headers.get("content-type", "").startswith("application/json")
        else None
    )
    if isinstance(detail, dict):
        return {
            "success": detail.get("success", False),
            "error": detail.get("error", f"http_{response.status_code}"),
            "message": detail.get("message", response.text),
        }
    return {
        "success": False,
        "error": f"http_{response.status_code}",
        "message": response.text,
    }


def _get_session(
    client: httpx.Client, core_url: str, organization_id: int, user_id: str
) -> dict | None:
    response = client.get(
        f"{core_url}/api/organizations/{organization_id}/sessions/{user_id}"
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("session")


def _clear_session(
    client: httpx.Client, core_url: str, organization_id: int, user_id: str
) -> None:
    response = client.delete(
        f"{core_url}/api/organizations/{organization_id}/sessions/{user_id}"
    )
    response.raise_for_status()


def switch_business_category(
    current_category: str,
    new_category: str,
) -> Tuple[str, int]:
    """Validate and return (category, org_id). Raises ValueError if unknown."""
    category = new_category.strip().lower()
    org_id = resolve_org_id(category)
    return category, org_id


def chat_loop(
    user_id: str,
    timezone: str,
    debug: bool,
    trace: bool = False,
    trace_view: str = "summary",
    business_category: str = DEFAULT_BUSINESS_CATEGORY,
):
    business_category = business_category.strip().lower()
    org_id = resolve_org_id(business_category)
    customer_id = _get_customer_id()
    customer_phone = _get_customer_phone()
    customer_email = _get_customer_email()
    customer_name = _get_customer_name()
    core_url = _get_core_base_url()

    _print_banner(
        business_category,
        org_id,
        core_url=core_url,
        user_id=user_id,
        customer_id=customer_id,
    )
    if trace:
        print(f"  trace view: {trace_view}")
    print("-" * 50)

    with httpx.Client(timeout=60.0) as client:
        try:
            health = client.get(f"{core_url}/health")
            health.raise_for_status()
        except Exception as e:
            print(f"\n[error] Core API not reachable at {core_url}: {e}")
            print("  Start it with: python -m core.api.main")
            return

        while True:
            try:
                user_text = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            if not user_text:
                continue

            cmd = user_text.lower()
            parts = user_text.split(maxsplit=1)
            cmd0 = parts[0].lower()

            if cmd in ("quit", "exit", "q"):
                print("Goodbye!")
                break

            if cmd0 == "help":
                _print_help()
                continue

            if cmd0 == "switch":
                if len(parts) < 2 or not parts[1].strip():
                    print(
                        "[switch] usage: switch <business_category>\n"
                        f"  supported: {', '.join(supported_business_categories())}"
                    )
                    continue
                try:
                    new_category, new_org = switch_business_category(
                        business_category, parts[1]
                    )
                except ValueError as e:
                    print(f"[switch error] {e}")
                    continue
                try:
                    _clear_session(client, core_url, org_id, user_id)
                except Exception as e:
                    print(f"[switch] previous session clear skipped: {e}")
                business_category = new_category
                org_id = new_org
                try:
                    # Warm / refresh catalog view for the new org.
                    load_catalog_for_category(
                        org_id, business_category, force_refresh=True
                    )
                except Exception as e:
                    print(f"[switch] catalog reload note: {e}")
                _print_banner(
                    business_category,
                    org_id,
                    core_url=core_url,
                    user_id=user_id,
                    customer_id=customer_id,
                )
                continue

            if cmd == "reset":
                try:
                    _clear_session(client, core_url, org_id, user_id)
                    print("[session cleared]")
                except Exception as e:
                    print(f"[reset error] {e}")
                continue

            if cmd == "debug":
                debug = not debug
                print(f"[debug {'on' if debug else 'off'}]")
                continue

            if cmd == "trace":
                trace = not trace
                print(f"[trace {'on' if trace else 'off'}]")
                continue

            if cmd == "status":
                try:
                    sess = _get_session(client, core_url, org_id, user_id)
                    if sess:
                        print(
                            json.dumps(
                                {
                                    "status": sess.get("status"),
                                    "intent": sess.get("intent_name"),
                                    "slots": sess.get("slots"),
                                    "missing_slots": sess.get("missing_slots"),
                                },
                                indent=2,
                            )
                        )
                    else:
                        print("[no active session]")
                except Exception as e:
                    print(f"[status error] {e}")
                continue

            if cmd == "catalog":
                _print_catalog(org_id, business_category)
                continue

            booking_domain = resolve_booking_domain_for_org(org_id)
            try:
                result = _post_message(
                    client,
                    core_url=core_url,
                    user_id=user_id,
                    text=user_text,
                    domain=booking_domain,
                    timezone=timezone,
                    org_id=org_id,
                    customer_id=customer_id,
                    customer_phone=customer_phone,
                    customer_email=customer_email,
                    customer_name=customer_name,
                    trace=trace,
                    trace_view=trace_view,
                )
            except httpx.ConnectError as e:
                print(f"\n[error] Cannot reach core API at {core_url}: {e}")
                print("  Start it with: python -m core.api.main")
                continue
            except Exception as e:
                print(f"\n[error] {type(e).__name__}: {e}")
                if debug:
                    import traceback

                    traceback.print_exc()
                continue

            outcome = result.get("outcome") or {}
            response_text = _extract_text(result)

            dt_text = result.get("decision_trace_text")
            if trace and dt_text and format_trace_view_text is not None:
                try:
                    print("\n" + dt_text)
                except Exception as e:
                    print(f"[trace render error] {e}")
            elif (
                trace
                and result.get("decision_trace")
                and format_trace_view_text is not None
            ):
                try:
                    view = result.get("trace_view") or "summary"
                    print(
                        "\n"
                        + format_trace_view_text(result["decision_trace"], view)
                    )
                except Exception as e:
                    print(f"[trace render error] {e}")

            if debug or not (result.get("text") or outcome.get("text")):
                print("\n--- RESULT ---")
                try:
                    print(
                        json.dumps(_condense_result(result), indent=2, default=str)
                    )
                except Exception:
                    print(repr(result))
                print("--- END ---")

            print(f"\nBot: {response_text}")


def build_arg_parser() -> argparse.ArgumentParser:
    cats = ", ".join(supported_business_categories())
    parser = argparse.ArgumentParser(
        description=(
            "Interactive DialogCart chat REPL. "
            f"Business categories: {cats}."
        )
    )
    parser.add_argument(
        "--business-category",
        default=DEFAULT_BUSINESS_CATEGORY,
        choices=sorted(BUSINESS_CATEGORY_TO_ORG.keys()),
        help=(
            "Business category to test "
            f"(default: {DEFAULT_BUSINESS_CATEGORY}). "
            f"Maps to org ids: {BUSINESS_CATEGORY_TO_ORG}"
        ),
    )
    parser.add_argument(
        "--user-id",
        default="chat_user",
        help="User ID for session (default: chat_user)",
    )
    parser.add_argument("--timezone", default="UTC", help="Timezone (default: UTC)")
    parser.add_argument(
        "--debug", action="store_true", help="Show debug JSON on each turn"
    )
    parser.add_argument(
        "--trace",
        nargs="?",
        const="summary",
        default=None,
        metavar="VIEW",
        choices=("summary", "reasoning", "forensic"),
        help="Print decision trace after each turn (default view: summary). "
        "VIEW may be summary, reasoning, or forensic.",
    )
    return parser


def main(argv: Optional[List[str]] = None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    trace_view = args.trace or "summary"
    chat_loop(
        args.user_id,
        args.timezone,
        args.debug,
        trace=args.trace is not None,
        trace_view=trace_view,
        business_category=args.business_category,
    )


if __name__ == "__main__":
    main()
