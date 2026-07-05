"""
Stage 1 intent extractor — lightweight LLM call for intent classification only.

Returns: {"intent": str, "confidence": float}

Later replaceable with embedding similarity (e.g. all-MiniLM-L6-v2) without
changing the Stage 2 interface, since both emit the same shape.
"""
import logging
import os
from typing import Any, Dict, Optional

import anthropic

from .prompt import build_system_prompt, get_tool
from ..shared.context import format_conversation_context

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"


class Stage1IntentExtractor:
    """Classify intent from user message + conversation context.

    Does not extract slots — that is Stage 2's responsibility.
    """

    def __init__(self):
        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    def classify(
        self,
        text: str,
        now: str,
        conversation_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return {"intent": str, "confidence": float}."""
        system = build_system_prompt(now, conversation_context)
        ctx_block = format_conversation_context(conversation_context or {})
        user_content = f"CURRENT USER MESSAGE:\n{text}" if ctx_block else text

        try:
            response = self._client.messages.create(
                model=_MODEL,
                max_tokens=128,
                system=system,
                tools=[get_tool()],
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception:
            logger.exception("Stage1 intent classification failed for text=%r", text)
            return {"intent": "UNKNOWN", "confidence": 0.0}

        for block in response.content:
            if block.type == "tool_use" and block.name == "classify_intent":
                return {
                    "intent": block.input.get("intent", "UNKNOWN"),
                    "confidence": float(block.input.get("confidence", 0.0)),
                }

        logger.warning("Stage1: no tool_use block returned for text=%r", text)
        return {"intent": "UNKNOWN", "confidence": 0.0}
