"""Small injectable UTC clock for Core-owned freshness decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class CoreClock(Protocol):
    def now(self) -> datetime: ...


class SystemCoreClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


_clock: CoreClock = SystemCoreClock()


def get_core_clock() -> CoreClock:
    return _clock


def set_core_clock(clock: CoreClock) -> None:
    """Inject a deterministic clock; production startup need not call this."""
    global _clock
    _clock = clock


def reset_core_clock() -> None:
    global _clock
    _clock = SystemCoreClock()


def utc_now() -> datetime:
    value = get_core_clock().now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")

