"""
Clarification Renderer

Renders clarification text from YAML templates.
"""

import re
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass


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


def render_clarification(reason: str, slots: Dict[str, Any]) -> RenderSpec:
    """
    Render a clarification prompt from a reason and slots.
    
    Rules:
    - Look up template by reason string
    - Validate all required_fields are present in slots
    - Replace {{placeholders}} deterministically
    - Raise a clear error if template or data is missing
    - No branching logic
    - No fallback text
    
    Args:
        reason: Clarification reason string (e.g., "MISSING_TIME", "MISSING_DATE")
        slots: Dictionary of slot values for template interpolation
    
    Returns:
        RenderSpec with rendered text
    
    Raises:
        KeyError: If template not found for reason
        ValueError: If required fields are missing from slots
    """
    # Load templates from YAML
    templates = _load_templates()
    
    # Look up template
    if reason not in templates:
        raise KeyError(
            f"No template found for clarification reason: {reason}. "
            f"Available templates: {list(templates.keys())}"
        )
    
    template_config = templates[reason]
    
    if not isinstance(template_config, dict):
        raise ValueError(
            f"Invalid template config for {reason}: expected dictionary, got {type(template_config)}"
        )
    
    if "template" not in template_config:
        raise KeyError(
            f"Template config for {reason} missing 'template' key"
        )
    
    template = template_config["template"]
    required_fields = template_config.get("required_fields", [])
    
    if not isinstance(required_fields, list):
        raise ValueError(
            f"Template config for {reason} has 'required_fields' that is not a list"
        )
    
    # Validate required fields
    missing_fields = [
        field for field in required_fields if field not in slots
    ]
    if missing_fields:
        raise ValueError(
            f"Missing required fields for {reason}: {missing_fields}. "
            f"Provided slots: {list(slots.keys())}"
        )
    
    # Extract all placeholders from template
    placeholders = re.findall(r'\{\{(\w+)\}\}', template)
    
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

