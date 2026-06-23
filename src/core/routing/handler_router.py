"""
Handler Router

Maps intent names to handler names using core/config/intent_handlers.yaml.

Usage:
    from core.routing.handler_router import resolve_handler
    handler = resolve_handler("GENERAL_INQUIRY")  # -> "rag"
    handler = resolve_handler("CREATE_APPOINTMENT")  # -> None
"""
import logging
from pathlib import Path
from typing import Dict, Optional

import yaml

logger = logging.getLogger(__name__)

_YAML_PATH = Path(__file__).parent.parent / "config" / "intent_handlers.yaml"

# Populated once on first call; maps intent_name -> handler_name
_intent_to_handler: Dict[str, str] = {}
_loaded = False


def _load() -> Dict[str, str]:
    global _intent_to_handler, _loaded
    if _loaded:
        return _intent_to_handler
    try:
        data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8")) or {}
        for handler_name, cfg in (data.get("handlers") or {}).items():
            for intent in cfg.get("intents") or []:
                _intent_to_handler[intent] = handler_name
        _loaded = True
    except Exception:
        logger.exception("Failed to load intent_handlers.yaml from %s", _YAML_PATH)
    return _intent_to_handler


def resolve_handler(intent: str) -> Optional[str]:
    """Return the handler name for intent, or None if no handler is configured."""
    return _load().get(intent)
