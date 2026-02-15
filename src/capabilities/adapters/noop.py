"""
Noop Capability Adapter

A minimal adapter that completes immediately with zero business logic.
Used for smoke testing the capability runner integration.

This adapter:
- Completes immediately on start()
- Emits one fact: {"noop_done": True}
- No prompts, no local state, no input handling
"""

from typing import Any, Dict

from ..base import AdapterResponse, CapabilityAdapter


class NoopAdapter(CapabilityAdapter):
    """
    Noop adapter that completes immediately.

    Used for testing capability runner integration without business logic.
    """

    @property
    def name(self) -> str:
        """Capability name: 'noop'"""
        return "noop"

    def start(self, context: Dict[str, Any]) -> AdapterResponse:
        """
        Start capability - completes immediately.

        Args:
            context: Context dictionary (unused)

        Returns:
            AdapterResponse with completed=True and noop_done fact
        """
        return AdapterResponse(completed=True, text=None, facts={"noop_done": True})

    def handle_input(self, user_input: str, context: Dict[str, Any]) -> AdapterResponse:
        """
        Handle user input - should never be called since start() completes immediately.

        Kept for safety - returns completion if somehow called.

        Args:
            user_input: User message (unused)
            context: Context dictionary (unused)

        Returns:
            AdapterResponse with completed=True and noop_done fact
        """
        return AdapterResponse(completed=True, text=None, facts={"noop_done": True})

    def abort(self, reason: str, context: Dict[str, Any]) -> None:
        """
        Abort capability - no cleanup needed (no state).

        Args:
            reason: Reason for abortion (unused)
            context: Context dictionary (unused)
        """
        return
