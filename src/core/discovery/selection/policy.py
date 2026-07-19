"""SelectionPolicy — domain-owned matching."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Protocol, Sequence

from core.discovery.models import SelectionRequest


class SelectionPolicy(Protocol):
    """Lightweight domain adapter for selection matching."""

    def is_explicit(self, request: SelectionRequest) -> bool:
        """Return True when ``request`` is a complete explicit choice."""

    def find_matches(
        self,
        items: Sequence[Mapping[str, Any]],
        request: SelectionRequest,
    ) -> List[Dict[str, Any]]:
        """Return items that match ``request`` within ``items``."""
