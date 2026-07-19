"""Search — own the Discovery search lifecycle."""

from __future__ import annotations

from typing import Optional

from core.discovery.models import SearchRequest, TrustedResult
from core.discovery.search.provider import SearchProvider


class Search:
    """Obtain and validate TrustedResult via a domain SearchProvider.

    Owns search identity comparison, reuse decisions, and provider invocation.
    Does not present, navigate, or select.
    """

    def search(
        self,
        request: SearchRequest,
        provider: SearchProvider,
        *,
        existing: Optional[TrustedResult] = None,
    ) -> TrustedResult:
        """Return a TrustedResult, reusing ``existing`` when identity still matches."""
        identity = provider.identity(request)
        if existing is not None and self._is_reusable(existing, identity):
            return existing

        items = provider.search(request)
        return TrustedResult(
            items=list(items),
            search_identity=identity,
            trusted=True,
        )

    @staticmethod
    def _is_reusable(existing: Optional[TrustedResult], identity: str) -> bool:
        if existing is None:
            return False
        if not existing.get("trusted"):
            return False
        return existing.get("search_identity") == identity
