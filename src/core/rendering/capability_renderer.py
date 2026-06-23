"""
Capability Renderer

Renders text for capability states (AWAITING_CAPABILITY).

This module provides deterministic rendering from semantic signals (status, active_capability, facts).
Core renderer is the source of truth for capability presentation text.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .clarification_renderer import RenderSpec

# Import at module level for test patching
try:
    from extensions.capabilities.registry import get_adapter
except ImportError:
    # Graceful handling if capabilities module not available
    get_adapter = None

logger = logging.getLogger(__name__)

# Cache for loaded templates
_TEMPLATES_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def _load_capability_templates() -> Dict[str, Dict[str, Any]]:
    """
    Load capability templates from YAML configuration.

    Templates are cached after first load for performance.

    Returns:
        Dictionary mapping capability template key strings to template configs

    Raises:
        FileNotFoundError: If templates/capabilities.yaml not found
        yaml.YAMLError: If YAML is invalid
    """
    global _TEMPLATES_CACHE

    if _TEMPLATES_CACHE is not None:
        return _TEMPLATES_CACHE

    # Find templates directory relative to this file
    # capability_renderer.py is at: src/core/rendering/capability_renderer.py
    # YAML is at: src/core/rendering/templates/capabilities.yaml
    current_file = Path(__file__)
    templates_path = current_file.parent / "templates" / "capabilities.yaml"

    if not templates_path.exists():
        raise FileNotFoundError(
            f"Capability templates not found at {templates_path}. "
            f"Expected location: src/core/rendering/templates/capabilities.yaml"
        )

    with open(templates_path, "r", encoding="utf-8") as f:
        _TEMPLATES_CACHE = yaml.safe_load(f)

    if not isinstance(_TEMPLATES_CACHE, dict):
        raise ValueError(
            f"Invalid template file format: expected dictionary, got {type(_TEMPLATES_CACHE)}"
        )

    return _TEMPLATES_CACHE


def _render_capability_template(capability: str, data: Dict[str, Any]) -> Optional[str]:
    """
    Render capability text from template.

    Args:
        capability: Capability name (e.g., "PAYMENT")
        data: Dictionary of values for template interpolation

    Returns:
        Rendered text string, or None if template not found or rendering fails
    """
    try:
        templates = _load_capability_templates()
    except (FileNotFoundError, yaml.YAMLError, ValueError):
        # Best-effort: if templates can't be loaded, return None
        return None

    # Try capability-specific template first
    specific_template_key = f"CAPABILITY__{capability.upper()}"

    # Check if specific template exists
    if specific_template_key in templates:
        template_config = templates[specific_template_key]

        if isinstance(template_config, dict) and "template" in template_config:
            template = template_config["template"]
            required_fields = template_config.get("required_fields", [])

            if isinstance(required_fields, list):
                # Validate required fields
                missing_fields = [
                    field for field in required_fields if field not in data
                ]
                if missing_fields:
                    # If required_fields missing, return None immediately
                    # DO NOT fall back to generic template
                    return None

                # Extract all placeholders from template
                placeholders = re.findall(r"\{\{(\w+)\}\}", template)

                # Validate all placeholders are present in data
                missing_placeholders = [
                    placeholder
                    for placeholder in placeholders
                    if placeholder not in data
                ]
                if missing_placeholders:
                    # If placeholders missing, return None immediately
                    return None

                # Interpolate template
                rendered = template
                for placeholder in placeholders:
                    value = str(data[placeholder])
                    rendered = rendered.replace(f"{{{{{placeholder}}}}}", value)

                # Strip trailing newlines from YAML multiline strings
                return rendered.strip()

    # Only try generic CAPABILITY template if specific template doesn't exist
    if "CAPABILITY" in templates:
        template_config = templates["CAPABILITY"]

        if isinstance(template_config, dict) and "template" in template_config:
            template = template_config["template"]
            required_fields = template_config.get("required_fields", [])

            if isinstance(required_fields, list):
                # Validate required fields
                missing_fields = [
                    field for field in required_fields if field not in data
                ]
                if missing_fields:
                    return None

                # Extract all placeholders from template
                placeholders = re.findall(r"\{\{(\w+)\}\}", template)

                # Validate all placeholders are present in data
                missing_placeholders = [
                    placeholder
                    for placeholder in placeholders
                    if placeholder not in data
                ]
                if missing_placeholders:
                    return None

                # Interpolate template
                rendered = template
                for placeholder in placeholders:
                    value = str(data[placeholder])
                    rendered = rendered.replace(f"{{{{{placeholder}}}}}", value)

                # Strip trailing newlines from YAML multiline strings
                return rendered.strip()

    # No template found
    return None


def render_capability(
    status: str,
    active_capability: Optional[str],
    facts: Dict[str, Any],
    slots: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Optional[RenderSpec]:
    """
    Render text for capability states.

    This function deterministically renders text from semantic signals:
    - status: Must be "AWAITING_CAPABILITY"
    - active_capability: Capability name (e.g., "payment")
    - facts: Facts dictionary (may contain capability-specific data)
    - slots: Slots dictionary (may contain booking info)
    - context: Optional context dictionary (for accessing external services if needed)

    Core renderer is the source of truth for capability presentation text.

    Args:
        status: Planning status (must be "AWAITING_CAPABILITY")
        active_capability: Active capability name (e.g., "payment")
        facts: Facts dictionary
        slots: Slots dictionary
        context: Optional context dictionary with session_slots, session_facts, etc.

    Returns:
        RenderSpec with rendered text, or None if capability not supported or status invalid
    """
    # Only render for AWAITING_CAPABILITY status
    if status != "AWAITING_CAPABILITY":
        return None

    if not active_capability:
        return None

    # Route to capability-specific renderer
    if active_capability == "payment":
        text = _render_payment_capability(facts, slots, context)
        if text is None:
            return None
        return RenderSpec(text=text)

    # Unknown capability - return None
    logger.debug(f"Unknown capability for rendering: {active_capability}")
    return None


def _render_payment_capability(
    facts: Dict[str, Any],
    slots: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Render payment capability text.

    Replicates PaymentAdapter.start() text generation logic:
    - Extracts booking info from context/slots/facts
    - Gets payment URL from payment client
    - Returns payment link message

    Args:
        facts: Facts dictionary
        slots: Slots dictionary
        context: Context dictionary with session_slots, session_facts, etc.

    Returns:
        Payment link message string, or None if payment setup fails
    """
    # Extract booking info (same logic as PaymentAdapter._extract_booking_info)
    session_facts = {}
    session_slots = {}

    if context:
        session_facts = context.get("session_facts", {})
        session_slots = context.get("session_slots", {})

    # Merge with facts and slots from current turn
    if isinstance(facts, dict):
        session_facts = {**session_facts, **facts}
    if isinstance(slots, dict):
        session_slots = {**session_slots, **slots}

    # Extract booking_code (required for payment URL)
    booking_code = None
    if "booking_code" in session_slots:
        booking_code = session_slots["booking_code"]
    elif "booking_code" in session_facts:
        booking_code = session_facts["booking_code"]

    # Try to derive from booking_id if available
    if not booking_code:
        booking_id = session_slots.get("booking_id") or session_facts.get("booking_id")
        if booking_id:
            booking_code = f"booking_{booking_id}"

    if not booking_code:
        logger.warning("Payment capability rendering: booking_code not found")
        return None

    # Get payment URL from payment client (same as adapter)
    try:
        # Use module-level get_adapter (imported at top)
        if get_adapter is None:
            logger.debug(
                "Payment adapter not available - capabilities module not imported"
            )
            return None

        try:
            # Get payment adapter to access its client
            payment_adapter = get_adapter("payment")
            if not hasattr(payment_adapter, "payment_client"):
                logger.debug("Payment adapter does not have payment_client")
                return None

            payment_client = payment_adapter.payment_client
        except KeyError:
            # Adapter not registered - rendering cannot proceed
            logger.debug(
                "Payment adapter not registered - skipping capability rendering"
            )
            return None

        # Get payment URL (same as adapter.start())
        url_response = payment_client.get_payment_url(booking_code)

        if not url_response.get("success") or not url_response["data"].get(
            "has_payment_intent"
        ):
            return "Payment link not available. Please try again."

        payment_url = url_response["data"].get("payment_url")
        if not payment_url:
            return "Payment link not available. Please try again."

        # Render from template (with fallback to hardcoded string)
        rendered = _render_capability_template(
            capability="PAYMENT", data={"payment_url": payment_url}
        )

        # Fallback to original hardcoded string if template missing
        if rendered is None:
            return (
                "Your booking is being held for 30 minutes.\n"
                "Please complete payment using the link below:\n\n"
                f"{payment_url}"
            )

        return rendered

    except ImportError:
        logger.debug("Payment client not available for capability rendering")
        return None
    except Exception as e:
        logger.warning(f"Payment capability rendering error: {e}")
        return None
