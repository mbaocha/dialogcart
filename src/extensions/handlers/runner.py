"""
Handler Runner

Invokes the correct IntentHandler for a HANDLER_DELEGATED outcome.
Single-shot: one call per turn, no lifecycle state.
Returns raw facts + render_instruction; core owns rendering.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict

from extensions.handlers.registry import get_handler

logger = logging.getLogger(__name__)


@dataclass
class HandlerResult:
    render_instruction: str
    facts: Dict[str, Any] = field(default_factory=dict)


class HandlerRunner:
    def handle(self, handler_name: str, context: Dict[str, Any]) -> HandlerResult:
        """Invoke handler and return result. Returns empty instruction on error."""
        try:
            handler = get_handler(handler_name)
        except KeyError:
            logger.error("No handler registered for %r", handler_name)
            return HandlerResult(render_instruction="")

        try:
            response = handler.handle(context)
            return HandlerResult(
                render_instruction=response.render_instruction,
                facts=response.facts,
            )
        except Exception:
            logger.exception("Handler %r raised during handle()", handler_name)
            return HandlerResult(render_instruction="")
