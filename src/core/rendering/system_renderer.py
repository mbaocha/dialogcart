"""
System Renderer

Renders system-level messages (welcome, greetings, etc.) from YAML templates.
"""

import yaml
from pathlib import Path
from typing import Dict, Any, Optional

from .clarification_renderer import RenderSpec

# Cache for loaded templates
_TEMPLATES_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def _load_system_templates() -> Dict[str, Dict[str, Any]]:
    """
    Load system templates from YAML configuration.
    
    Templates are cached after first load for performance.
    
    Returns:
        Dictionary mapping system template key strings to template configs
    
    Raises:
        FileNotFoundError: If templates/system.yaml not found
        yaml.YAMLError: If YAML is invalid
    """
    global _TEMPLATES_CACHE
    
    if _TEMPLATES_CACHE is not None:
        return _TEMPLATES_CACHE
    
    # Find templates directory relative to this file
    # system_renderer.py is at: src/core/rendering/system_renderer.py
    # YAML is at: src/core/rendering/templates/system.yaml
    current_file = Path(__file__)
    templates_path = current_file.parent / "templates" / "system.yaml"
    
    if not templates_path.exists():
        raise FileNotFoundError(
            f"System templates not found at {templates_path}. "
            f"Expected location: src/core/rendering/templates/system.yaml"
        )
    
    with open(templates_path, "r", encoding="utf-8") as f:
        _TEMPLATES_CACHE = yaml.safe_load(f)
    
    if not isinstance(_TEMPLATES_CACHE, dict):
        raise ValueError(
            f"Invalid template file format: expected dictionary, got {type(_TEMPLATES_CACHE)}"
        )
    
    return _TEMPLATES_CACHE


def render_system(intent_name: Optional[str]) -> Optional[RenderSpec]:
    """
    Render system text from intent name.
    
    Behavior:
    - If intent_name is falsy → return None
    - Normalize intent_name to upper
    - If intent_name in ["GREETING", "WELCOME"]:
        - Try keys in order:
            1) SYSTEM__WELCOME
            2) SYSTEM
        - Return first valid template
    - Else return None
    - Catch exceptions and return None (best effort)
    
    Args:
        intent_name: Intent name string (e.g., "GREETING", "WELCOME")
    
    Returns:
        RenderSpec with rendered text, or None if not applicable or template not found
    """
    # If intent_name is falsy, return None
    if not intent_name:
        return None
    
    # Normalize intent_name to upper
    intent_upper = str(intent_name).strip().upper()
    
    # Only render for GREETING or WELCOME intents
    if intent_upper not in ("GREETING", "WELCOME"):
        return None
    
    try:
        # Load templates
        templates = _load_system_templates()
    except (FileNotFoundError, yaml.YAMLError, ValueError):
        # Best-effort: if templates can't be loaded, return None
        return None
    
    # Try keys in order: SYSTEM__WELCOME, then SYSTEM
    for template_key in ["SYSTEM__WELCOME", "SYSTEM"]:
        if template_key not in templates:
            continue
        
        template_config = templates[template_key]
        
        if not isinstance(template_config, dict):
            continue
        
        if "template" not in template_config:
            continue
        
        template = template_config["template"]
        required_fields = template_config.get("required_fields", [])
        
        if not isinstance(required_fields, list):
            continue
        
        # Validate required fields (should be empty for system templates)
        missing_fields = [
            field for field in required_fields if field not in {}
        ]
        if missing_fields:
            # Try next candidate
            continue
        
        # System templates don't have placeholders, so just return the template text
        # Strip any trailing newlines from YAML multiline strings
        rendered = template.strip() if isinstance(template, str) else str(template).strip()
        
        return RenderSpec(text=rendered)
    
    # No template found
    return None

