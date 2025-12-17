"""
Clarification Template Renderer

Deterministic template rendering from Clarification objects.
No branching logic, no fallback text.

Templates are loaded from JSON configuration at templates/clarification.json.
"""

from typing import Dict, Any
import re
import json
from pathlib import Path

from .models import Clarification

# Cache for loaded templates
_TEMPLATES_CACHE: Dict[str, Dict[str, Any]] | None = None


def _load_templates() -> Dict[str, Dict[str, Any]]:
    """
    Load clarification templates from JSON configuration.

    Templates are cached after first load for performance.

    Returns:
        Dictionary mapping ClarificationReason values to template configs

    Raises:
        FileNotFoundError: If templates/clarification.json not found
        json.JSONDecodeError: If JSON is invalid
    """
    global _TEMPLATES_CACHE

    if _TEMPLATES_CACHE is not None:
        return _TEMPLATES_CACHE

    # Find templates directory relative to this file
    # renderer.py is at: dialogcart/src/luma/clarification/renderer.py
    # JSON is at: dialogcart/templates/clarification.json
    current_file = Path(__file__)
    templates_path = current_file.parent.parent.parent.parent / \
        "templates" / "clarification.json"

    if not templates_path.exists():
        raise FileNotFoundError(
            f"Clarification templates not found at {templates_path}. "
            f"Expected location: dialogcart/templates/clarification.json"
        )

    with open(templates_path, "r", encoding="utf-8") as f:
        _TEMPLATES_CACHE = json.load(f)

    return _TEMPLATES_CACHE


def render_clarification(clarification: Clarification) -> str:
    """
    Render a clarification prompt from a Clarification object.

    Rules:
    - Look up template by clarification.reason.value
    - Validate all required_fields are present in clarification.data
    - Replace {{placeholders}} deterministically
    - Raise a clear error if data is missing
    - No branching logic
    - No fallback text

    Args:
        clarification: Clarification object with reason and data

    Returns:
        Rendered clarification message string

    Raises:
        KeyError: If template not found for reason
        ValueError: If required fields are missing from data
    """
    reason_value = clarification.reason.value

    # Load templates from JSON
    templates = _load_templates()

    # Look up template
    if reason_value not in templates:
        raise KeyError(
            f"No template found for ClarificationReason: {reason_value}. "
            f"Available templates: {list(templates.keys())}"
        )

    template_config = templates[reason_value]
    template = template_config["template"]
    required_fields = template_config["required_fields"]

    # Validate required fields
    missing_fields = [
        field for field in required_fields if field not in clarification.data]
    if missing_fields:
        raise ValueError(
            f"Missing required fields for {reason_value}: {missing_fields}. "
            f"Provided data: {clarification.data}"
        )

    # Extract all placeholders from template
    placeholders = re.findall(r'\{\{(\w+)\}\}', template)

    # Replace each placeholder with data value
    rendered = template
    for placeholder in placeholders:
        if placeholder not in clarification.data:
            raise ValueError(
                f"Placeholder '{placeholder}' found in template but missing from data. "
                f"Required fields: {required_fields}, Data: {clarification.data}"
            )
        value = str(clarification.data[placeholder])
        rendered = rendered.replace(f"{{{{{placeholder}}}}}", value)

    return rendered
