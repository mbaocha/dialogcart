"""Navigator — navigate a trusted result set.

Responsible for:
- deriving PresentedWindow from TrustedResult
- handling browse/explore movement
- maintaining navigation state
- detecting exhaustion (via last_moved)

Not responsible for:
- searching
- binding selections
- planner policy

Grouping, paging, cursors, and traversal mechanics are owned by
NavigationPolicy (domain) under Navigator orchestration.
"""

from core.discovery.presentation.navigator import Navigator
from core.discovery.presentation.policy import NavigationPolicy

__all__ = [
    "Navigator",
    "NavigationPolicy",
]
