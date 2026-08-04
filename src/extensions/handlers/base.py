"""
Intent Handler Base Interface

Defines the contract for intent handlers invoked when core emits HANDLER_DELEGATED.

Handlers are single-shot: one call per turn, returns raw facts + a render instruction.
Core owns rendering — the handler never produces user-facing text directly.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class HandlerResponse:
    """Response from an intent handler."""

    render_instruction: str
    """Natural-language instruction telling the LLM renderer what to do.
    Example: "Answer the user's question 'haircut price' using the provided evidence."
    Core passes this verbatim to the LLM renderer alongside facts."""

    facts: Dict[str, Any] = field(default_factory=dict)
    """Raw retrieval data for the renderer (chunks, structured_context, etc.)."""


class IntentHandler(ABC):
    """Abstract base for single-shot intent handlers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique handler name (e.g. "rag"). Must match intent_handlers.yaml."""

    @abstractmethod
    def handle(self, context: Dict[str, Any]) -> HandlerResponse:
        """
        Resolve the intent and return raw facts + a render instruction.

        Core calls the LLM renderer with render_instruction + facts after this returns.
        Do not produce user-facing text here.

        Args:
            context: {
                "user_id": str,
                "organization_id": int | None,
                "user_text": str,
                "intent_name": str,
                "search_query": str | None,
                "slots": dict,
                "session_slots": dict,   # persisted booking-kernel slots
                "session": dict,         # full raw session (read-only)
            }
        """
