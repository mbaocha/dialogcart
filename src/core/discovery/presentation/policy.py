"""NavigationPolicy — domain-owned grouping and ordering."""

from __future__ import annotations

from typing import Protocol

from core.discovery.models import BrowseIntent, PresentedWindow, TrustedResult


class NavigationPolicy(Protocol):
    """Lightweight domain adapter for window construction and movement."""

    def initial_window(self, trusted: TrustedResult) -> PresentedWindow:
        """Build the first PresentedWindow over ``trusted``."""

    def advance(
        self,
        trusted: TrustedResult,
        current: PresentedWindow,
        intent: BrowseIntent,
    ) -> PresentedWindow:
        """Move from ``current`` according to ``intent``.

        When no further movement is possible, return ``current`` (or an
        equivalent window). Navigator treats an unchanged window as exhausted.
        """
