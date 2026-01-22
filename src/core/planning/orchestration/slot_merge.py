"""
Slot Merge

Merges session state with Luma response for follow-up handling.

This module provides pure functions for merging session state without
changing core logic.
"""

# Re-export from original location for backward compatibility
# The actual implementation remains in orchestration/api/session_merge.py
# This allows gradual migration while maintaining existing functionality
from core.orchestration.api.session_merge import (
    merge_luma_with_session,
    _compute_effective_collected_slots,
)

__all__ = [
    "merge_luma_with_session",
    "_compute_effective_collected_slots",
]

