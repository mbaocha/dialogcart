"""CLI for inspecting saved DialogCart response JSON decision traces."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.tracing.formatters import decision_trace_to_mermaid, format_decision_summary

_VALID_CATEGORIES = ("routing", "inference", "presentation", "persistence")


def _load_payload(path: Optional[str]) -> Dict[str, Any]:
    if path and path != "-":
        raw = Path(path).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit("Expected a JSON object at the top level")
    return data


def _extract_trace(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    trace = payload.get("decision_trace")
    if isinstance(trace, dict):
        return trace
    if payload.get("records") is not None:
        return payload
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print a human-readable summary of a DialogCart decision trace.",
    )
    parser.add_argument(
        "json_file",
        nargs="?",
        default="-",
        help="Saved API response JSON file (default: stdin)",
    )
    parser.add_argument(
        "--mermaid",
        action="store_true",
        help="Emit a Mermaid flowchart instead of the text summary",
    )
    parser.add_argument(
        "--category",
        action="append",
        choices=_VALID_CATEGORIES,
        metavar="CATEGORY",
        help="Filter to decisions in a category (repeatable)",
    )
    args = parser.parse_args(argv)

    payload = _load_payload(args.json_file)
    trace = _extract_trace(payload)
    if not trace:
        print("No decision_trace found in payload", file=sys.stderr)
        return 1

    if args.mermaid:
        print(decision_trace_to_mermaid(trace, categories=args.category))
    else:
        print(format_decision_summary(trace, categories=args.category))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
