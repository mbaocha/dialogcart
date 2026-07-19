"""
Unified bootstrap for all extensions.

Registers default capability adapters (payment) and intent handlers (RAG).
Call once at application startup.
"""

import logging

logger = logging.getLogger(__name__)


def register_default_extensions() -> None:
    """Register default capability adapters and intent handlers."""
    from extensions.capabilities.bootstrap import register_default_adapters
    from extensions.handlers.bootstrap import register_default_handlers

    register_default_adapters()
    register_default_handlers()
    logger.info("Registered default extensions (capabilities + handlers)")
