"""
Semantic Resolution Layer

Resolves semantic meaning from extracted entities and grouped intents.
Decides what the user means without binding to actual calendar dates.

This layer handles:
- Time precedence (exact vs window vs range)
- Date precedence (absolute vs relative)
- Conflict detection
- Ambiguity resolution

Does NOT:
- Bind to real dates/timestamps
- Infer years
- Check availability
"""

from luma.resolution.semantic_resolver import (
    resolve_semantics,
    SemanticResolutionResult,
)

__all__ = [
    "resolve_semantics",
    "SemanticResolutionResult",
]

