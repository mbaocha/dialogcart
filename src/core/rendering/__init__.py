"""
Rendering Layer

Converts structured outcome objects into WhatsApp messages.

This layer handles template lookup, variable interpolation, required fields
validation, and WhatsApp message formatting (text, buttons, etc.).

It consumes outcome objects only and does not call Luma or business APIs.
"""

from core.rendering.whatsapp_renderer import render_outcome_to_whatsapp

__all__ = ["render_outcome_to_whatsapp"]

