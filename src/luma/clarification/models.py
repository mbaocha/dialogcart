"""
Clarification Model

Structured clarification data without message text.
Data must be serializable.
"""

from dataclasses import dataclass, field
from typing import Dict, Any

from .reasons import ClarificationReason


@dataclass
class Clarification:
    """
    Clarification dataclass.
    
    Contains reason and structured data only.
    No message text allowed in this object.
    """
    reason: ClarificationReason
    data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to JSON-serializable dictionary.
        
        Returns:
            Dictionary with reason value and data
        """
        return {
            "reason": self.reason.value,
            "data": self.data
        }

