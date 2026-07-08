"""Helpers for attaching decision/invariant traces to E2E assertion failures."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.tracing.decision_trace import get_last_decision_trace, is_decision_trace_enabled
from core.tracing.formatters import format_decision_failure_context
from core.tracing.views import format_trace_view_text
from core.tracing.invariant_trace import (
    format_invariant_summary,
    get_last_trace_summary,
    is_trace_enabled,
)

_stashed_decision_trace: Optional[Dict[str, Any]] = None


def stash_decision_trace_from_body(body: Optional[Dict[str, Any]] = None) -> None:
    """Remember the latest API decision trace for pytest display hooks."""
    global _stashed_decision_trace
    if isinstance(body, dict) and body.get("decision_trace"):
        _stashed_decision_trace = body["decision_trace"]


def pop_stashed_decision_trace() -> Optional[Dict[str, Any]]:
    """Return and clear the stashed decision trace (pytest display hook)."""
    global _stashed_decision_trace
    trace = _stashed_decision_trace
    _stashed_decision_trace = None
    return trace


def maybe_print_decision_trace(body: Optional[Dict[str, Any]] = None) -> None:
    """Print human-readable trace when DIALOGCART_TRACE_SHOW is enabled."""
    if os.getenv("DIALOGCART_TRACE_SHOW", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    text = trace_summary_from_body(body)
    if text:
        print(f"\n{text}\n", flush=True)


def trace_summary_from_body(body: Optional[Dict[str, Any]] = None) -> str:
    """Build failure context from decision trace, falling back to invariant trace."""
    if body and isinstance(body, dict):
        if body.get("decision_trace_text"):
            return str(body["decision_trace_text"])
        if body.get("decision_trace"):
            view = body.get("trace_view") or "summary"
            text = format_trace_view_text(body["decision_trace"], view)
            if text:
                return text
            text = format_decision_failure_context(body=body)
            if text:
                return text

    if is_decision_trace_enabled():
        text = format_decision_failure_context(trace=get_last_decision_trace())
        if text:
            return text

    if not is_trace_enabled():
        return ""

    summary = None
    if body and isinstance(body, dict):
        summary = body.get("invariant_trace") or body.get("_invariant_trace")
    if not summary:
        summary = get_last_trace_summary()
    return format_invariant_summary(summary)


def augment_assertion_message(
    message: str,
    *,
    body: Optional[Dict[str, Any]] = None,
) -> str:
    trace_text = trace_summary_from_body(body)
    if not trace_text:
        return message
    return f"{message}\n\n{trace_text}"
