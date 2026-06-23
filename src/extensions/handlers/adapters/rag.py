"""RAG intent handler — stub implementation."""
from typing import Any, Dict

from extensions.handlers.base import HandlerResponse, IntentHandler


class RagAdapter(IntentHandler):
    @property
    def name(self) -> str:
        return "rag"

    def handle(self, context: Dict[str, Any]) -> HandlerResponse:
        search_query = (context.get("search_query") or "").strip()
        return HandlerResponse(text=f"[RAG] {search_query}" if search_query else "[RAG]")
