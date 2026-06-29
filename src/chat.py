#!/usr/bin/env python3
"""
chat.py - Interactive chat REPL for DialogCart orchestration.

Maintains session state between turns so multi-turn conversations work correctly.
Shows only the bot response text, not raw JSON.

Usage:
    LUMA_BASE_URL=http://localhost:9002 ORG_ID=1 python chat.py
    LUMA_BASE_URL=http://localhost:9002 ORG_ID=1 python chat.py --user-id alice --debug

In-session commands:
    quit / exit / q  - end the session
    reset            - clear session state and start fresh
    debug            - toggle verbose JSON output
    status           - show current session state
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Add src/ to Python path
src_path = Path(__file__).parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Load .env files before importing core modules
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

from core.orchestration.api.session_merge import build_session_state_from_outcome
from core.orchestration.cache.catalog_cache import catalog_cache
from core.orchestration.cache.org_domain_cache import org_domain_cache
from core.orchestration.clients.catalog_client import CatalogClient
from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.nlu.conversation_memory import append_messages_turn
from core.orchestration.orchestrator import handle_message
from core.orchestration.session import clear_session, get_session, save_session

# Suppress all internal logging noise AFTER imports — orchestrator.py resets levels on import
logging.basicConfig(level=logging.CRITICAL)
for _noisy in [
    "core",
    "core.orchestration",
    "core.planning",
    "core.session",
    "core.nlu",
    "core.rendering",
    "core.turn_log",
    "httpx",
    "httpcore",
]:
    logging.getLogger(_noisy).setLevel(logging.CRITICAL)


def _get_org_id() -> int:
    try:
        return max(1, int(os.getenv("ORG_ID", "1")))
    except Exception:
        return 1


def _get_domain(org_id: int) -> str:
    try:
        domain, _ = org_domain_cache.get_domain(
            org_id, OrganizationClient(), force_refresh=False
        )
        return domain or "service"
    except Exception:
        return "service"


def _extract_text(result: dict) -> str:
    outcome = result.get("outcome") or {}
    text = result.get("text") or outcome.get("text")
    if text:
        return text
    if not result.get("success") and result.get("message"):
        return f"[{result.get('error', 'error')}] {result.get('message')}"
    status = outcome.get("status", "no-response")
    return f"[{status}] (no text — try a booking request)"


def _persist_session(result: dict, outcome: dict, session_state, raw_session, user_id: str, user_text: str):
    status = outcome.get("status")
    text = result.get("text") or outcome.get("text")

    if status in ("NEEDS_CLARIFICATION", "AWAITING_CAPABILITY"):
        new_session = build_session_state_from_outcome(
            outcome,
            status,
            result.get("_merged_luma_response"),
            session_state,
            user_id,
        )
        if new_session:
            new_session = append_messages_turn(new_session, user_text, text)
            save_session(user_id, new_session)

    elif status == "READY":
        base = raw_session or {}
        updated = append_messages_turn(base, user_text, text)
        save_session(user_id, updated)


def chat_loop(user_id: str, timezone: str, debug: bool):
    org_id = _get_org_id()
    domain = _get_domain(org_id)

    luma_url = os.getenv("LUMA_BASE_URL", "http://localhost:9001")

    print(f"\nDialogCart Chat")
    print(f"  org={org_id}  domain={domain}  user={user_id}  luma={luma_url}")
    print(f"  Commands: quit  reset  debug  status  catalog")
    print("-" * 50)

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
            clear_session(user_id)
            print("[session cleared]")
            continue

        if cmd == "debug":
            debug = not debug
            print(f"[debug {'on' if debug else 'off'}]")
            continue

        if cmd == "status":
            sess = get_session(user_id)
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
            continue

        if cmd == "catalog":
            try:
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
            continue

        # Load session — only pass through if it's an active clarification turn
        raw_session = get_session(user_id)
        session_state = raw_session
        if session_state and session_state.get("status") not in (
            "NEEDS_CLARIFICATION",
            "AWAITING_CAPABILITY",
        ):
            session_state = None

        try:
            result = handle_message(
                user_id=user_id,
                text=user_text,
                domain=domain,
                timezone=timezone,
                organization_id=org_id,
                session_state=session_state,
            )
        except Exception as e:
            import traceback
            cause = e.__cause__ or e.__context__
            cause_msg = f" (caused by: {cause})" if cause else ""
            print(f"\n[error] {type(e).__name__}: {e}{cause_msg}")
            if "Connection refused" in str(e) or "Connection refused" in str(cause):
                internal_url = os.getenv("INTERNAL_API_BASE_URL", "http://localhost:3000")
                print(f"  Check: is the internal API running at {internal_url}?")
                print(f"  Check: is Luma (NLU) running at {luma_url}?")
            if debug:
                traceback.print_exc()
            continue

        outcome = result.get("outcome") or {}
        response_text = _extract_text(result)

        if debug or not (result.get("text") or outcome.get("text")):
            # Dump full result — strip large internal fields
            safe = {k: v for k, v in result.items() if not k.startswith("_")}
            print("\n--- RESULT ---")
            try:
                print(json.dumps(safe, indent=2, default=str))
            except Exception:
                print(repr(safe))
            print("--- END ---")

        _persist_session(result, outcome, session_state, raw_session, user_id, user_text)

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
