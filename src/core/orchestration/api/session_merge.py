"""
Session Merge Helper (backward-compatible re-exports).

Implementation lives in core.session.* modules.
"""

from core.session.effective_slots import (
    _compute_effective_collected_slots,
    _compute_effective_collected_slots_internal,
)
from core.session.merge import merge_luma_with_session, merge_session_with_luma_response
from core.session.persist import build_session_state_from_outcome

__all__ = [
    "merge_luma_with_session",
    "merge_session_with_luma_response",
    "_compute_effective_collected_slots",
    "_compute_effective_collected_slots_internal",
    "build_session_state_from_outcome",
]
