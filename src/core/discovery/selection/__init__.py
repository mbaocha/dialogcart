"""Selector — resolve user choices from presented or trusted results.

Responsible for:
- resolving ambiguous selections from PresentedWindow
- resolving explicit selections from TrustedResult
- producing SelectionResult

Not responsible for:
- navigation
- searching
- booking
- planner decisions

Matching rules live in SelectionPolicy (domain) under Selector orchestration.
"""

from core.discovery.selection.policy import SelectionPolicy
from core.discovery.selection.selector import Selector

__all__ = [
    "Selector",
    "SelectionPolicy",
]
