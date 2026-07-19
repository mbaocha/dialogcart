"""Map between availability domain contracts and Discovery public models.

Preserves availability-specific fields (cursor, search_date, browse_status)
for round-trips through PresentedWindow without modifying Discovery.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.discovery.models import (
    BrowseIntent as DiscoveryBrowseIntent,
    PresentedWindow,
    SelectionResult,
    TrustedResult,
)
from core.workflows.availability.contracts import (
    AvailabilityCache,
    BrowseIntent as AvailabilityBrowseIntent,
    PresentedAvailability,
    SelectionResolution,
    SelectionSource,
)
from core.workflows.availability.presentation import normalize_search_date

# Private bag inside PresentedWindow.browse_hints for lossless round-trip.
_PRESENTED_BAG = "_availability_presented"


def cache_to_trusted(cache: Optional[AvailabilityCache]) -> Optional[TrustedResult]:
    """Project AvailabilityCache into a Discovery TrustedResult."""
    if not isinstance(cache, dict):
        return None
    slots = cache.get("slots")
    return TrustedResult(
        items=list(slots) if isinstance(slots, list) else [],
        search_identity=cache.get("fingerprint"),
        trusted=True,
    )


def trusted_to_cache(
    trusted: TrustedResult,
    *,
    template: Optional[AvailabilityCache] = None,
    search_date: Optional[str] = None,
) -> AvailabilityCache:
    """Rebuild AvailabilityCache from TrustedResult, preserving template metadata."""
    cache: Dict[str, Any] = dict(template) if isinstance(template, dict) else {}
    items = trusted.get("items")
    cache["slots"] = list(items) if isinstance(items, list) else []
    identity = trusted.get("search_identity")
    if identity is not None:
        cache["fingerprint"] = identity
    resolved_date = search_date or cache.get("search_date")
    if not resolved_date and cache["slots"]:
        first = cache["slots"][0]
        if isinstance(first, dict):
            start = first.get("starts_at") or first.get("start")
            if isinstance(start, str) and len(start) >= 10:
                resolved_date = normalize_search_date(start)
    if resolved_date:
        cache["search_date"] = resolved_date
    cache.setdefault("type", "availability")
    cache.setdefault("status", "ok")
    return cache  # type: ignore[return-value]


def presented_to_window(presented: PresentedAvailability) -> PresentedWindow:
    """Project PresentedAvailability into PresentedWindow (lossless via hints bag)."""
    hints: Dict[str, Any] = dict(presented.get("browse_hints") or {})
    hints[_PRESENTED_BAG] = dict(presented)
    return PresentedWindow(
        items=list(presented.get("slots") or []),
        search_identity=presented.get("fingerprint"),
        browse_hints=hints,
    )


def window_to_presented(window: PresentedWindow) -> PresentedAvailability:
    """Restore PresentedAvailability from PresentedWindow."""
    hints = window.get("browse_hints") if isinstance(window.get("browse_hints"), dict) else {}
    stored = hints.get(_PRESENTED_BAG)
    if isinstance(stored, dict):
        return stored  # type: ignore[return-value]

    public_hints = {k: v for k, v in hints.items() if k != _PRESENTED_BAG}
    presented: Dict[str, Any] = {
        "slots": list(window.get("items") or []),
        "browse_hints": public_hints,
    }
    identity = window.get("search_identity")
    if identity is not None:
        presented["fingerprint"] = identity
    return presented  # type: ignore[return-value]


def to_discovery_browse_intent(
    intent: AvailabilityBrowseIntent,
) -> DiscoveryBrowseIntent:
    """Copy availability BrowseIntent into Discovery BrowseIntent."""
    discovery: Dict[str, Any] = {}
    direction = intent.get("direction")
    if direction is not None:
        discovery["direction"] = direction
    axis = intent.get("axis_hint")
    if axis is not None:
        discovery["axis_hint"] = axis
    return discovery  # type: ignore[return-value]


def to_availability_browse_intent(
    intent: DiscoveryBrowseIntent,
) -> AvailabilityBrowseIntent:
    """Copy Discovery BrowseIntent into availability BrowseIntent."""
    availability: Dict[str, Any] = {}
    direction = intent.get("direction")
    if direction is not None:
        availability["direction"] = direction
    axis = intent.get("axis_hint")
    if axis in (None, "any", "time", "date"):
        availability["axis_hint"] = axis
    elif axis is not None:
        availability["axis_hint"] = "any"
    return availability  # type: ignore[return-value]


def selection_result_to_resolution(
    result: SelectionResult,
    *,
    reason_code: Optional[str] = None,
    bind_result: Optional[Dict[str, Any]] = None,
) -> SelectionResolution:
    """Map Discovery SelectionResult to availability SelectionResolution."""
    source = result.get("source")
    avail_source: Optional[SelectionSource]
    if source == "search":
        avail_source = "cache"
    elif source == "presentation":
        avail_source = "presentation"
    else:
        avail_source = None

    return SelectionResolution(
        status=result.get("status") or "not_found",
        source=avail_source,
        slot=result.get("item"),
        reason_code=reason_code or result.get("reason_code") or "no_match",
        bind_result=bind_result,
    )
