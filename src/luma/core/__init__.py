"""
Core pipeline components for entity extraction.

DEPRECATED: This module re-exports from the new stage-based structure for backward compatibility.

New structure:
- luma.extraction (Stage 1)
- luma.classification (Stage 2)
- luma.grouping (Stage 3)
"""

# Backward compatibility: re-export from new locations
from luma.extraction import EntityMatcher
from luma.grouping import (
    group_appointment,
    BOOK_APPOINTMENT_INTENT,
    STATUS_OK,
    STATUS_NEEDS_CLARIFICATION,
)

__all__ = [
    "EntityMatcher",
    "group_appointment",
    "BOOK_APPOINTMENT_INTENT",
    "STATUS_OK",
    "STATUS_NEEDS_CLARIFICATION",
]

