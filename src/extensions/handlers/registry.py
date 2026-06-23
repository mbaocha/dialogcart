"""Handler registry — maps handler names to IntentHandler instances."""

import logging
from typing import Dict, List

from .base import IntentHandler

logger = logging.getLogger(__name__)

HANDLER_REGISTRY: Dict[str, IntentHandler] = {}


def register_handler(handler: IntentHandler) -> None:
    if not isinstance(handler, IntentHandler):
        raise ValueError(f"Expected IntentHandler, got {type(handler)}")
    name = handler.name
    if not name:
        raise ValueError("Handler name must be non-empty")
    if name in HANDLER_REGISTRY:
        logger.warning("Handler %r already registered, overwriting", name)
    HANDLER_REGISTRY[name] = handler
    logger.info("Registered handler: %s", name)


def get_handler(name: str) -> IntentHandler:
    if name not in HANDLER_REGISTRY:
        raise KeyError(
            f"Handler not registered: {name!r}. Available: {list(HANDLER_REGISTRY)}"
        )
    return HANDLER_REGISTRY[name]


def list_handlers() -> List[str]:
    return list(HANDLER_REGISTRY)


def clear_registry() -> None:
    HANDLER_REGISTRY.clear()
