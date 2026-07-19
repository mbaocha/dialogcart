"""Discovery — domain-neutral Search → Navigator → Selector engine.

Public surface: Search, Navigator, Selector, and the shared models.
Domain behaviour plugs in via SearchProvider, NavigationPolicy, and
SelectionPolicy. Availability remains outside this package until Phase 3.
"""

from core.discovery.models import (
    BrowseIntent,
    PresentedWindow,
    SearchRequest,
    SelectionRequest,
    SelectionResult,
    TrustedResult,
)
from core.discovery.presentation import NavigationPolicy, Navigator
from core.discovery.search import Search, SearchProvider
from core.discovery.selection import SelectionPolicy, Selector

__all__ = [
    "BrowseIntent",
    "NavigationPolicy",
    "Navigator",
    "PresentedWindow",
    "Search",
    "SearchProvider",
    "SearchRequest",
    "SelectionPolicy",
    "SelectionRequest",
    "SelectionResult",
    "Selector",
    "TrustedResult",
]
