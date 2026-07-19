"""AvailabilityProvider — SearchProvider adapter for availability search."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.discovery.models import SearchRequest
from core.workflows.availability.fingerprint import compute_availability_fingerprint

# Domain search callable: opaque criteria → offer slot dicts.
ExecuteSearch = Callable[[Dict[str, Any]], List[Dict[str, Any]]]


class AvailabilityProvider:
    """Adapt availability fingerprint + search execution to Discovery Search.

    ``execute_search`` is injected so this adapter does not own tool dispatch.
    Identity reuses ``compute_availability_fingerprint``.
    """

    def __init__(self, *, execute_search: Optional[ExecuteSearch] = None) -> None:
        self._execute_search = execute_search

    def identity(self, request: SearchRequest) -> str:
        criteria = request.get("criteria") if isinstance(request.get("criteria"), dict) else {}
        fingerprint = compute_availability_fingerprint(criteria)
        return fingerprint or ""

    def search(self, request: SearchRequest) -> List[Dict[str, Any]]:
        criteria = request.get("criteria") if isinstance(request.get("criteria"), dict) else {}
        if self._execute_search is None:
            raise RuntimeError(
                "AvailabilityProvider.search requires execute_search "
                "(inject the domain search callable at construction)"
            )
        items = self._execute_search(dict(criteria))
        return list(items) if items else []
