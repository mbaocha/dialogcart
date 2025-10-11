"""
Luma - Entity Extraction Pipeline

A clean, typed, testable entity extraction system for e-commerce applications.

This package provides:
- Type-safe data structures (types.py)
- Entity extraction pipeline (core/)
- NER model implementation (models/)
- NER model training (ner_model_training.py)
- Adapters for backward compatibility (adapters.py)

Phase 1: ✅ Foundation - Type system
Phase 2: ✅ Wrapper Layer - Both systems coexist
Phase 3A: ✅ NER Model - Clean implementation
Phase 3B-C: 🔜 Entity matcher & Grouper
"""

# Export core types
from luma.data_types import (
    # Enums
    ProcessingStatus,
    
    # Data structures
    Entity,
    NLPExtraction,
    NERPrediction,
    EntityGroup,
    GroupingResult,
    ExtractionResult,
    
    # Type aliases
    EntityList,
    TokenList,
    LabelList,
    ScoreList,
)

# Phase 2: Export pipeline functions
from luma.core.pipeline import (
    extract_entities,
    extract_entities_legacy,
    EntityExtractionPipeline,
)

# Export adapters for advanced use
from luma.adapters import (
    from_legacy_result,
    to_legacy_result,
)

# Stage-based imports (new structure)
from luma.extraction import EntityMatcher
from luma.classification import NERModel
from luma.grouping import (
    simple_group_entities,
    index_parameterized_tokens,
    decide_processing_path,
)

# Backward compatibility: also available via old paths
# luma.models.NERModel still works
# luma.core.EntityMatcher still works

__version__ = "1.0.0"  # Phase 3D Complete - Full Integration! 🎉
__author__ = "DialogCart Team"

__all__ = [
    # Main API
    "extract_entities",          # New typed API (recommended)
    "extract_entities_legacy",   # Old dict API (backward compat)
    "EntityExtractionPipeline",  # Pipeline class
    
    # Components
    "NERModel",                  # NER model (Phase 3A)
    "EntityMatcher",             # Entity matcher (Phase 3B)
    
    # Grouping Functions
    "simple_group_entities",     # Grouping logic (Phase 3C)
    "index_parameterized_tokens", # Token indexing (Phase 3C)
    "decide_processing_path",    # Main grouper entry (Phase 3C)
    
    # Enums
    "ProcessingStatus",
    
    # Core types
    "Entity",
    "NLPExtraction",
    "NERPrediction",
    "EntityGroup",
    "GroupingResult",
    "ExtractionResult",
    
    # Type aliases
    "EntityList",
    "TokenList",
    "LabelList",
    "ScoreList",
    
    # Adapters (for migration)
    "from_legacy_result",
    "to_legacy_result",
]

