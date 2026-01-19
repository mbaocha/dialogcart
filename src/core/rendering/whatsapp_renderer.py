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
from datetime import datetime

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


def _format_datetime_for_display(dt_str: str) -> str:
    """
    Format ISO datetime string to user-friendly format.
    
    Converts "2025-01-04T14:00:00Z" to "Jan 4, 2:00 PM"
    
    Args:
        dt_str: ISO datetime string (e.g., "2025-01-04T14:00:00Z")
    
    Returns:
        Formatted string (e.g., "Jan 4, 2:00 PM")
    """
    try:
        # Parse ISO format
        if dt_str.endswith('Z'):
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(dt_str.replace('Z', ''))
        
        # Format as "Jan 4, 2:00 PM"
        month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        month = month_names[dt.month - 1]
        day = dt.day
        hour = dt.hour
        minute = dt.minute
        
        # Convert to 12-hour format
        if hour == 0:
            hour_12 = 12
            meridiem = "AM"
        elif hour < 12:
            hour_12 = hour
            meridiem = "AM"
        elif hour == 12:
            hour_12 = 12
            meridiem = "PM"
        else:
            hour_12 = hour - 12
            meridiem = "PM"
        
        if minute == 0:
            return f"{month} {day}, {hour_12}:00 {meridiem}"
        else:
            return f"{month} {day}, {hour_12}:{minute:02d} {meridiem}"
    except (ValueError, AttributeError, TypeError):
        # If parsing fails, return original string
        return dt_str


def _extract_slots_from_outcome(outcome: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract slots from outcome.facts.slots and format for template rendering.
    
    All template variables are sourced from outcome.facts.slots only.
    This function does not access outcome.booking or any other fields.
    
    Args:
        outcome: Outcome dictionary with facts.slots
    
    Returns:
        Dictionary of formatted slot values for template interpolation
    """
    facts = outcome.get("facts", {})
    slots = facts.get("slots", {})
    
    formatted_slots = {}
    
    # Extract service_id - use as both service_id and service_name
    service_id = slots.get("service_id")
    if service_id:
        formatted_slots["service_id"] = service_id
        # Use service_id as service_name if no separate name is provided
        formatted_slots["service"] = service_id
        formatted_slots["service_name"] = service_id
    
    # Extract and format datetime_range
    datetime_range = slots.get("datetime_range")
    if datetime_range:
        if isinstance(datetime_range, dict):
            start = datetime_range.get("start")
            if start:
                formatted_datetime = _format_datetime_for_display(start)
                formatted_slots["datetime_range"] = formatted_datetime
                formatted_slots["datetime"] = formatted_datetime
                formatted_slots["datetime_start"] = formatted_datetime
        else:
            # If datetime_range is a string, try to format it
            formatted_datetime = _format_datetime_for_display(str(datetime_range))
            formatted_slots["datetime_range"] = formatted_datetime
            formatted_slots["datetime"] = formatted_datetime
    
    # Extract date_range
    date_range = slots.get("date_range")
    if date_range:
        if isinstance(date_range, dict):
            start_date = date_range.get("start")
            end_date = date_range.get("end")
            
            # Format start date
            if start_date:
                formatted_slots["date"] = start_date
                formatted_slots["date_range"] = start_date
                
                # Also set datetime for templates that expect it (e.g., AWAITING_CONFIRMATION)
                # Format date as datetime for display
                try:
                    from datetime import datetime
                    # Try to parse and format the date
                    if isinstance(start_date, str):
                        # If it's just a date (YYYY-MM-DD), format it nicely
                        if len(start_date) == 10 and start_date.count('-') == 2:
                            dt = datetime.strptime(start_date, "%Y-%m-%d")
                            formatted_date = dt.strftime("%b %d, %Y")
                            # If we have an end date, format as range
                            if end_date and isinstance(end_date, str) and len(end_date) == 10:
                                try:
                                    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                                    formatted_end = end_dt.strftime("%b %d, %Y")
                                    formatted_slots["datetime"] = f"{formatted_date} to {formatted_end}"
                                except Exception:
                                    formatted_slots["datetime"] = formatted_date
                            else:
                                formatted_slots["datetime"] = formatted_date
                        else:
                            formatted_slots["datetime"] = start_date
                    else:
                        formatted_slots["datetime"] = str(start_date)
                except Exception:
                    # Fallback: use date as-is
                    formatted_slots["datetime"] = str(start_date)
        else:
            formatted_slots["date"] = str(date_range)
            formatted_slots["date_range"] = str(date_range)
            formatted_slots["datetime"] = str(date_range)
    
    # Extract time_range
    time_range = slots.get("time_range")
    if time_range:
        if isinstance(time_range, dict):
            start_time = time_range.get("start_time")
            if start_time:
                formatted_slots["time"] = start_time
                formatted_slots["time_range"] = start_time
        else:
            formatted_slots["time"] = str(time_range)
            formatted_slots["time_range"] = str(time_range)
    
    return formatted_slots


def render_outcome_to_whatsapp(outcome: Dict[str, Any]) -> Dict[str, Any]:
    """
    Render a structured outcome object into a WhatsApp message.

    This function consumes outcome objects from the orchestration layer
    and converts them into WhatsApp-formatted messages.

    Args:
        outcome: Outcome dictionary with 'status', 'template_key', 'data', etc.

    Returns:
        WhatsApp message dictionary with 'text', 'buttons', etc.
        Format depends on outcome status:
        - NEEDS_CLARIFICATION: Returns text message from template
        - AWAITING_CONFIRMATION: Returns confirmation prompt
        - EXECUTED: Returns success confirmation message
        - etc.

    Raises:
        ValueError: If template is missing required fields or template not found
    """
    outcome_status = outcome.get("status")

    if outcome_status == "NEEDS_CLARIFICATION":
        template_key = outcome.get("template_key")
        if not template_key:
            raise ValueError(
                "CLARIFY outcome missing template_key. "
                "All clarification outcomes must have a template_key for rendering."
            )

        data = outcome.get("data", {})
        
        # Require structured reason from data - this is the authoritative source
        reason = data.get("reason")
        if not reason:
            raise ValueError(
                f"CLARIFY outcome missing data.reason. Data: {data}. "
                "All clarification outcomes must have data.reason for template lookup."
            )
        
        # Look up template by reason - template must exist
        template_def = _TEMPLATE_REGISTRY.get(reason)
        if not template_def:
            # Try fallback to NEEDS_CLARIFICATION template
            template_def = _TEMPLATE_REGISTRY.get("NEEDS_CLARIFICATION")
            if not template_def:
                raise ValueError(
                    f"Template not found for reason {reason!r} (from template_key {template_key!r}). "
                    f"Available templates: {list(_TEMPLATE_REGISTRY.keys())}. "
                    "All clarification reasons must have corresponding templates."
                )

        # Merge slots data with clarification data (slots take precedence for template variables)
        slots_data = _extract_slots_from_outcome(outcome)
        # Merge: slots_data first (for template variables), then data (for clarification-specific fields)
        merged_data = {**slots_data, **data}
        
        missing_fields = _validate_required_fields(template_key, template_def, merged_data)
        if missing_fields:
            raise ValueError(
                f"Template {template_key!r} missing required fields: {missing_fields}. "
                f"Available data: {list(merged_data.keys())}. "
                "All required template fields must be provided."
            )

        template_text = template_def.get("template")
        if not template_text:
            raise ValueError(
                f"Template {template_key!r} has no 'template' field. "
                "Template definition must include a 'template' string."
            )
        rendered_text = _interpolate_template(template_text, merged_data)

        return {
            "text": rendered_text,
            "type": "text"
        }

    elif outcome_status == "AWAITING_CONFIRMATION":
        # Render user confirmation prompt using template
        # Extract slots from facts.slots
        slots_data = _extract_slots_from_outcome(outcome)
        
        # Look up template by status - template must exist
        template_key = "AWAITING_CONFIRMATION"
        template_def = _TEMPLATE_REGISTRY.get(template_key)
        
        if not template_def:
            raise ValueError(
                f"Template not found for status {template_key!r}. "
                f"Available templates: {list(_TEMPLATE_REGISTRY.keys())}. "
                "AWAITING_CONFIRMATION status requires a template."
            )
        
        # Validate required fields
        missing_fields = _validate_required_fields(template_key, template_def, slots_data)
        if missing_fields:
            raise ValueError(
                f"Template {template_key!r} missing required fields: {missing_fields}. "
                f"Available data: {list(slots_data.keys())}. "
                "All required template fields must be provided."
            )
        
        template_text = template_def.get("template")
        if not template_text:
            raise ValueError(
                f"Template {template_key!r} has no 'template' field. "
                "Template definition must include a 'template' string."
            )
        rendered_text = _interpolate_template(template_text, slots_data)
        
        return {
            "text": rendered_text,
            "type": "text"
        }

    elif outcome_status == "EXECUTED":
        # Handle successful booking creation (status is EXECUTED after commit action executes)
        # Extract slots from facts.slots for template variables
        slots_data = _extract_slots_from_outcome(outcome)
        
        # Add booking_code and status if available (these are outcome-level, not in slots)
        # Check slots first, then outcome
        facts = outcome.get("facts", {})
        slots = facts.get("slots", {})
        booking_code = slots.get("booking_code") or outcome.get("booking_code")
        if booking_code:
            slots_data["booking_code"] = booking_code
        booking_status = slots.get("booking_status") or outcome.get("booking_status") or "confirmed"
        slots_data["booking_status"] = booking_status
        
        # Look up template by status - template must exist
        template_key = "EXECUTED"
        template_def = _TEMPLATE_REGISTRY.get(template_key)
        
        if not template_def:
            raise ValueError(
                f"Template not found for status {template_key!r}. "
                f"Available templates: {list(_TEMPLATE_REGISTRY.keys())}. "
                "EXECUTED status requires a template."
            )
        
        # Validate required fields
        missing_fields = _validate_required_fields(template_key, template_def, slots_data)
        if missing_fields:
            raise ValueError(
                f"Template {template_key!r} missing required fields: {missing_fields}. "
                f"Available data: {list(slots_data.keys())}. "
                "All required template fields must be provided."
            )
        
        template_text = template_def.get("template")
        if not template_text:
            raise ValueError(
                f"Template {template_key!r} has no 'template' field. "
                "Template definition must include a 'template' string."
            )
        rendered_text = _interpolate_template(template_text, slots_data)
        
        return {
            "text": rendered_text,
            "type": "text"
        }

    else:
        # Unknown outcome status - must have a template
        # Try to look up template by status name
        template_def = _TEMPLATE_REGISTRY.get(outcome_status)
        if template_def:
            slots_data = _extract_slots_from_outcome(outcome)
            missing_fields = _validate_required_fields(outcome_status, template_def, slots_data)
            if missing_fields:
                raise ValueError(
                    f"Template {outcome_status!r} missing required fields: {missing_fields}. "
                    f"Available data: {list(slots_data.keys())}."
                )
            template_text = template_def.get("template")
            if not template_text:
                raise ValueError(
                    f"Template {outcome_status!r} has no 'template' field."
                )
            rendered_text = _interpolate_template(template_text, slots_data)
            return {
                "text": rendered_text,
                "type": "text"
            }
        
        # No template found for unknown status
        raise ValueError(
            f"Unknown outcome status: {outcome_status!r}. "
            f"No template found. Available templates: {list(_TEMPLATE_REGISTRY.keys())}. "
            "All outcome statuses must have corresponding templates."
        )

