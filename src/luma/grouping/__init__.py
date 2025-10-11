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

__all__ = [
    "simple_group_entities",
    "index_parameterized_tokens",
    "decide_processing_path",
    "extract_entities",
    "align_quantities_to_products",
]

