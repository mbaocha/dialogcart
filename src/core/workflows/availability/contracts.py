"""Stable public contracts for availability cache, presentation, and selection.

Internal grouping, indexing, and traversal details must not appear here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict


class AvailabilityCache(TypedDict, total=False):
    """Trusted result of the last successful availability search."""

    type: str
    status: str
    slots: List[Dict[str, Any]]
    fingerprint: Optional[str]
    search_date: Optional[str]
    time_resolution: Dict[str, Any]


BrowseDirection = Literal["next", "previous"]
BrowseAxisHint = Literal["any", "time", "date"]


class BrowseIntent(TypedDict, total=False):
    """User browse request — direction and optional axis, no cursor mechanics."""

    direction: BrowseDirection
    axis_hint: Optional[BrowseAxisHint]


class BrowseHints(TypedDict, total=False):
    """Public browse metadata for rendering (no cursor/index internals)."""

    has_more_times: bool
    has_previous_times: bool
    has_next_date: bool
    has_previous_date: bool
    has_more_any: bool
    has_previous_any: bool
    suggested_next: Optional[Literal["show more", "next day"]]
    suggested_previous: Optional[Literal["go back", "previous day"]]
    # Legacy fields retained for older session/tests readers.
    more_count: int
    total_unique: int


class PresentedAvailability(TypedDict, total=False):
    """Currently visible offers used for discovery and ambiguous selection."""

    search_date: Optional[str]
    slots: List[Dict[str, Any]]
    times: List[str]
    more_count: int
    total_unique: int
    fingerprint: Optional[str]
    browse_hints: BrowseHints
    browse_status: Optional[str]


SelectionStatus = Literal["matched", "ambiguous", "not_found", "criteria_changed"]
SelectionSource = Literal["presentation", "cache"]


class SelectionResolution(TypedDict, total=False):
    """Result of interpreting a user's slot choice for planner consumption."""

    status: SelectionStatus
    source: Optional[SelectionSource]
    slot: Optional[Dict[str, Any]]
    reason_code: str
    bind_result: Optional[Dict[str, Any]]


class BrowseProjection(TypedDict, total=False):
    """Result of advancing presentation for a browse intent."""

    presented: PresentedAvailability
    moved: bool
    reason_code: str
