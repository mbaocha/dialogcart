"""
Intent Handler Base Interface

Defines the contract for intent handlers that are invoked when core emits
status == "HANDLER_DELEGATED".

Unlike capability adapters (which have multi-turn lifecycle), handlers are
single-shot: one call per turn, returns text immediately.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class HandlerResponse:
    """Response from an intent handler."""

    text: str
    """Text to return to the user."""

    facts: Dict[str, Any] = field(default_factory=dict)
    """Optional facts to surface (reserved for future use)."""


class IntentHandler(ABC):
    """Abstract base for single-shot intent handlers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique handler name (e.g. "rag"). Must match intent_handlers.yaml."""

    @abstractmethod
    def handle(self, context: Dict[str, Any]) -> HandlerResponse:
        """
        Handle the intent and return a response.

        Args:
            context: {
                "user_id": str,
                "intent_name": str,
                "search_query": str | None,
                "slots": dict,
                "session_slots": dict,
            }
        """
