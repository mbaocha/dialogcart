"""
Stage 1: Entity Extraction & Parameterization

Extracts entities from raw text using spaCy and fuzzy matching,
then parameterizes the sentence for NER classification.

Modular structure:
- normalization.py: Text normalization utilities
- entity_loading.py: Entity loading and pattern building
- entity_processing.py: Entity extraction and canonicalization
- matcher.py: Main EntityMatcher class
"""

# Main class
from .matcher import EntityMatcher

# Normalization utilities
from .normalization import (
    normalize_hyphens,
    pre_normalization,
    post_normalize_parameterized_text,
    normalize_longest_phrases,
    normalize_plural_to_singular,
)

# Entity loading utilities
from .entity_loading import (
    load_global_entities,
    build_global_synonym_map,
    build_entity_patterns,
    build_support_maps,
    init_nlp_with_entities,
)

# Processing utilities (for advanced usage)
from .entity_processing import (
    extract_entities_from_doc,
    canonicalize_entities,
    simplify_result,
)

__all__ = [
    # Main class
    "EntityMatcher",
    
    # Normalization
    "normalize_hyphens",
    "pre_normalization",
    "post_normalize_parameterized_text",
    "normalize_longest_phrases",
    "normalize_plural_to_singular",
    
    # Entity loading
    "load_global_entities",
    "build_global_synonym_map",
    "build_entity_patterns",
    "build_support_maps",
    "init_nlp_with_entities",
    
    # Processing (advanced)
    "extract_entities_from_doc",
    "canonicalize_entities",
    "simplify_result",
]

