"""
Entity extraction pipeline implementation.

Phase 2: Wrapper that delegates to semantics/ code but returns typed results
Phase 3+: Will gradually replace with clean luma implementations

The pipeline can be controlled via environment variable:
    USE_LUMA_PIPELINE=true  -> Use new luma implementation (Phase 3+)
    USE_LUMA_PIPELINE=false -> Use legacy semantics code (default)
"""
import os
import sys
from typing import Optional
from pathlib import Path

from luma.data_types import ExtractionResult, ProcessingStatus, NERPrediction
from luma.adapters import from_legacy_result, to_legacy_result, from_legacy_ner_result


# Debug logging
DEBUG_ENABLED = os.getenv("DEBUG_NLP", "0") == "1"


def debug_print(*args, **kwargs):
    """Print debug message only if DEBUG_ENABLED is True."""
    if DEBUG_ENABLED:
        print(*args, **kwargs)


# Feature flags
USE_LUMA = os.getenv("USE_LUMA_PIPELINE", "false").lower() == "true"
USE_LUMA_NER = os.getenv("USE_LUMA_NER", "false").lower() == "true"  # Phase 3A: NER only


def _get_semantics_path() -> str:
    """Get the path to semantics/ directory."""
    # From luma/core/pipeline.py -> go up to src/intents/semantics
    current_file = Path(__file__).resolve()
    semantics_path = current_file.parent.parent.parent / "intents" / "semantics"
    return str(semantics_path)


def _import_legacy_extract():
    """
    Dynamically import the legacy extract_entities function from semantics/.
    
    This avoids polluting imports and makes the dependency explicit.
    """
    semantics_path = _get_semantics_path()
    
    if semantics_path not in sys.path:
        sys.path.insert(0, semantics_path)
    
    try:
        from entity_extraction_pipeline import extract_entities as legacy_extract
        return legacy_extract
    except ImportError as e:
        raise ImportError(
            f"Cannot import from semantics/: {e}\n"
            f"Tried path: {semantics_path}\n"
            "Make sure semantics/entity_extraction_pipeline.py exists."
        )


class EntityExtractionPipeline:
    """
    Entity extraction pipeline.
    
    Phase 2: Wraps legacy semantics/ code
    Phase 3+: Will use clean luma implementations
    
    Example:
        >>> pipeline = EntityExtractionPipeline()
        >>> result = pipeline.extract("add 2kg rice")
        >>> print(result.groups[0].products)
        ['rice']
    """
    
    def __init__(
        self,
        use_luma: Optional[bool] = None,
        use_luma_ner: Optional[bool] = None,
        entity_file: Optional[str] = None
    ):
        """
        Initialize pipeline.
        
        Args:
            use_luma: Force use of full luma implementation (overrides env var).
                     None = use env var, True = luma, False = legacy
            use_luma_ner: Force use of luma NER only (overrides env var).
                         Phase 3A: Use clean NER, keep rest as legacy
            entity_file: Optional custom entity file (for luma mode)
        """
        self.use_luma = use_luma if use_luma is not None else USE_LUMA
        self.use_luma_ner = use_luma_ner if use_luma_ner is not None else USE_LUMA_NER
        
        # Load legacy if not using full luma
        self._legacy_extract = None
        if not self.use_luma:
            self._legacy_extract = _import_legacy_extract()
        
        # Phase 3D: Initialize luma components if using full luma
        self.entity_matcher = None
        self.ner_model = None
        self.grouper = None
        
        if self.use_luma:
            # Full luma pipeline - using new stage-based structure
            from luma.extraction import EntityMatcher
            from luma.classification import NERModel
            from luma.grouping import decide_processing_path, index_parameterized_tokens
            
            self.entity_matcher = EntityMatcher(entity_file=entity_file)
            self.ner_model = NERModel()
            self.grouper = decide_processing_path
            self.indexer = index_parameterized_tokens
            
        elif self.use_luma_ner:
            # Hybrid: NER only
            from luma.classification import NERModel
            self.ner_model = NERModel()
    
    def extract(self, sentence: str, debug: bool = False) -> ExtractionResult:
        """
        Extract entities from sentence.
        
        Args:
            sentence: Input text to extract entities from
            debug: Enable debug logging
            
        Returns:
            ExtractionResult with extracted entity groups
            
        Example:
            >>> pipeline = EntityExtractionPipeline()
            >>> result = pipeline.extract("add 2kg rice to cart")
            >>> assert result.is_successful()
            >>> assert len(result.groups) == 1
        """
        if self.use_luma:
            # Phase 3+: Use full luma implementation
            return self._extract_with_luma(sentence, debug)
        elif self.use_luma_ner:
            # Phase 3A: Hybrid - use luma NER, keep rest as legacy
            return self._extract_hybrid_ner(sentence, debug)
        else:
            # Phase 2: Delegate to legacy code
            return self._extract_with_legacy(sentence, debug)
    
    def _extract_with_legacy(self, sentence: str, debug: bool) -> ExtractionResult:
        """
        Extract using legacy semantics/ code.
        
        Calls the old extract_entities function and converts result to typed format.
        """
        try:
            # Call legacy extractor
            legacy_result = self._legacy_extract(sentence, debug=debug)
            
            # Convert to typed format
            typed_result = from_legacy_result(legacy_result)
            
            return typed_result
            
        except Exception as e:
            # Handle errors gracefully
            return ExtractionResult(
                status=ProcessingStatus.ERROR,
                original_sentence=sentence,
                parameterized_sentence="",
                notes=f"Legacy extraction failed: {str(e)}"
            )
    
    def _extract_hybrid_ner(self, sentence: str, debug: bool) -> ExtractionResult:
        """
        Hybrid extraction: Use luma NER, delegate rest to semantics.
        
        Phase 3A: This allows testing NER in production while keeping
        entity matching and grouping logic stable.
        
        Flow:
        1. Get parameterized sentence from semantics (NLP + matching)
        2. Use luma NER model instead of semantics NER
        3. Continue with semantics grouping
        4. Convert result to typed format
        """
        try:
            # This would require modifying semantics to accept external NER
            # For now, just use full legacy and swap NER internally
            # TODO: Implement proper NER injection
            
            # Fallback to legacy for now
            legacy_result = self._legacy_extract(sentence, debug=debug)
            typed_result = from_legacy_result(legacy_result)
            
            # Add note that NER swapping needs implementation
            typed_result.notes += " (NER hybrid mode needs implementation)"
            
            return typed_result
            
        except Exception as e:
            return ExtractionResult(
                status=ProcessingStatus.ERROR,
                original_sentence=sentence,
                parameterized_sentence="",
                notes=f"Hybrid NER extraction failed: {str(e)}"
            )
    
    def _extract_with_luma(self, sentence: str, debug: bool) -> ExtractionResult:
        """
        Extract using full luma implementation.
        
        Phase 3D: Complete pipeline using all luma components
        
        Flow:
        1. EntityMatcher extracts & parameterizes
        2. NERModel classifies tokens
        3. Grouper groups entities
        4. Return typed ExtractionResult
        """
        try:
            from luma.adapters import from_legacy_nlp_result
            
            # Step 1: Entity Matching & Parameterization
            debug_print("[LUMA Pipeline] Step 1: Entity Matching...")
            nlp_result_dict = self.entity_matcher.extract_with_parameterization(
                sentence,
                debug_units=debug
            )
            
            # Step 2: NER Classification
            debug_print("[LUMA Pipeline] Step 2: NER Classification...")
            parameterized_sentence = nlp_result_dict["psentence"]
            ner_result = self.ner_model.predict(parameterized_sentence)
            
            # Step 3: Index parameterized tokens
            debug_print("[LUMA Pipeline] Step 3: Indexing tokens...")
            indexed_tokens = self.indexer(ner_result.tokens)
            
            # Build index map
            index_map = {}
            for i, (original, indexed) in enumerate(zip(ner_result.tokens, indexed_tokens)):
                if original != indexed:
                    index_map[indexed] = original
            
            # Step 4: Grouping
            debug_print("[LUMA Pipeline] Step 4: Grouping...")
            grouped_result, route, route_reason = self.grouper(
                indexed_tokens,
                ner_result.labels,
                memory_state=None
            )
            
            # Step 5: Convert to typed result
            debug_print("[LUMA Pipeline] Step 5: Building result...")
            
            from luma.data_types import EntityGroup, GroupingResult
            
            groups = []
            for g in grouped_result.get("groups", []):
                group = EntityGroup(
                    action=g.get("action", ""),
                    intent=g.get("intent"),
                    intent_confidence=g.get("intent_confidence"),
                    products=g.get("products", []),
                    brands=g.get("brands", []),
                    quantities=g.get("quantities", []),
                    units=g.get("units", []),
                    variants=g.get("variants", [])
                )
                groups.append(group)
            
            grouping = GroupingResult(
                groups=groups,
                status=grouped_result.get("status", "ok"),
                reason=grouped_result.get("reason")
            )
            
            # Build NLPExtraction
            from luma.data_types import NLPExtraction
            nlp_extraction = NLPExtraction(
                products=nlp_result_dict.get("products", []),
                brands=nlp_result_dict.get("brands", []),
                units=nlp_result_dict.get("units", []),
                quantities=nlp_result_dict.get("quantities", []),
                variants=nlp_result_dict.get("variants", []),
                likely_products=nlp_result_dict.get("likely_products", []),
                likely_brands=nlp_result_dict.get("likely_brands", []),
                productbrands=nlp_result_dict.get("productbrands", []),
                original_sentence=nlp_result_dict.get("osentence", sentence),
                parameterized_sentence=parameterized_sentence
            )
            
            return ExtractionResult(
                status=ProcessingStatus.SUCCESS if grouping.is_successful() else ProcessingStatus.NEEDS_LLM,
                original_sentence=sentence,
                parameterized_sentence=parameterized_sentence,
                groups=groups,
                nlp_extraction=nlp_extraction,
                ner_prediction=ner_result,
                grouping_result=grouping,
                index_map=index_map,
                notes=f"Luma pipeline (route={route})"
            )
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return ExtractionResult(
                status=ProcessingStatus.ERROR,
                original_sentence=sentence,
                parameterized_sentence="",
                notes=f"Luma pipeline error: {str(e)}"
            )


def extract_entities(sentence: str, debug: bool = False) -> ExtractionResult:
    """
    Main API function for entity extraction (typed output).
    
    This is the primary entry point for the luma package.
    
    Args:
        sentence: Input text to extract entities from
        debug: Enable debug logging
        
    Returns:
        ExtractionResult with typed entity groups
        
    Example:
        >>> from luma import extract_entities
        >>> result = extract_entities("add 2kg rice")
        >>> print(result.groups[0].products)
        ['rice']
    """
    pipeline = EntityExtractionPipeline()
    return pipeline.extract(sentence, debug)


def extract_entities_legacy(sentence: str, debug: bool = False) -> dict:
    """
    Legacy API that returns old dict format.
    
    For backward compatibility during migration. Existing code that expects
    dict format can use this function.
    
    Args:
        sentence: Input text to extract entities from
        debug: Enable debug logging
        
    Returns:
        Dictionary in old semantics/ format
        
    Example:
        >>> result = extract_entities_legacy("add 2kg rice")
        >>> assert isinstance(result, dict)
        >>> assert "grouped_entities" in result
    """
    # Get typed result
    typed_result = extract_entities(sentence, debug)
    
    # Convert to legacy dict format
    return to_legacy_result(typed_result)

