"""Selector — own Discovery selection resolution."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.discovery.models import (
    PresentedWindow,
    SelectionRequest,
    SelectionResult,
    SelectionSource,
    TrustedResult,
)
from core.discovery.selection.policy import SelectionPolicy


class Selector:
    """Resolve a SelectionRequest against presented and/or trusted results.

    Ambiguous choices resolve only against PresentedWindow.
    Explicit choices may resolve against TrustedResult.
    Does not search, browse, book, or update session state.
    """

    def __init__(self, policy: SelectionPolicy) -> None:
        self._policy = policy

    def resolve(
        self,
        request: SelectionRequest,
        *,
        window: PresentedWindow,
        trusted: Optional[TrustedResult] = None,
    ) -> SelectionResult:
        """Produce a SelectionResult for ``request``."""
        if self._policy.is_explicit(request):
            return self._resolve_explicit(request, window=window, trusted=trusted)
        return self._resolve_from_items(
            list(window.get("items") or []),
            request,
            source="presentation",
        )

    def _resolve_explicit(
        self,
        request: SelectionRequest,
        *,
        window: PresentedWindow,
        trusted: Optional[TrustedResult],
    ) -> SelectionResult:
        if trusted is not None:
            expected = request.get("search_identity")
            actual = trusted.get("search_identity")
            if expected is not None and actual is not None and expected != actual:
                return SelectionResult(
                    status="criteria_changed",
                    source=None,
                    item=None,
                    reason_code="search_identity_mismatch",
                )
            if trusted.get("trusted"):
                return self._resolve_from_items(
                    list(trusted.get("items") or []),
                    request,
                    source="search",
                )

        return self._resolve_from_items(
            list(window.get("items") or []),
            request,
            source="presentation",
        )

    def _resolve_from_items(
        self,
        items: List[Dict[str, Any]],
        request: SelectionRequest,
        *,
        source: SelectionSource,
    ) -> SelectionResult:
        matches = self._policy.find_matches(items, request)
        if not matches:
            return SelectionResult(
                status="not_found",
                source=source,
                item=None,
                reason_code="no_match",
            )
        if len(matches) == 1:
            return SelectionResult(
                status="matched",
                source=source,
                item=matches[0],
                reason_code="unique_match",
            )
        return SelectionResult(
            status="ambiguous",
            source=source,
            item=None,
            reason_code="multiple_matches",
        )
