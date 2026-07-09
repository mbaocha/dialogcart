"""LumaInterpreter — Phase 1 architectural boundary for NLU.

Initially wraps the existing Luma client. Phase 2 will move context-building
and error recovery into this class.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class LumaInterpreter:
    """Single entry point for NLU interpretation via the Luma service.

    Phase 1: thin wrapper around the existing LumaClient.
    Phase 2: will own context construction, tenant_context building, and
             UpstreamError recovery that currently live in turn_planner.py.
    """

    def __init__(self, luma_client: Optional[Any] = None) -> None:
        if luma_client is None:
            from core.orchestration.nlu.luma_client import LumaClient

            luma_client = LumaClient()
        self._client = luma_client

    def resolve(
        self,
        user_id: str,
        text: str,
        domain: str,
        timezone: str = "UTC",
        tenant_context: Optional[Dict[str, Any]] = None,
        conversation_context: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Interpret a user utterance and return structured NLU output."""
        return self._client.resolve(
            user_id=user_id,
            text=text,
            domain=domain,
            timezone=timezone,
            tenant_context=tenant_context,
            conversation_context=conversation_context,
        )
