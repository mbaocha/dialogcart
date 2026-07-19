"""SearchProvider — domain-owned search execution.

Discovery decides when a provider runs. The provider decides how.
"""

from __future__ import annotations

from typing import Any, Dict, List, Protocol

from core.discovery.models import SearchRequest


class SearchProvider(Protocol):
    """Lightweight domain adapter for search identity and retrieval."""

    def identity(self, request: SearchRequest) -> str:
        """Return a stable search identity for ``request``."""

    def search(self, request: SearchRequest) -> List[Dict[str, Any]]:
        """Execute a domain search and return opaque items."""
