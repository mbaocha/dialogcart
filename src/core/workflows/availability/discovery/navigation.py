"""AvailabilityNavigationPolicy — NavigationPolicy adapter for availability."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.discovery.models import BrowseIntent, PresentedWindow, TrustedResult
from core.workflows.availability.contracts import AvailabilityCache, BrowseProjection
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

    Presentation span is derived from planning criteria passed into this policy —
    never from persisted cache presentation flags.
    """

    def __init__(
        self,
        *,
        page_size: int = DEFAULT_MAX_TIMES,
        search_date: Optional[str] = None,
        cache_template: Optional[AvailabilityCache] = None,
        slots: Optional[Dict[str, Any]] = None,
        date_proposal: Optional[Dict[str, Any]] = None,
        fingerprint_slots: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._page_size = page_size
        self._search_date = search_date
        self._cache_template: Dict[str, Any] = (
            dict(cache_template) if isinstance(cache_template, dict) else {}
        )
        self._slots = dict(slots) if isinstance(slots, dict) else None
        self._date_proposal = (
            dict(date_proposal) if isinstance(date_proposal, dict) else None
        )
        self._fingerprint_slots = (
            dict(fingerprint_slots) if isinstance(fingerprint_slots, dict) else None
        )
        self.last_projection: Optional[BrowseProjection] = None

    def _cache_from_trusted(self, trusted: TrustedResult) -> AvailabilityCache:
        return trusted_to_cache(
            trusted,
            template=self._cache_template,  # type: ignore[arg-type]
            search_date=self._search_date or self._cache_template.get("search_date"),
        )

    def initial_window(self, trusted: TrustedResult) -> PresentedWindow:
        cache = self._cache_from_trusted(trusted)
        presented = build_initial_presentation(
            cache,
            page_size=self._page_size,
            search_date=self._search_date or cache.get("search_date"),
            slots=self._slots,
            date_proposal=self._date_proposal,
            fingerprint_slots=self._fingerprint_slots,
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
        cache = self._cache_from_trusted(trusted)
        presented = window_to_presented(current)
        avail_intent = to_availability_browse_intent(intent)
        projection = advance_presentation(
            cache,
            presented,
            avail_intent,
            page_size=self._page_size,
            slots=self._slots,
            date_proposal=self._date_proposal,
            fingerprint_slots=self._fingerprint_slots,
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
