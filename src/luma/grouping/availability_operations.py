"""Detect structured availability operations from user text (Luma → Core contract)."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

BROWSE_NEXT = "browse_next"
BROWSE_PREVIOUS = "browse_previous"

_operations_cache: Optional[Dict[str, List[str]]] = None
_cache_lock = threading.Lock()


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    lowered = text.lower()
    no_punct = re.sub(r"[^\w\s]", " ", lowered)
    return re.sub(r"\s+", " ", no_punct).strip()


def _load_availability_operations() -> Dict[str, List[str]]:
    global _operations_cache
    if _operations_cache is not None:
        return _operations_cache

    with _cache_lock:
        if _operations_cache is not None:
            return _operations_cache

        config_dir = Path(__file__).resolve().parent.parent / "config" / "data"
        path = config_dir / "availability_operations.yaml"
        if not path.exists():
            _operations_cache = {BROWSE_NEXT: [], BROWSE_PREVIOUS: []}
            return _operations_cache

        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}

        loaded: Dict[str, List[str]] = {}
        for operation in (BROWSE_NEXT, BROWSE_PREVIOUS):
            section = raw.get(operation) if isinstance(raw, dict) else None
            phrases: List[str] = []
            if isinstance(section, dict):
                for phrase in section.get("phrases") or []:
                    if isinstance(phrase, str):
                        normalized = _normalize_text(phrase)
                        if normalized:
                            phrases.append(normalized)
            loaded[operation] = phrases

        _operations_cache = loaded
        return _operations_cache


def detect_availability_operation(text: Optional[str]) -> Optional[str]:
    """Return ``browse_next`` / ``browse_previous`` when text matches browse phrases."""
    normalized = _normalize_text(text or "")
    if not normalized:
        return None

    operations = _load_availability_operations()
    for operation in (BROWSE_PREVIOUS, BROWSE_NEXT):
        for phrase in operations.get(operation) or []:
            if phrase in normalized:
                return operation
    return None


def availability_operation_phrases() -> Tuple[List[str], List[str]]:
    """Return (browse_next_phrases, browse_previous_phrases) for tests."""
    operations = _load_availability_operations()
    return list(operations.get(BROWSE_NEXT) or []), list(
        operations.get(BROWSE_PREVIOUS) or []
    )
