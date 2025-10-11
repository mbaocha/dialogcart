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
    simple_group_entities,
    index_parameterized_tokens,
    decide_processing_path,
)

__all__ = [
    "EntityMatcher",
    "simple_group_entities",
    "index_parameterized_tokens",
    "decide_processing_path",
]

