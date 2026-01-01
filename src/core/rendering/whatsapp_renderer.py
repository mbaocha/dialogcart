"""
WhatsApp Renderer

Renders structured outcome objects into WhatsApp messages.

This module handles:
- Template lookup by template_key
- Variable interpolation from outcome data
- Required fields validation
- WhatsApp message formatting (text, buttons, etc.)

Constraints:
- Must consume outcome objects only
- Must not call Luma or business APIs
- Must not contain orchestration logic
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# Template registry loaded from JSON
_TEMPLATE_REGISTRY: Dict[str, Dict[str, Any]] = {}


def _load_template_registry() -> Dict[str, Dict[str, Any]]:
    """
    Load template registry from JSON file.

    Returns:
        Dictionary mapping template keys to template definitions
    """
    # Find template file relative to this module
    # whatsapp_renderer.py is at: src/core/rendering/whatsapp_renderer.py
    # template file is at: src/core/rendering/templates/clarification.json
    current_file = Path(__file__)
    template_file = current_file.parent / "templates" / "clarification.json"

    if not template_file.exists():
        logger.warning(
            "Template registry not found at %s. Using empty registry.",
            template_file
        )
        return {}

    try:
        with open(template_file, 'r', encoding='utf-8') as f:
            registry_data = json.load(f)

        if not isinstance(registry_data, dict):
            logger.warning(
                "Template registry at %s must be a JSON object. Got %s. Using empty registry.",
                template_file,
                type(registry_data).__name__
            )
            return {}

        # Validate structure: each entry should have 'template' and 'required_fields'
        validated: Dict[str, Dict[str, Any]] = {}
        for key, template_def in registry_data.items():
            if not isinstance(template_def, dict):
                logger.warning(
                    "Skipping invalid template definition for %r: expected object, got %s",
                    key,
                    type(template_def).__name__
                )
                continue
            if "template" not in template_def:
                logger.warning(
                    "Skipping template %r: missing 'template' field",
                    key
                )
                continue
            if "required_fields" not in template_def:
                logger.warning(
                    "Template %r missing 'required_fields', defaulting to empty list",
                    key
                )
                template_def["required_fields"] = []
            validated[key] = template_def

        logger.info(
            "Loaded %d templates from %s",
            len(validated),
            template_file
        )
        return validated

    except json.JSONDecodeError as e:
        logger.error(
            "Failed to parse template registry JSON at %s: %s",
            template_file,
            e
        )
        return {}
    except Exception as e:
        logger.error(
            "Error loading template registry from %s: %s",
            template_file,
            e,
            exc_info=True
        )
        return {}


# Load templates at module import time
_TEMPLATE_REGISTRY = _load_template_registry()


def _interpolate_template(template: str, data: Dict[str, Any]) -> str:
    """
    Interpolate variables in template string.

    Supports {{variable}} syntax for variable substitution.

    Args:
        template: Template string with {{variable}} placeholders
        data: Dictionary of variables to substitute

    Returns:
        Interpolated string
    """
    result = template
    for key, value in data.items():
        placeholder = f"{{{{{key}}}}}"
        if placeholder in result:
            result = result.replace(placeholder, str(value))
    return result


def _validate_required_fields(
    template_key: str,
    template_def: Dict[str, Any],
    data: Dict[str, Any]
) -> List[str]:
    """
    Validate that all required fields are present in data.

    Args:
        template_key: Template key for error messages
        template_def: Template definition with 'required_fields' list
        data: Data dictionary to validate

    Returns:
        List of missing field names (empty if all present)
    """
    required_fields = template_def.get("required_fields", [])
    missing = []
    for field in required_fields:
        if field not in data or data[field] is None:
            missing.append(field)
    return missing


def render_outcome_to_whatsapp(outcome: Dict[str, Any]) -> Dict[str, Any]:
    """
    Render a structured outcome object into a WhatsApp message.

    This function consumes outcome objects from the orchestration layer
    and converts them into WhatsApp-formatted messages.

    Args:
        outcome: Outcome dictionary with 'type', 'template_key', 'data', etc.

    Returns:
        WhatsApp message dictionary with 'text', 'buttons', etc.
        Format depends on outcome type:
        - CLARIFY: Returns text message from template
        - BOOKING_CREATED: Returns confirmation message
        - BOOKING_CANCELLED: Returns cancellation message
        - etc.

    Raises:
        ValueError: If template is missing required fields or template not found
    """
    outcome_type = outcome.get("type")

    if outcome_type == "CLARIFY":
        template_key = outcome.get("template_key")
        if not template_key:
            logger.warning("CLARIFY outcome missing template_key, using default message")
            return {
                "text": "Could you provide more information?",
                "type": "text"
            }

        data = outcome.get("data", {})
        
        # Require structured reason from data - this is the authoritative source
        reason = data.get("reason")
        if not reason:
            logger.error(
                "CLARIFY outcome missing data.reason. Data: %s. Using default message.",
                data
            )
            return {
                "text": "Could you provide more information?",
                "type": "text"
            }
        
        # Look up template by reason
        template_def = _TEMPLATE_REGISTRY.get(reason)
        if not template_def:
            logger.warning(
                "Template not found for reason %r (from template_key %r). Using default message.",
                reason,
                template_key
            )
            return {
                "text": "Could you provide more information?",
                "type": "text"
            }

        # data already extracted above
        missing_fields = _validate_required_fields(template_key, template_def, data)
        if missing_fields:
            logger.warning(
                "Template %r missing required fields: %s. Rendering anyway with available data.",
                template_key,
                missing_fields
            )

        template_text = template_def.get("template", "Could you provide more information?")
        rendered_text = _interpolate_template(template_text, data)

        return {
            "text": rendered_text,
            "type": "text"
        }

    elif outcome_type == "BOOKING_CREATED":
        booking_code = outcome.get("booking_code", "N/A")
        status = outcome.get("status", "pending")
        return {
            "text": f"Booking confirmed! Your booking code is {booking_code}. Status: {status}.",
            "type": "text"
        }

    elif outcome_type == "BOOKING_CANCELLED":
        booking_code = outcome.get("booking_code", "N/A")
        return {
            "text": f"Booking {booking_code} has been cancelled.",
            "type": "text"
        }

    else:
        logger.warning("Unknown outcome type: %r. Returning default message.", outcome_type)
        return {
            "text": "Your request has been processed.",
            "type": "text"
        }

