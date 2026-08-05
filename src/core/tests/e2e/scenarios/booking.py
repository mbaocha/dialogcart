"""Compatibility shim ? booking scenarios live in ``core.tests.e2e.booking``."""

from __future__ import annotations

from core.tests.e2e.booking import SCENARIOS  # noqa: F401
from core.tests.e2e.booking._helpers import (  # noqa: F401
    _assert_booking_created,
    _assert_no_booking,
)

__all__ = ["SCENARIOS", "_assert_booking_created", "_assert_no_booking"]
