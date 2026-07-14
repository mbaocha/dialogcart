"""HTTP / application API boundary for Core."""

from core.api.capability_boundary import apply_capability_to_result, build_capability_context
from core.api.compat import handle_message

__all__ = [
    "apply_capability_to_result",
    "build_capability_context",
    "handle_message",
]
