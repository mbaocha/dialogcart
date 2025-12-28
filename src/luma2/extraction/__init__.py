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
    normalize_orthography,
    post_normalize_parameterized_text,
    normalize_natural_language_variants,
)

# Vocabulary normalization (new structure)
from .vocabulary_normalization import (
    load_vocabularies,
    compile_vocabulary_maps,
    normalize_vocabularies,
    validate_vocabularies,
)

# Entity loading utilities
from .entity_loading import (
    load_normalization_entities,
    load_global_noise_set,
    load_global_orthography_rules,
    build_entity_patterns,
    build_support_maps,
    init_nlp_with_entities,
)

# Processing utilities (for advanced usage)
from .entity_processing import (
    extract_entities_from_doc,
    build_parameterized_sentence,
    canonicalize_services,
)

# Fuzzy matching (optional - requires rapidfuzz)
try:
    from .fuzzy_matcher import TenantFuzzyMatcher
    FUZZY_AVAILABLE = True
except ImportError:
    TenantFuzzyMatcher = None
    FUZZY_AVAILABLE = False

__all__ = [
    # Main classes
    "EntityMatcher",

    # Normalization
    "normalize_hyphens",
    "pre_normalization",
    "normalize_orthography",
    "post_normalize_parameterized_text",
    "normalize_natural_language_variants",

    # Vocabulary normalization
    "load_vocabularies",
    "compile_vocabulary_maps",
    "normalize_vocabularies",
    "validate_vocabularies",

    # Entity loading
    "load_normalization_entities",
    "load_global_noise_set",
    "build_entity_patterns",
    "build_support_maps",
    "init_nlp_with_entities",

    # Processing (advanced)
    "extract_entities_from_doc",
    "build_parameterized_sentence",
    "canonicalize_services",

    # Fuzzy matching (optional)
    "TenantFuzzyMatcher",
    "FUZZY_AVAILABLE",
]
