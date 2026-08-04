"""Canonical reference clock for E2E and NLU test bootstrap.

Single source of truth for deterministic relative-date resolution.

Production processes must not import this module to set the wall clock.
Test/E2E paths and ``run.py`` (NLU test bootstrap) consume these values.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Canonical E2E / NLU-test reference instant (UTC).
TEST_NOW: datetime = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)

# ISO-8601 form used for LUMA_TEST_NOW and /resolve test_now.
TEST_NOW_ISO: str = TEST_NOW.strftime("%Y-%m-%dT%H:%M:%SZ")

# Env var name recognised by NLU (``pipeline._resolve_now``) and LumaClient.
LUMA_TEST_NOW_ENV: str = "LUMA_TEST_NOW"

# E2E alias — Core fixtures and availability mocks use this name historically.
FROZEN_TIME: datetime = TEST_NOW
