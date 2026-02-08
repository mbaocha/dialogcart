"""
Clarification Router

Maps clarification reasons to template keys.

This is a pure routing function with no side effects, no execution,
and no rendering logic. It only performs semantic signal → identifier mapping.
"""

import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

# Clarification reason → template key mapping (loaded from config)
_CLARIFICATION_TEMPLATES: Dict[str, str] = {}


def _load_clarification_templates() -> Dict[str, str]:
    """
    Load clarification template mappings from YAML config file.

    Returns:
        Dictionary mapping clarification reasons to template key patterns

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config file is malformed
    """
    # Find config file relative to this module
    # clarification_router.py is at: src/core/routing/clarification_router.py
    # config file is at: src/core/routing/config/clarification_templates.yaml
    current_file = Path(__file__)
    config_file = current_file.parent / \
        "config" / "clarification_templates.yaml"

    if not config_file.exists():
        logger.warning(
            "Clarification templates config not found at %s. "
            "Using empty mapping (all reasons will fall back to {domain}.clarify)",
            config_file
        )
        return {}

    try:
        import yaml
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f) or {}

        if not isinstance(config_data, dict):
            raise ValueError(
                f"Config file {config_file} must contain a YAML dictionary. "
                f"Got {type(config_data)}"
            )

        # Validate entries: all values must be strings containing {domain}
        validated: Dict[str, str] = {}
        for reason, template_pattern in config_data.items():
            if not isinstance(reason, str):
                logger.warning(
                    "Skipping invalid key in config (must be string): %r",
                    reason
                )
                continue
            if not isinstance(template_pattern, str):
                logger.warning(
                    "Skipping invalid template pattern for %r: expected string, got %s",
                    reason,
                    type(template_pattern).__name__
                )
                continue
            if "{domain}" not in template_pattern:
                logger.warning(
                    "Template pattern for %r missing {domain} placeholder: %r. "
                    "It will not be domain-aware.",
                    reason,
                    template_pattern
                )
            validated[reason] = template_pattern

        logger.info(
            "Loaded %d clarification template mappings from %s",
            len(validated),
            config_file
        )
        return validated

    except ImportError:
        logger.warning(
            "PyYAML not available. Cannot load clarification templates from %s. "
            "Using empty mapping (all reasons will fall back to {domain}.clarify). "
            "Install PyYAML to enable config-driven templates.",
            config_file
        )
        return {}
    except yaml.YAMLError as e:
        raise ValueError(
            f"Failed to parse YAML config file {config_file}: {e}"
        ) from e
    except Exception as e:
        logger.error(
            "Error loading clarification templates from %s: %s",
            config_file,
            e,
            exc_info=True
        )
        raise ValueError(
            f"Failed to load clarification templates: {e}"
        ) from e


# Load templates at module import time
_CLARIFICATION_TEMPLATES = _load_clarification_templates()


def get_template_key(reason: str, domain: str = "service") -> str:
    """
    Get template key for clarification reason.

    Maps a clarification reason (semantic signal) to a template key (identifier).
    This is a pure routing function with no side effects.

    Args:
        reason: Clarification reason string (e.g., "MISSING_TIME")
        domain: Domain (default: "service")

    Returns:
        Template key string (e.g., "service.ask_time")
    """
    template_pattern = _CLARIFICATION_TEMPLATES.get(reason, "{domain}.clarify")
    try:
        return template_pattern.format(domain=domain)
    except KeyError as e:
        # Handle case where template pattern has unexpected placeholders
        logger.warning(
            "Template pattern for %r has unexpected placeholder: %s. "
            "Falling back to {domain}.clarify",
            reason,
            e
        )
        return f"{domain}.clarify"

