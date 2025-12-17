"""
Structural interpretation layer for Luma.

Rule-based analysis of entity relationships and structure.
Replaces NER for structure modeling in service/appointment/reservation use cases.
"""

from luma.structure.structure_types import StructureResult
from luma.structure.interpreter import interpret_structure
from luma.structure.rules import (
    count_bookings,
    determine_service_scope,
    determine_time_type,
    determine_time_scope,
    check_has_duration,
    check_needs_clarification
)

__all__ = [
    "StructureResult",
    "interpret_structure",
    "count_bookings",
    "determine_service_scope",
    "determine_time_type",
    "determine_time_scope",
    "check_has_duration",
    "check_needs_clarification",
]

