#!/usr/bin/env python3
"""
chat.py - Interactive chat REPL for DialogCart orchestration.

Thin HTTP client to the core API (POST /api/message). Session persistence
lives on the server — same path real integrations and E2E tests should use.

Usage:
    # Terminal 1 — start core API
    python -m core.orchestration.api.main

    # Terminal 2 — chat REPL
    CORE_BASE_URL=http://localhost:8000 ORG_ID=1 python chat.py
    CORE_BASE_URL=http://localhost:8000 ORG_ID=1 python chat.py --user-id alice --debug

In-session commands:
    quit / exit / q  - end the session
    reset            - clear session state and start fresh
    debug            - toggle verbose JSON output
    status           - show current session state
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

# Add src/ to Python path (for catalog debug command only)
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


def _get_org_id() -> int:
    try:
        return max(1, int(os.getenv("ORG_ID", "1")))
    except Exception:
        return 1


def _get_core_base_url() -> str:
    return os.getenv("CORE_BASE_URL", "http://localhost:8000").rstrip("/")


def _get_domain(org_id: int) -> str:
    try:
        from core.orchestration.cache.org_domain_cache import org_domain_cache
        from core.orchestration.clients.organization_client import OrganizationClient

        domain, _ = org_domain_cache.get_domain(
            org_id, OrganizationClient(), force_refresh=False
        )
        return domain or "service"
    except Exception:
        return "service"


def _condense_result(result: dict) -> dict:
    """Compact view of a result — only fields useful for turn-by-turn debugging."""
    outcome = result.get("outcome") or result.get("result") or {}

    out: dict = {}

    for key in ("intent_name", "status", "stage", "action"):
        val = outcome.get(key)
        if val is not None:
            out[key] = val

    slots = {
        k: v for k, v in (outcome.get("slots") or {}).items()
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


def _post_message(
    client: httpx.Client,
    *,
    core_url: str,
    user_id: str,
    text: str,
    domain: str,
    timezone: str,
    org_id: int,
) -> dict:
    response = client.post(
        f"{core_url}/api/message",
        json={
            "user_id": user_id,
            "text": text,
            "domain": domain,
            "timezone": timezone,
            "organization_id": org_id,
        },
    )
    if response.is_success:
        return response.json()

    detail = response.json().get("detail") if response.headers.get("content-type", "").startswith("application/json") else None
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


def _get_session(client: httpx.Client, core_url: str, user_id: str) -> dict | None:
    response = client.get(f"{core_url}/api/session/{user_id}")
    response.raise_for_status()
    payload = response.json()
    return payload.get("session")


def _clear_session(client: httpx.Client, core_url: str, user_id: str) -> None:
    response = client.delete(f"{core_url}/api/session/{user_id}")
    response.raise_for_status()


def _print_catalog(org_id: int, domain: str) -> None:
    try:
        from core.orchestration.cache.catalog_cache import catalog_cache
        from core.orchestration.clients.catalog_client import CatalogClient

        data = catalog_cache.get_catalog(org_id, CatalogClient(), domain=domain)
        services = [
            s for s in data.get("services", [])
            if isinstance(s, dict) and s.get("is_active") is not False
        ]
        print(f"\nCatalog (org={org_id}, domain={domain}):")
        if services:
            for svc in services:
                name = svc.get("name", "?")
                key = (
                    svc.get("service_family_id")
                    or svc.get("canonical")
                    or svc.get("slug")
                    or name.lower().replace(" ", "_")
                )
                alias = f"beauty_and_wellness.{key}" if "." not in str(key) else key
                print(f"  '{name.lower()}' → {alias}")
        else:
            print("  (no active services found)")
    except Exception as e:
        print(f"[catalog error] {e}")


def chat_loop(user_id: str, timezone: str, debug: bool):
    org_id = _get_org_id()
    domain = _get_domain(org_id)
    core_url = _get_core_base_url()
    luma_url = os.getenv("LUMA_BASE_URL", "http://localhost:9001")
    internal_url = os.getenv("INTERNAL_API_BASE_URL", "http://localhost:3000")

    print("\nDialogCart Chat (HTTP → core API)")
    print(f"  core={core_url}  org={org_id}  domain={domain}  user={user_id}")
    print(f"  luma={luma_url}  internal={internal_url}")
    print("  Commands: quit  reset  debug  status  catalog")
    print("-" * 50)

    with httpx.Client(timeout=60.0) as client:
        try:
            health = client.get(f"{core_url}/health")
            health.raise_for_status()
        except Exception as e:
            print(f"\n[error] Core API not reachable at {core_url}: {e}")
            print("  Start it with: python -m core.orchestration.api.main")
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

            if cmd in ("quit", "exit", "q"):
                print("Goodbye!")
                break

            if cmd == "reset":
                try:
                    _clear_session(client, core_url, user_id)
                    print("[session cleared]")
                except Exception as e:
                    print(f"[reset error] {e}")
                continue

            if cmd == "debug":
                debug = not debug
                print(f"[debug {'on' if debug else 'off'}]")
                continue

            if cmd == "status":
                try:
                    sess = _get_session(client, core_url, user_id)
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
                _print_catalog(org_id, domain)
                continue

            try:
                result = _post_message(
                    client,
                    core_url=core_url,
                    user_id=user_id,
                    text=user_text,
                    domain=domain,
                    timezone=timezone,
                    org_id=org_id,
                )
            except httpx.ConnectError as e:
                print(f"\n[error] Cannot reach core API at {core_url}: {e}")
                print("  Start it with: python -m core.orchestration.api.main")
                continue
            except Exception as e:
                print(f"\n[error] {type(e).__name__}: {e}")
                if debug:
                    import traceback
                    traceback.print_exc()
                continue

            outcome = result.get("outcome") or {}
            response_text = _extract_text(result)

            if debug or not (result.get("text") or outcome.get("text")):
                print("\n--- RESULT ---")
                try:
                    print(json.dumps(_condense_result(result), indent=2, default=str))
                except Exception:
                    print(repr(result))
                print("--- END ---")

            print(f"\nBot: {response_text}")


def main():
    parser = argparse.ArgumentParser(description="Interactive DialogCart chat REPL")
    parser.add_argument(
        "--user-id", default="chat_user", help="User ID for session (default: chat_user)"
    )
    parser.add_argument("--timezone", default="UTC", help="Timezone (default: UTC)")
    parser.add_argument("--debug", action="store_true", help="Show debug JSON on each turn")
    args = parser.parse_args()

    chat_loop(args.user_id, args.timezone, args.debug)


if __name__ == "__main__":
    main()
