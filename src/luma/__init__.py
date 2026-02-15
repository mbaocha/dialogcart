"""
Luma - Service/Reservation Booking Pipeline

A clean, typed, testable service/reservation booking system.

This package provides:
- Entity extraction (services, dates, times)
- Intent resolution
- Structural interpretation
- Semantic resolution
- Calendar binding
- Clarification system
"""

from luma.calendar.calendar_binder import bind_calendar

# Export clarification system
from luma.clarification import Clarification, ClarificationReason, render_clarification

# Export configuration
from luma.config import LumaConfig, config

# Export core pipeline components
from luma.extraction.matcher import EntityMatcher
from luma.grouping.appointment_grouper import group_appointment
from luma.grouping.reservation_intent_resolver import ReservationIntentResolver

# Export pipeline orchestrator
from luma.pipeline import LumaPipeline
from luma.resolution.semantic_resolver import resolve_semantics
from luma.structure.interpreter import interpret_structure

# Note: Legacy data_types.py has been removed (e-commerce specific)
# Service/reservation workflow uses its own types in structure/, resolution/, calendar/

# Optional: EntityClassifier (if available)
try:
    from luma.extraction import EntityClassifier
except ImportError:
    EntityClassifier = None

# Optional features
try:
    from luma.extraction import FUZZY_AVAILABLE, FuzzyEntityMatcher
except ImportError:
    FuzzyEntityMatcher = None
    FUZZY_AVAILABLE = False

try:
    from luma.cli.interactive import interactive_main

    CLI_AVAILABLE = True
except ImportError:
    interactive_main = None
    CLI_AVAILABLE = False

__version__ = "1.0.0"
__author__ = "DialogCart Team"

__all__ = [
    # Configuration
    "config",
    "LumaConfig",
    # Clarification System
    "ClarificationReason",
    "Clarification",
    "render_clarification",
    # Core Pipeline Components
    "EntityMatcher",
    "ReservationIntentResolver",
    "interpret_structure",
    "group_appointment",
    "resolve_semantics",
    "bind_calendar",
    # Optional Components
    "EntityClassifier",
    "FuzzyEntityMatcher",
    "FUZZY_AVAILABLE",
    # CLI
    "interactive_main",
    "CLI_AVAILABLE",
]
