"""
FAQ API Client

Thin HTTP client for the /api/internal/faq/retrieve endpoint.
Commerce returns chunks + structured_context; Core is the only RAG renderer.
"""

from typing import Any, Dict, Optional

from core.orchestration.clients.base_client import BaseClient
from core.orchestration.errors import UpstreamError


class FaqClient(BaseClient):
    """HTTP client for FAQ retrieval internal API."""

    def __init__(self, base_url: Optional[str] = None):
        super().__init__(
            base_url=base_url,
            env_var="INTERNAL_API_BASE_URL",
            default_url="http://localhost:3000",
        )

    def retrieve(
        self,
        organization_id: int,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.75,
    ) -> Dict[str, Any]:
        """
        Retrieve FAQ chunks matching query from commerce.

        Args:
            organization_id: Organization identifier
            query: Natural-language search query (resolved by caller)
            top_k: Hint for maximum chunks to return
            min_score: Hint for minimum relevance score

        Returns:
            dict with keys: chunks (list), structured_context (dict), no_hit (bool)

        Raises:
            UpstreamError: On network failures, HTTP errors, or unexpected response shape
        """
        payload: Dict[str, Any] = {
            "organization_id": organization_id,
            "query": query,
        }
        raw = self._request("POST", "/api/internal/faq/retrieve", json=payload)

        # Unwrap { success, data } envelope
        if isinstance(raw, dict) and raw.get("success") and "data" in raw:
            data = raw["data"]
        elif isinstance(raw, dict) and "chunks" in raw:
            # Already unwrapped (direct data object)
            data = raw
        else:
            raise UpstreamError(
                f"Unexpected FAQ retrieve response shape: {str(raw)[:200]}"
            )

        return {
            "chunks": data.get("chunks") or [],
            "structured_context": data.get("structured_context") or {},
            "no_hit": bool(data.get("no_hit", False)),
        }
