"""
Data types for structural interpretation layer.

Defines StructureResult dataclass for rule-based structure analysis.
"""
from dataclasses import dataclass
from typing import Literal


@dataclass
class StructureResult:
    """
    Structural interpretation result.
    
    Describes how extracted entities relate to each other,
    not what the entities are.
    """
    booking_count: int = 1
    service_scope: Literal["shared", "separate"] = "separate"
    time_scope: Literal["shared", "per_service"] = "shared"
    date_scope: Literal["shared"] = "shared"
    time_type: Literal["exact", "window", "range", "none"] = "none"
    has_duration: bool = False
    needs_clarification: bool = False
    
    def to_dict(self) -> dict:
        """Convert to dictionary format."""
        return {
            "structure": {
                "booking_count": self.booking_count,
                "service_scope": self.service_scope,
                "time_scope": self.time_scope,
                "date_scope": self.date_scope,
                "time_type": self.time_type,
                "has_duration": self.has_duration,
                "needs_clarification": self.needs_clarification
            }
        }

