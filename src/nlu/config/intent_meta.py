"""Intent metadata — loads temporal_shape from intent_signals.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_INTENT_META_CACHE: Dict[str, Dict[str, Any]] = {}


@dataclass(frozen=True)
class IntentMeta:
    intent_name: str
    temporal_shape: Optional[str] = None

    @classmethod
    def from_dict(cls, intent_name: str, data: Dict[str, Any]) -> "IntentMeta":
        return cls(
            intent_name=intent_name,
            temporal_shape=data.get("temporal_shape"),
        )


class IntentRegistry:
    """Read-only registry for intent temporal shapes."""

    def __init__(self):
        self._meta_cache: Optional[Dict[str, IntentMeta]] = None

    def _ensure_loaded(self) -> None:
        if self._meta_cache is None:
            raw_meta = load_intent_meta()
            self._meta_cache = {
                name: IntentMeta.from_dict(name, data)
                for name, data in raw_meta.items()
            }

    def get(self, intent_name: str) -> Optional[IntentMeta]:
        self._ensure_loaded()
        return self._meta_cache.get(intent_name) if self._meta_cache else None

    def all_intents(self) -> Dict[str, IntentMeta]:
        self._ensure_loaded()
        return self._meta_cache.copy() if self._meta_cache else {}


_registry_instance: Optional[IntentRegistry] = None


def get_intent_registry() -> IntentRegistry:
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = IntentRegistry()
    return _registry_instance


def load_intent_meta() -> Dict[str, Dict[str, Any]]:
    global _INTENT_META_CACHE
    if _INTENT_META_CACHE:
        return _INTENT_META_CACHE

    config_data_path = Path(__file__).resolve().parent / "data" / "intent_signals.yaml"
    if not config_data_path.exists():
        raise FileNotFoundError(f"intent_signals.yaml not found: {config_data_path}")

    with config_data_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    intents_cfg = raw.get("intents", raw) if isinstance(raw, dict) else {}
    for intent, cfg in intents_cfg.items():
        if isinstance(cfg, dict):
            _INTENT_META_CACHE[intent] = cfg
    return _INTENT_META_CACHE
