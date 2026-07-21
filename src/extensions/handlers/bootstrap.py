"""Register default intent handlers at startup."""
import logging

logger = logging.getLogger(__name__)


def register_default_handlers() -> None:
    try:
        from extensions.handlers.adapters.off_topic import OffTopicAdapter
        from extensions.handlers.adapters.rag import RagAdapter
        from extensions.handlers.registry import register_handler

        register_handler(RagAdapter())
        register_handler(OffTopicAdapter())
        logger.info("Registered default handlers: rag, off_topic")
    except Exception:
        logger.exception("Failed to register default handlers")
