"""Domain-neutral Discovery public models.

These describe conversation semantics for Search → Navigator → Selector.
They are not tied to any business domain.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict


class SearchRequest(TypedDict, total=False):
    """Opaque search request. Domains supply criteria meaning."""

    criteria: Dict[str, Any]


class TrustedResult(TypedDict, total=False):
    """Trusted outcome of a search for the current search identity.

    Search owns trust. Downstream components treat this as authoritative
    until search identity changes and trust must be re-established.
    """

    items: List[Dict[str, Any]]
    search_identity: Optional[str]
    trusted: bool


BrowseDirection = Literal["next", "previous"]


class BrowseIntent(TypedDict, total=False):
    """User request to move within a presented result set.

    Direction is generic. Optional ``axis_hint`` is an opaque domain hint;
    Discovery does not interpret it.
    """

    direction: BrowseDirection
    axis_hint: Optional[str]


class PresentedWindow(TypedDict, total=False):
    """Currently navigable subset shown to the user.

    Derived from TrustedResult by the Navigator. Used for discovery and
    ambiguous selection. Does not own search trust or booking truth.
    """

    items: List[Dict[str, Any]]
    search_identity: Optional[str]
    browse_hints: Dict[str, Any]


class SelectionRequest(TypedDict, total=False):
    """Opaque selection request from the current turn.

    Optional ``search_identity`` lets Selector detect criteria drift against
    a TrustedResult without domain knowledge.
    """

    criteria: Dict[str, Any]
    search_identity: Optional[str]


SelectionStatus = Literal["matched", "ambiguous", "not_found", "criteria_changed"]
SelectionSource = Literal["presentation", "search"]


class SelectionResult(TypedDict, total=False):
    """Result of resolving a user choice against presented or trusted results.

    Consumed by planning; Selector does not decide the next conversation action.
    """

    status: SelectionStatus
    source: Optional[SelectionSource]
    item: Optional[Dict[str, Any]]
    reason_code: str
