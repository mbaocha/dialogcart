"""Shared fixtures for core REST API tests."""

import logging
import os

import pytest
from fastapi.testclient import TestClient

from core.api.main import app
from core.tests.harness.recording_luma_client import RECACHE_ENV
from core.tests.harness.test_clock import LUMA_TEST_NOW_ENV, TEST_NOW_ISO
from core.tracing.invariant_trace import TRACE_ENV_VAR

# Register booking conversation fixtures for all e2e test modules.
pytest_plugins = ["core.tests.e2e.framework.fixtures"]


def pytest_addoption(parser):
    parser.addoption(
        "--recache-luma",
        action="store_true",
        default=False,
        help=(
            "Force live Luma /resolve and overwrite E2E recordings "
            f"(sets {RECACHE_ENV}=1)"
        ),
    )


def pytest_configure(config):
    """Show orchestration trace logs (logger.error) during E2E runs."""
    config.addinivalue_line(
        "markers",
        "live_luma: integration test that requires a reachable Live Luma /resolve service",
    )
    # Pin relative-date resolution for any live /resolve during E2E (cache miss
    # or --recache-luma). RecordingLumaClient also defaults to TEST_NOW_ISO;
    # this covers production LumaClient env fallback used by the inner client.
    os.environ.setdefault(LUMA_TEST_NOW_ENV, TEST_NOW_ISO)
    if config.getoption("--recache-luma"):
        os.environ[RECACHE_ENV] = "1"
    if config.getoption("--trace-invariants", default=False):
        os.environ[TRACE_ENV_VAR] = "1"
    if os.getenv(TRACE_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}:
        os.environ.setdefault(TRACE_ENV_VAR, "1")
    if config.option.capture == "fd":
        config.option.capture = "no"
    logging.basicConfig(level=logging.ERROR, format="%(message)s", force=True)
    for logger_name in (
        "core",
        "core.planning",
        "core.turn_log",
        "httpx",
        "httpcore",
    ):
        logging.getLogger(logger_name).setLevel(logging.ERROR)


@pytest.fixture(autouse=True)
def _enable_invariant_tracing(monkeypatch):
    """Enable tracing for E2E tests when DIALOGCART_TRACE_INVARIANTS is set."""
    if os.getenv(TRACE_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}:
        monkeypatch.setenv(TRACE_ENV_VAR, "1")


@pytest.fixture
def api_client():
    return TestClient(app)
