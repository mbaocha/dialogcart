"""
Adapters to convert between legacy dict-based format and new typed format.

This module enables gradual migration by providing bidirectional conversion
between the old semantics/ dict structures and new luma typed structures.

During Phase 2-3, both systems can coexist and communicate through these adapters.
"""
from typing import Dict, Any, List, Optional
from luma.data_types import (
    ProcessingStatus,
    ExtractionResult,
    EntityGroup,
    GroupingResult,
    NLPExtraction,
    NERPrediction,
)


def from_legacy_result(legacy_dict: Dict[str, Any]) -> ExtractionResult:
    """
    Convert old dict-based extraction result to new typed format.
    
    Args:
        legacy_dict: Dictionary from semantics/entity_extraction_pipeline.py
        
    Returns:
        ExtractionResult with typed data
        
    Example:
        >>> legacy = {
        ...     "status": "success",
        ...     "original_sentence": "add rice",
        ...     "grouped_entities": {"groups": [{"action": "add", "products": ["rice"]}]}
        ... }
        >>> result = from_legacy_result(legacy)
        >>> assert isinstance(result, ExtractionResult)
    """
    # Map status string to enum
    status_map = {
        "success": ProcessingStatus.SUCCESS,
        "error": ProcessingStatus.ERROR,
        "needs_llm_fix": ProcessingStatus.NEEDS_LLM,
        "no_entities_found": ProcessingStatus.NO_ENTITIES,
    }
    status = status_map.get(
        legacy_dict.get("status", "error"),
        ProcessingStatus.ERROR
    )
    
    # Convert grouped entities
    groups = []
    grouped_entities = legacy_dict.get("grouped_entities", {})
    for g in grouped_entities.get("groups", []):
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
    
    # Convert NLP entities if present
    nlp_extraction = None
    if "nlp_entities" in legacy_dict and legacy_dict["nlp_entities"]:
        nlp_data = legacy_dict["nlp_entities"]
        nlp_extraction = NLPExtraction(
            products=nlp_data.get("products", []),
            brands=nlp_data.get("brands", []),
            units=nlp_data.get("units", []),
            quantities=nlp_data.get("quantities", []),
            variants=nlp_data.get("variants", []),
            likely_products=nlp_data.get("likely_products", []),
            likely_brands=nlp_data.get("likely_brands", []),
            likely_variants=nlp_data.get("likely_variants", []),
            productbrands=nlp_data.get("productbrands", []),
            original_sentence=nlp_data.get("osentence", legacy_dict.get("original_sentence", "")),
            parameterized_sentence=nlp_data.get("psentence", legacy_dict.get("parameterized_sentence", ""))
        )
    
    # Build grouping result
    grouping_result = None
    if grouped_entities:
        grouping_result = GroupingResult(
            groups=groups,
            status=grouped_entities.get("status", "ok"),
            reason=grouped_entities.get("reason")
        )
    
    return ExtractionResult(
        status=status,
        original_sentence=legacy_dict.get("original_sentence", ""),
        parameterized_sentence=legacy_dict.get("parameterized_sentence", ""),
        groups=groups,
        nlp_extraction=nlp_extraction,
        grouping_result=grouping_result,
        notes=legacy_dict.get("notes", ""),
        index_map=legacy_dict.get("index_map", {})
    )


def to_legacy_result(result: ExtractionResult) -> Dict[str, Any]:
    """
    Convert new typed format back to old dict-based format.
    
    Args:
        result: ExtractionResult from luma pipeline
        
    Returns:
        Dictionary compatible with old semantics/ format
        
    Example:
        >>> result = ExtractionResult(
        ...     status=ProcessingStatus.SUCCESS,
        ...     original_sentence="add rice",
        ...     parameterized_sentence="add producttoken",
        ...     groups=[EntityGroup(action="add", products=["rice"])]
        ... )
        >>> legacy = to_legacy_result(result)
        >>> assert legacy["status"] == "success"
    """
    # Convert groups to dict format
    groups_dict = []
    for group in result.groups:
        groups_dict.append({
            "action": group.action,
            "intent": group.intent,
            "intent_confidence": group.intent_confidence,
            "products": group.products,
            "brands": group.brands,
            "quantities": group.quantities,
            "units": group.units,
            "variants": group.variants,
            "ordinal_ref": group.ordinal_ref,  # ✅ Include ordinal reference
        })
    
    # Convert NLP extraction if present
    nlp_entities = None
    if result.nlp_extraction:
        nlp = result.nlp_extraction
        nlp_entities = {
            "products": nlp.products,
            "brands": nlp.brands,
            "units": nlp.units,
            "quantities": nlp.quantities,
            "variants": nlp.variants,
            "likely_products": nlp.likely_products,
            "likely_brands": nlp.likely_brands,
            "likely_variants": nlp.likely_variants,
            "productbrands": nlp.productbrands,
            "osentence": nlp.original_sentence,
            "psentence": nlp.parameterized_sentence,
        }
    
    # Build legacy dict
    return {
        "status": result.status.value,
        "original_sentence": result.original_sentence,
        "parameterized_sentence": result.parameterized_sentence,
        "grouped_entities": {
            "status": result.grouping_result.status if result.grouping_result else "ok",
            "reason": result.grouping_result.reason if result.grouping_result else None,
            "route": result.grouping_result.route if result.grouping_result else None,  # ✅ Include route
            "groups": groups_dict
        },
        "nlp_entities": nlp_entities,
        "notes": result.notes,
        "index_map": result.index_map,
    }


def from_legacy_nlp_result(legacy_nlp: Dict[str, Any]) -> NLPExtraction:
    """
    Convert legacy NLP result dict to typed NLPExtraction.
    
    Args:
        legacy_nlp: Dict from nlp_processor.extract_entities_with_parameterization()
        
    Returns:
        Typed NLPExtraction
    """
    return NLPExtraction(
        products=legacy_nlp.get("products", []),
        brands=legacy_nlp.get("brands", []),
        units=legacy_nlp.get("units", []),
        quantities=legacy_nlp.get("quantities", []),
        variants=legacy_nlp.get("variants", []),
        likely_products=legacy_nlp.get("likely_products", []),
        likely_brands=legacy_nlp.get("likely_brands", []),
        likely_variants=legacy_nlp.get("likely_variants", []),
        productbrands=legacy_nlp.get("productbrands", []),
        original_sentence=legacy_nlp.get("osentence", ""),
        parameterized_sentence=legacy_nlp.get("psentence", "")
    )


def from_legacy_ner_result(legacy_ner: Dict[str, Any]) -> NERPrediction:
    """
    Convert legacy NER result dict to typed NERPrediction.
    
    Args:
        legacy_ner: Dict from ner_inference.process_text()
        
    Returns:
        Typed NERPrediction
    """
    return NERPrediction(
        tokens=legacy_ner.get("tokens", []),
        labels=legacy_ner.get("labels", []),
        scores=legacy_ner.get("scores", [])
    )


def validate_conversion(legacy: Dict[str, Any], converted: ExtractionResult) -> bool:
    """
    Validate that conversion from legacy to typed format preserves data.
    
    Args:
        legacy: Original legacy dict
        converted: Converted ExtractionResult
        
    Returns:
        True if conversion is lossless, False otherwise
    """
    # Check basic fields
    if converted.original_sentence != legacy.get("original_sentence", ""):
        return False
    
    if converted.status.value != legacy.get("status", ""):
        return False
    
    # Check group count
    legacy_groups = legacy.get("grouped_entities", {}).get("groups", [])
    if len(converted.groups) != len(legacy_groups):
        return False
    
    # Check each group
    for conv_group, leg_group in zip(converted.groups, legacy_groups):
        if conv_group.action != leg_group.get("action", ""):
            return False
        if conv_group.products != leg_group.get("products", []):
            return False
    
    return True

