"""
Dialog Policy Module

Provides advisory dialog prompts based on intent and missing slots.
Prompts are advisory and do not enforce order - users can provide missing slots in any order.
"""

from .policy import get_dialog_instructions, load_dialog_policy

__all__ = ["get_dialog_instructions", "load_dialog_policy"]

