"""Pytest hooks for decision/invariant trace attachment on test failures."""

from __future__ import annotations

import os

import pytest

from core.tracing.decision_trace import (
    TRACE_ENV_VAR as DECISION_TRACE_ENV_VAR,
    get_last_decision_trace,
    is_decision_trace_enabled,
)
from core.tracing.formatters import format_decision_failure_context, format_decision_summary
from core.tests.e2e.framework.trace_helpers import pop_stashed_decision_trace
from core.tracing.invariant_trace import (
    TRACE_ENV_VAR,
    format_invariant_summary,
    get_last_trace_summary,
    is_trace_enabled,
)


SHOW_DECISION_TRACE_ENV = "DIALOGCART_TRACE_SHOW"


def pytest_configure(config):
    if os.getenv(TRACE_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}:
        pass
    elif config.getoption("--trace-invariants", default=False):
        os.environ[TRACE_ENV_VAR] = "1"

    if os.getenv(DECISION_TRACE_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}:
        return
    if config.getoption("--trace-decisions", default=False):
        os.environ[DECISION_TRACE_ENV_VAR] = "1"


def pytest_addoption(parser):
    parser.addoption(
        "--trace-invariants",
        action="store_true",
        default=False,
        help="Enable DialogCart invariant tracing (sets DIALOGCART_TRACE_INVARIANTS=1)",
    )
    parser.addoption(
        "--trace-decisions",
        action="store_true",
        default=False,
        help="Enable DialogCart decision tracing (sets DIALOGCART_TRACE_DECISIONS=1)",
    )
    parser.addoption(
        "--show-decision-trace",
        action="store_true",
        default=False,
        help="Print human-readable decision trace summary after each test (also DIALOGCART_TRACE_SHOW=1)",
    )


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _should_show_decision_trace(config) -> bool:
    if config.getoption("--show-decision-trace", default=False):
        return True
    return _truthy_env(SHOW_DECISION_TRACE_ENV)


def _decision_trace_display_text() -> str:
    stashed = pop_stashed_decision_trace()
    if stashed:
        text = format_decision_failure_context(body={"decision_trace": stashed})
        if text:
            return text

    if not is_decision_trace_enabled():
        return ""
    formatted = format_decision_failure_context(trace=get_last_decision_trace())
    if formatted:
        return formatted
    summary = format_decision_summary(get_last_decision_trace())
    return summary or ""


def _write_decision_trace_summary(item, text: str) -> None:
    if not text:
        return
    terminal = item.config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.write_line("")
        for line in text.splitlines():
            terminal.write_line(line)
        terminal.write_line("")
        return
    # Fallback when terminal reporter is unavailable
    print(f"\n{text}\n", flush=True)


def _failure_trace_text() -> str:
    text = _decision_trace_display_text()
    if text:
        return text

    if not is_trace_enabled():
        return ""

    summary = get_last_trace_summary()
    if not summary:
        return ""
    return format_invariant_summary(summary) or ""


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return

    if _should_show_decision_trace(item.config):
        _write_decision_trace_summary(item, _decision_trace_display_text())

    if not report.failed:
        return

    formatted = _failure_trace_text()
    if not formatted:
        return

    if report.longrepr is None:
        report.longrepr = formatted
    else:
        report.longrepr = f"{report.longrepr}\n\n{formatted}"
