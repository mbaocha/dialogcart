"""
Outcome Renderer

Renders outcome text from YAML templates for EXECUTED outcomes.
"""

import re
import yaml
from pathlib import Path
from typing import Dict, Any, Optional

from .clarification_renderer import RenderSpec
from .mapper.outcome_mapper import derive_outcome_template_key_candidates

# Cache for loaded templates
_TEMPLATES_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def _load_outcome_templates() -> Dict[str, Dict[str, Any]]:
    """
    Load outcome templates from YAML configuration.
    
    Templates are cached after first load for performance.
    
    Returns:
        Dictionary mapping outcome template key strings to template configs
    
    Raises:
        FileNotFoundError: If templates/outcomes.yaml not found
        yaml.YAMLError: If YAML is invalid
    """
    global _TEMPLATES_CACHE
    
    if _TEMPLATES_CACHE is not None:
        return _TEMPLATES_CACHE
    
    # Find templates directory relative to this file
    # outcome_renderer.py is at: src/core/rendering/outcome_renderer.py
    # YAML is at: src/core/rendering/templates/outcomes.yaml
    current_file = Path(__file__)
    templates_path = current_file.parent / "templates" / "outcomes.yaml"
    
    if not templates_path.exists():
        raise FileNotFoundError(
            f"Outcome templates not found at {templates_path}. "
            f"Expected location: src/core/rendering/templates/outcomes.yaml"
        )
    
    with open(templates_path, "r", encoding="utf-8") as f:
        _TEMPLATES_CACHE = yaml.safe_load(f)
    
    if not isinstance(_TEMPLATES_CACHE, dict):
        raise ValueError(
            f"Invalid template file format: expected dictionary, got {type(_TEMPLATES_CACHE)}"
        )
    
    return _TEMPLATES_CACHE


def render_outcome(
    decision: Optional[Dict[str, Any]],
    outcome: Dict[str, Any]
) -> Optional[RenderSpec]:
    """
    Render outcome text from decision and outcome.
    
    Only renders for EXECUTED or FAILED status.
    
    Behavior:
    - If outcome.status not in ("EXECUTED", "FAILED"): return None
    - Generate candidate keys via outcome_mapper
    - Build interpolation data from outcome (use outcome dict as data)
    - For first matching key:
        - validate required_fields all present in data
        - interpolate {{placeholders}} deterministically
        - return RenderSpec(text=rendered)
    - If none found/valid: return None
    
    Args:
        decision: Decision dictionary (optional, for intent extraction)
        outcome: Outcome dictionary with status, intent_name, etc.
    
    Returns:
        RenderSpec with rendered text, or None if not applicable or template not found
    """
    # Only render for EXECUTED or FAILED status
    outcome_status = outcome.get("status")
    if outcome_status not in ("EXECUTED", "FAILED"):
        return None
    
    # Derive template key candidates
    template_key_candidates = derive_outcome_template_key_candidates(decision, outcome)
    if not template_key_candidates:
        return None
    
    # Load templates
    try:
        templates = _load_outcome_templates()
    except (FileNotFoundError, yaml.YAMLError, ValueError) as e:
        # Best-effort: if templates can't be loaded, return None
        return None
    
    # Build interpolation data from outcome
    # Use outcome dict directly as data source
    data = outcome.copy()
    
    # Also include nested fields that might be needed (e.g., outcome.booking_code)
    # Extract top-level fields that might be in outcome directly
    if "booking_code" not in data:
        # Try to get from nested structures
        booking_code = outcome.get("booking_code")
        if booking_code:
            data["booking_code"] = booking_code
    
    # Try each candidate in order
    for template_key in template_key_candidates:
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
        
        # Validate required fields
        missing_fields = [
            field for field in required_fields if field not in data
        ]
        if missing_fields:
            # Try next candidate
            continue
        
        # Extract all placeholders from template
        placeholders = re.findall(r'\{\{(\w+)\}\}', template)
        
        # Validate all placeholders are present in data
        missing_placeholders = [
            placeholder for placeholder in placeholders if placeholder not in data
        ]
        if missing_placeholders:
            # Try next candidate
            continue
        
        # Interpolate template
        rendered = template
        for placeholder in placeholders:
            value = str(data[placeholder])
            rendered = rendered.replace(f"{{{{{placeholder}}}}}", value)
        
        return RenderSpec(text=rendered)
    
    # No template found or validated
    return None

