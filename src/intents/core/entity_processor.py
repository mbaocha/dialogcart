"""
Entity Processor for cleaning and enhancing entity extraction.

This module handles entity-level operations like deduplication
to improve the quality of entity extraction before action mapping.
"""
from typing import List


def deduplicate_products(entities: List[dict]) -> List[dict]:
    """
    Deduplicate overlapping/nested product entities by preferring longer values.

    Strategy:
    - Consider only `product` entities with a truthy `value`.
    - Sort those product entities by len(value) descending.
    - Keep a product entity if its value is not a substring of any kept value (case-insensitive).
    - Preserve all non-product entities and the original relative order of kept items.

    This mitigates cases where Rasa emits both "red oil" and partials like "red" and "oil".
    """
    # Collect product entities
    product_entities = [e for e in entities if e.get("entity") == "product" and e.get("value")]

    # Sort products by length of value (desc)
    def _val_len(e: dict) -> int:
        return len(str(e.get("value", "")))

    sorted_products = sorted(product_entities, key=_val_len, reverse=True)

    kept: List[dict] = []
    kept_values_lower: List[str] = []
    for e in sorted_products:
        val = str(e.get("value", "")).lower()
        if not any(val in kv for kv in kept_values_lower):
            kept.append(e)
            kept_values_lower.append(val)

    kept_ids = {id(e) for e in kept}

    # Rebuild entity list preserving original order, but only keep deduped products
    deduped: List[dict] = []
    for e in entities:
        if e.get("entity") != "product":
            deduped.append(e)
        else:
            if id(e) in kept_ids:
                deduped.append(e)

    return deduped


def process_entities(entities: List[dict], _source_text: str = "") -> List[dict]:
    """
    Main entry point for entity processing pipeline.

    Currently applies only deduplication of overlapping product entities.
    """
    return deduplicate_products(entities)
