"""
Capability Policy Loader

Loads capability blocking rules from capabilities.yaml.
Fail-safe: returns empty config if file missing or invalid.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)

# Cache for capability policies
_capability_policies_cache: Optional[Dict[str, Any]] = None
_cache_lock = None

try:
    import threading

    _cache_lock = threading.Lock()
except ImportError:
    _cache_lock = None


def load_capability_policies() -> Dict[str, Any]:
    """
    Load capability policies from capabilities.yaml (cached at module level).

    Thread-safe lazy loading: loads once on first access, reuses cached data
    for subsequent calls.

    Fail-safe behavior:
    - If file missing → returns empty dict
    - If file invalid → returns empty dict
    - No exceptions bubble up

    Returns:
        Dictionary with capability policies. Structure:
        {
            "capabilities": {
                "capability_name": {
                    "applies_to": {"intent": "INTENT_NAME"},
                    "blocks": ["STEP_NAME"],
                    "when": {...}
                }
            }
        }
        Returns empty dict if file missing or invalid.
    """
    global _capability_policies_cache

    # Fast path: return cached data if already loaded
    if _capability_policies_cache is not None:
        return _capability_policies_cache

    # Slow path: load and cache (thread-safe if threading available)
    if _cache_lock:
        with _cache_lock:
            # Double-check after acquiring lock
            if _capability_policies_cache is not None:
                return _capability_policies_cache
            _capability_policies_cache = _load_capability_policies_impl()
    else:
        _capability_policies_cache = _load_capability_policies_impl()

    return _capability_policies_cache


def _load_capability_policies_impl() -> Dict[str, Any]:
    """
    Internal implementation of capability policy loading.

    Returns:
        Dictionary with capability policies, or empty dict if file missing/invalid.
    """
    # Load YAML file from config directory
    config_dir = Path(__file__).resolve().parent
    config_path = config_dir / "capabilities.yaml"

    # Fail-safe: return empty config if file doesn't exist
    if not config_path.exists():
        logger.debug(
            f"capabilities.yaml not found at {config_path}. "
            "Returning empty capability policies (no blocking)."
        )
        return {}

    try:
        with config_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # Extract capabilities dict from YAML
        # Structure: {capabilities: {capability_name: {...}}}
        if not isinstance(raw, dict):
            logger.debug(
                f"capabilities.yaml at {config_path} is not a dict. "
                "Returning empty capability policies."
            )
            return {}

        capabilities = raw.get("capabilities", {})
        if not isinstance(capabilities, dict):
            logger.debug(
                f"capabilities.yaml at {config_path} has invalid 'capabilities' section. "
                "Returning empty capability policies."
            )
            return {}

        # Return full structure for consistency
        return {"capabilities": capabilities}

    except yaml.YAMLError as e:
        logger.debug(
            f"Failed to parse capabilities.yaml at {config_path}: {e}. "
            "Returning empty capability policies."
        )
        return {}
    except Exception as e:
        logger.debug(
            f"Unexpected error loading capabilities.yaml at {config_path}: {e}. "
            "Returning empty capability policies."
        )
        return {}
