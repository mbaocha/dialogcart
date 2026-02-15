"""
Clarification Renderer

Renders clarification text from YAML templates.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


@dataclass
class RenderSpec:
    """Specification for rendering a message."""

    text: str


# Cache for loaded templates
_TEMPLATES_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def _load_templates() -> Dict[str, Dict[str, Any]]:
    """
    Load clarification templates from YAML configuration.

    Templates are cached after first load for performance.

    Returns:
        Dictionary mapping clarification reason strings to template configs

    Raises:
        FileNotFoundError: If templates/clarifications.yaml not found
        yaml.YAMLError: If YAML is invalid
    """
    global _TEMPLATES_CACHE

    if _TEMPLATES_CACHE is not None:
        return _TEMPLATES_CACHE

    # Find templates directory relative to this file
    # clarification_renderer.py is at: src/core/rendering/clarification_renderer.py
    # YAML is at: src/core/rendering/templates/clarifications.yaml
    current_file = Path(__file__)
    templates_path = current_file.parent / "templates" / "clarifications.yaml"

    if not templates_path.exists():
        raise FileNotFoundError(
            f"Clarification templates not found at {templates_path}. "
            f"Expected location: src/core/rendering/templates/clarifications.yaml"
        )

    with open(templates_path, "r", encoding="utf-8") as f:
        _TEMPLATES_CACHE = yaml.safe_load(f)

    if not isinstance(_TEMPLATES_CACHE, dict):
        raise ValueError(
            f"Invalid template file format: expected dictionary, got {type(_TEMPLATES_CACHE)}"
        )

    return _TEMPLATES_CACHE


def render_clarification(
    reason: str, slots: Dict[str, Any], attempt_count: int = 0
) -> RenderSpec:
    """
    Render a clarification prompt from a reason and slots.

    Rules:
    - Look up template by reason string
    - If attempt_count > 1, try variant template first (e.g., "MISSING_TIME_ATTEMPT_2")
    - Fallback to base reason template if variant doesn't exist
    - Validate all required_fields are present in slots
    - Replace {{placeholders}} deterministically
    - Raise a clear error if template or data is missing
    - No branching logic
    - No fallback text

    Args:
        reason: Clarification reason string (e.g., "MISSING_TIME", "MISSING_DATE")
        slots: Dictionary of slot values for template interpolation
        attempt_count: Number of attempts for the slot (0 = first attempt, 1+ = retry attempts)

    Returns:
        RenderSpec with rendered text

    Raises:
        KeyError: If template not found for reason (after fallback)
        ValueError: If required fields are missing from slots
    """
    # Load templates from YAML
    templates = _load_templates()

    # attempt_count reflects number of previous prompts,
    # because increment happens after rendering.
    # So second prompt will have attempt_count == 1.
    template_key = reason
    if attempt_count >= 1:
        variant_key = f"{reason}_ATTEMPT_{attempt_count + 1}"
        if variant_key in templates:
            template_key = variant_key
        else:
            template_key = reason

    # Look up template
    if template_key not in templates:
        raise KeyError(
            f"No template found for clarification reason: {template_key}. "
            f"Available templates: {list(templates.keys())}"
        )

    template_config = templates[template_key]

    if not isinstance(template_config, dict):
        raise ValueError(
            f"Invalid template config for {template_key}: expected dictionary, got {type(template_config)}"
        )

    if "template" not in template_config:
        raise KeyError(f"Template config for {template_key} missing 'template' key")

    template = template_config["template"]
    required_fields = template_config.get("required_fields", [])

    if not isinstance(required_fields, list):
        raise ValueError(
            f"Template config for {template_key} has 'required_fields' that is not a list"
        )

    # Normalize slots: if service_id is present but service is not, use service_id as service
    if "service_id" in slots and "service" not in slots:
        slots["service"] = slots["service_id"]

    # Validate required fields
    missing_fields = [field for field in required_fields if field not in slots]
    if missing_fields:
        raise ValueError(
            f"Missing required fields for {template_key}: {missing_fields}. "
            f"Provided slots: {list(slots.keys())}"
        )

    # Extract all placeholders from template
    placeholders = re.findall(r"\{\{(\w+)\}\}", template)

    # Replace each placeholder with slot value
    rendered = template
    for placeholder in placeholders:
        if placeholder not in slots:
            raise ValueError(
                f"Placeholder '{placeholder}' found in template but missing from slots. "
                f"Required fields: {required_fields}, Slots: {list(slots.keys())}"
            )
        value = str(slots[placeholder])
        rendered = rendered.replace(f"{{{{{placeholder}}}}}", value)

    return RenderSpec(text=rendered)
