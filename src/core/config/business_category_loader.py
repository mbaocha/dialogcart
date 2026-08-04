"""
Business Category Configuration Loader

Loads per-category business schema from business_categories.yaml.
Each category owns entities and declares booking_domain (workflow).
Fail-safe: returns empty config if file missing or invalid.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_VALID_BOOKING_DOMAINS = frozenset({"service", "reservation"})

_business_category_cache: Optional[Dict[str, Any]] = None
_cache_lock = None

try:
    import threading

    _cache_lock = threading.Lock()
except ImportError:
    _cache_lock = None


def clear_business_category_cache() -> None:
    """Drop cached YAML (tests / hot-reload)."""
    global _business_category_cache
    _business_category_cache = None


def load_business_categories() -> Dict[str, Any]:
    """Load business category configuration (cached). Returns {} on missing/invalid."""
    global _business_category_cache

    if _business_category_cache is not None:
        return _business_category_cache

    if _cache_lock:
        with _cache_lock:
            if _business_category_cache is not None:
                return _business_category_cache
            _business_category_cache = _load_business_categories_impl()
    else:
        _business_category_cache = _load_business_categories_impl()

    return _business_category_cache


def get_configured_categories() -> List[str]:
    """Return business category names (stable sorted order)."""
    config = load_business_categories()
    if not isinstance(config, dict):
        return []
    return sorted(
        name
        for name, entry in config.items()
        if isinstance(name, str) and isinstance(entry, dict)
    )


def is_configured_category(business_category: str) -> bool:
    """True when ``business_category`` has an entry in business_categories.yaml."""
    if not business_category or not isinstance(business_category, str):
        return False
    config = load_business_categories()
    if not isinstance(config, dict):
        return False
    return isinstance(config.get(business_category), dict)


def get_category_entities(business_category: str) -> List[Dict[str, Any]]:
    """Return entity definition dicts for a business category, or [].

    Entities are passed through as declared in YAML (including optional
    ``availability_criteria``). Interpretation of defaults happens in
    ``entity_schema_builder``.
    """
    config = load_business_categories()
    entry = config.get(business_category) if isinstance(config, dict) else None
    if not isinstance(entry, dict):
        return []
    entities = entry.get("entities")
    if not isinstance(entities, list):
        return []
    return [e for e in entities if isinstance(e, dict)]


def get_booking_domain(business_category: str) -> Optional[str]:
    """Return platform booking_domain for a category, or None if invalid/missing."""
    config = load_business_categories()
    entry = config.get(business_category) if isinstance(config, dict) else None
    if not isinstance(entry, dict):
        return None
    booking_domain = entry.get("booking_domain")
    if booking_domain not in _VALID_BOOKING_DOMAINS:
        return None
    return str(booking_domain)


def get_catalog_collection_keys(business_category: str) -> List[str]:
    """Ordered unique catalog collection keys referenced by the business schema."""
    keys: List[str] = []
    seen: set = set()
    for entity in get_category_entities(business_category):
        if entity.get("type") != "catalog":
            continue
        catalog_key = entity.get("catalog")
        if not isinstance(catalog_key, str) or not catalog_key or catalog_key in seen:
            continue
        keys.append(catalog_key)
        seen.add(catalog_key)
    return keys


def _load_business_categories_impl() -> Dict[str, Any]:
    config_dir = Path(__file__).resolve().parent
    config_path = config_dir / "business_categories.yaml"

    if not config_path.exists():
        logger.debug(
            "business_categories.yaml not found at %s. Returning empty config.",
            config_path,
        )
        return {}

    try:
        with config_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            logger.debug(
                "business_categories.yaml at %s is not a dict. Returning empty config.",
                config_path,
            )
            return {}
        return raw
    except yaml.YAMLError as e:
        logger.debug(
            "Failed to parse business_categories.yaml at %s: %s. Returning empty config.",
            config_path,
            e,
        )
        return {}
    except Exception as e:
        logger.debug(
            "Unexpected error loading business_categories.yaml at %s: %s. "
            "Returning empty config.",
            config_path,
            e,
        )
        return {}
