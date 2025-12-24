#!/usr/bin/env python3
"""
Send a single request to the Luma /resolve API and print the JSON response.

Usage example (PowerShell-safe):
python -m luma.test_single --sentence "book premium haircut tomorrow 9am" --domain service --api-base http://localhost:9001/resolve --tenant-domain your-tenant.example --tenant-context "{\"aliases\":{\"premium haircut\":\"haircut\"}}"
"""

import argparse
import json
import sys
from typing import Any, Dict, Optional

import requests


def _parse_tenant_context(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"Invalid tenant_context JSON: {exc}\n")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a single Luma request and print JSON response.")
    parser.add_argument("--sentence", required=True,
                        help="Input text to resolve.")
    parser.add_argument("--domain", default="service",
                        choices=["service", "reservation"])
    parser.add_argument(
        "--api-base", default="http://localhost:9001/resolve", help="Luma resolve endpoint URL.")
    parser.add_argument("--tenant-context", dest="tenant_context",
                        help="Tenant context JSON string.")
    parser.add_argument("--user-id", dest="user_id", default="user123",
                        help="User identifier to send in the request.")
    args = parser.parse_args()

    payload: Dict[str, Any] = {
        "text": args.sentence,
        "domain": args.domain,
        "user_id": args.user_id,
    }
    tc = _parse_tenant_context(args.tenant_context)
    if tc is not None:
        payload["tenant_context"] = tc

    try:
        resp = requests.post(args.api_base, json=payload, timeout=30)
    except requests.RequestException as exc:
        sys.stderr.write(f"Request failed: {exc}\n")
        sys.exit(1)

    status_ok = 200 <= resp.status_code < 300

    try:
        data = resp.json()
    except ValueError:
        sys.stderr.write(
            f"Response not JSON (status {resp.status_code}):\n{resp.text}\n")
        sys.exit(1)

    if not status_ok:
        sys.stderr.write(f"Request failed with status {resp.status_code}:\n")
        sys.stderr.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        sys.exit(1)

    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
