"""Single-shot intent handlers (RAG, etc.) for HANDLER_DELEGATED outcomes."""

from .adapters.rag import RagAdapter
from .base import HandlerResponse, IntentHandler
from .bootstrap import register_default_handlers
from .registry import (
    HANDLER_REGISTRY,
    clear_registry,
    get_handler,
    list_handlers,
    register_handler,
)
from .runner import HandlerResult, HandlerRunner

__all__ = [
    "IntentHandler",
    "HandlerResponse",
    "HandlerRunner",
    "HandlerResult",
    "HANDLER_REGISTRY",
    "register_handler",
    "register_default_handlers",
    "get_handler",
    "list_handlers",
    "clear_registry",
    "RagAdapter",
]
