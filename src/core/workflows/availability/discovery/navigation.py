"""AvailabilityNavigationPolicy — NavigationPolicy adapter for availability."""

from __future__ import annotations

from typing import Optional

from core.discovery.models import BrowseIntent, PresentedWindow, TrustedResult
from core.workflows.availability.contracts import BrowseProjection
from core.workflows.availability.discovery.mapping import (
    presented_to_window,
    to_availability_browse_intent,
    trusted_to_cache,
    window_to_presented,
)
from core.workflows.availability.presentation import (
    DEFAULT_MAX_TIMES,
    advance_presentation,
    build_initial_presentation,
)


class AvailabilityNavigationPolicy:
    """Adapt availability presentation algorithms to Discovery Navigator.

    Reuses ``build_initial_presentation`` and ``advance_presentation``.
    On exhaustion, refreshes window content in place (same object) so Navigator
    ``last_moved`` stays false while browse_status/hints remain accurate.
    """

    def __init__(
        self,
        *,
        page_size: int = DEFAULT_MAX_TIMES,
        search_date: Optional[str] = None,
    ) -> None:
        self._page_size = page_size
        self._search_date = search_date
        self.last_projection: Optional[BrowseProjection] = None

    def initial_window(self, trusted: TrustedResult) -> PresentedWindow:
        cache = trusted_to_cache(trusted, search_date=self._search_date)
        presented = build_initial_presentation(
            cache,
            page_size=self._page_size,
            search_date=self._search_date or cache.get("search_date"),
        )
        self.last_projection = {
            "presented": presented,
            "moved": True,
            "reason_code": "initial",
        }
        return presented_to_window(presented)

    def advance(
        self,
        trusted: TrustedResult,
        current: PresentedWindow,
        intent: BrowseIntent,
    ) -> PresentedWindow:
        cache = trusted_to_cache(trusted, search_date=self._search_date)
        presented = window_to_presented(current)
        avail_intent = to_availability_browse_intent(intent)
        projection = advance_presentation(
            cache,
            presented,
            avail_intent,
            page_size=self._page_size,
        )
        self.last_projection = projection
        next_presented = projection.get("presented") or presented
        refreshed = presented_to_window(next_presented)
        if not projection.get("moved"):
            # Preserve object identity for Navigator exhaustion detection while
            # applying browse_status / hint updates from the projection.
            if isinstance(current, dict):
                current.clear()
                current.update(refreshed)
                return current
            return refreshed
        return refreshed
