"""RAG intent handler — resolves FAQ query, returns raw facts for core to render."""

import logging
from typing import Any, Dict, Optional

from core.adapters.errors import UpstreamError
from extensions.handlers.base import HandlerResponse, IntentHandler
from extensions.handlers.clients.faq_client import FaqClient
from extensions.handlers.query_resolution import resolve_faq_query

logger = logging.getLogger(__name__)


class RagAdapter(IntentHandler):
    def __init__(self, faq_client: Optional[FaqClient] = None):
        self._faq_client = faq_client

    @property
    def name(self) -> str:
        return "rag"

    def handle(self, context: Dict[str, Any]) -> HandlerResponse:
        organization_id = context.get("organization_id")
        if not organization_id:
            logger.warning("RagAdapter: missing organization_id in context")
            return HandlerResponse(
                render_instruction="Tell the user you're unable to retrieve that information right now.",
                facts={"error": "missing_organization_id"},
            )

        query = resolve_faq_query(
            search_query=context.get("search_query"),
            user_text=context.get("user_text", ""),
            session=context.get("session"),
        )

        client = self._faq_client or FaqClient()
        try:
            data = client.retrieve(organization_id=int(organization_id), query=query)
        except UpstreamError as e:
            logger.error("RagAdapter: FAQ retrieve failed: %s", e)
            return HandlerResponse(
                render_instruction="Tell the user you're unable to retrieve that information right now.",
                facts={"error": "upstream_error"},
            )

        return HandlerResponse(
            render_instruction=(
                f"Answer the user's question '{query}' using Business Knowledge "
                "and Supporting Evidence. "
                "If no relevant Supporting Evidence is available, use Business Knowledge. "
                "Respond like a knowledgeable front-desk colleague: help the customer "
                "understand and compare options, include decision-useful details when "
                "relevant, and naturally invite the next step in this conversation. "
                "Share contact details only if they explicitly asked for them. "
                "Do not encourage calling the business when you can continue helping here."
            ),
            facts={
                "query": query,
                "chunks": data["chunks"],
                "structured_context": data["structured_context"],
                "no_hit": data["no_hit"],
                "chunk_ids": [c["id"] for c in data["chunks"] if "id" in c],
            },
        )
