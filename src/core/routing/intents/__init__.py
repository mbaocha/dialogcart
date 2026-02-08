"""
Core Intent Definitions

This package defines the core-owned base intents that are orchestrated by core.
These intents represent the stable foundation for the booking system.
"""

from .base_intents import CORE_BASE_INTENTS, is_core_intent

__all__ = ["CORE_BASE_INTENTS", "is_core_intent"]

