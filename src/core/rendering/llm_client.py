"""
Shared Anthropic client for rendering-layer LLM operations.

Used by ``llm_renderer`` (wording) and ``off_topic`` (OFF_TOPIC factual evidence).
"""

from __future__ import annotations

import os
from typing import Any, Optional

DEFAULT_MODEL = os.getenv("LLM_RENDER_MODEL", "claude-haiku-4-5-20251001")


def get_anthropic_client(client: Any = None) -> Any:
    """Return an Anthropic client, or raise if API key is missing and no client given."""
    if client is not None:
        return client
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def resolve_model(override: Optional[str] = None) -> str:
    return override or DEFAULT_MODEL
