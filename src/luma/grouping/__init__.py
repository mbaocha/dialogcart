"""
Stage 3: Entity Grouping & Alignment

Groups classified entities by action and aligns quantities/units to products.
"""

from luma.grouping.grouper import (
    simple_group_entities,
    index_parameterized_tokens,
    decide_processing_path,
    extract_entities,
    align_quantities_to_products,
)

from luma.grouping.reverse_mapper import (
    map_tokens_to_original_values,
)

# Intent mapper is optional - only imported if enabled
try:
    from luma.grouping.intent_mapper import IntentMapper
    INTENT_MAPPER_AVAILABLE = True
except ImportError:
    IntentMapper = None
    INTENT_MAPPER_AVAILABLE = False

__all__ = [
    "simple_group_entities",
    "index_parameterized_tokens",
    "decide_processing_path",
    "extract_entities",
    "align_quantities_to_products",
    "map_tokens_to_original_values",
    "IntentMapper",
]

