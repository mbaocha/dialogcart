"""
Step 1: Non-destructive service annotation

Annotates tokens as ALIAS or FAMILY without removing them.
This preserves full sentence structure and token positions.
"""

from typing import Dict, Any, Optional, List, Tuple
import logging

logger = logging.getLogger(__name__)


def annotate_service_tokens(
    doc,
    alias_spans: List[Dict[str, Any]],
    services: List[Dict[str, Any]],
    business_category_map: Dict[str, str],
    tenant_aliases: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Step 1: Non-destructive annotation of service tokens.
    
    Marks tokens as:
    - ALIAS(token, tenant_service_id, canonical_family) for tenant aliases
    - FAMILY(token, canonical_family) for canonical family words
    - MODIFIER(token, canonical_family) for service modifiers (e.g., standard, deluxe)
    
    Does NOT remove or replace tokens - only annotates them.
    
    Args:
        doc: spaCy doc object
        alias_spans: List of tenant alias spans (from detect_tenant_alias_spans)
        services: List of service entities from extraction
        business_category_map: Map from natural language to canonical family IDs
        tenant_aliases: Optional tenant aliases dict to detect modifiers
    
    Returns:
        Dictionary with:
        - alias_annotations: List of ALIAS annotations
        - family_annotations: List of FAMILY annotations
        - modifier_annotations: List of MODIFIER annotations
        - annotated_sentence: Original sentence with annotations stored in metadata
    """
    alias_annotations: List[Dict[str, Any]] = []
    family_annotations: List[Dict[str, Any]] = []
    modifier_annotations: List[Dict[str, Any]] = []
    
    # Map alias spans to token ranges
    alias_token_ranges: List[Tuple[int, int, str, str]] = []  # (start, end, text, tenant_service_id)
    
    # Build set of canonical families from services (to detect modifiers)
    canonical_families_from_services = set()
    for service in services:
        entity_text = service.get("text", "").lower()
        canonical = service.get("canonical") or business_category_map.get(entity_text)
        if canonical:
            canonical_families_from_services.add(canonical)
    
    for span in alias_spans:
        from .matcher import _map_char_span_to_token_span
        mapped = _map_char_span_to_token_span(
            doc, span["start_char"], span["end_char"]
        )
        if not mapped:
            continue
        start_tok, end_tok = mapped
        # Contract: alias_key = tenant_service_id, alias_value = canonical_family
        # tenant_aliases dict: {"beard": "beard grooming"}
        #   - "beard" (key) = tenant_service_id
        #   - "beard grooming" (value) = canonical_family
        tenant_service_id = span.get("alias_key", "")
        canonical_family = span.get("canonical", "")  # alias value is canonical_family
        
        # Detect if this alias is a MODIFIER (modifies a FAMILY service)
        # Example: "standard": "room" where "room" is a FAMILY
        # If canonical_family is in the services, this alias is a modifier
        is_modifier = canonical_family in canonical_families_from_services and tenant_aliases
        
        if is_modifier:
            # This is a modifier (e.g., "standard" modifies "room")
            modifier_annotations.append({
                "type": "MODIFIER",
                "start_token": start_tok,
                "end_token": end_tok,
                "text": span["text"],
                "tenant_service_id": tenant_service_id,  # alias key
                "canonical_family": canonical_family  # alias value (the family it modifies)
            })
            # Modifiers are not added to alias_token_ranges - they don't block families
        else:
            # This is a concrete ALIAS (e.g., "beard" → "beard grooming")
            alias_annotations.append({
                "type": "ALIAS",
                "start_token": start_tok,
                "end_token": end_tok,
                "text": span["text"],
                "tenant_service_id": tenant_service_id,  # alias key
                "canonical_family": canonical_family  # alias value
            })
            alias_token_ranges.append((start_tok, end_tok, span["text"], tenant_service_id))
    
    # Annotate canonical family words (only if not overlapping with aliases)
    for service in services:
        start = service.get("position", 0)
        length = service.get("length", 1)
        end = start + length
        
        # Skip if overlaps with alias
        overlaps_alias = any(
            not (end <= a_start or start >= a_end)
            for a_start, a_end, _, _ in alias_token_ranges
        )
        if overlaps_alias:
            continue
        
        entity_text = service.get("text", "").lower()
        canonical_family = service.get("canonical") or business_category_map.get(entity_text)
        
        if canonical_family:
            family_annotations.append({
                "type": "FAMILY",
                "start_token": start,
                "end_token": end,
                "text": entity_text,
                "canonical_family": canonical_family
            })
    
    logger.debug(
        f"[annotation] Step 1: Annotated {len(alias_annotations)} ALIAS tokens, "
        f"{len(family_annotations)} FAMILY tokens, "
        f"{len(modifier_annotations)} MODIFIER tokens"
    )
    
    return {
        "alias_annotations": alias_annotations,
        "family_annotations": family_annotations,
        "modifier_annotations": modifier_annotations,
        "alias_token_ranges": [(a, b) for a, b, _, _ in alias_token_ranges]
    }


def consume_service_annotations(
    doc,
    annotations: Dict[str, Any],
    logger_instance: Optional[logging.Logger] = None
) -> Tuple[str, Dict[str, Any]]:
    """
    Step 2: Deterministic consumption pass.
    
    If tenant alias is present:
    - Consume alias token → replace with servicetenanttoken
    - Suppress any FAMILY tokens implied by that alias
    
    If no alias is present:
    - Parameterize canonical family words as servicefamilytoken
    
    Args:
        doc: spaCy doc object
        annotations: Output from annotate_service_tokens
        logger_instance: Optional logger for logging
    
    Returns:
        Tuple of (parameterized_sentence, consumption_metadata)
    """
    log = logger_instance or logger
    
    tokens = [t.text.lower() for t in doc]
    alias_annotations = annotations.get("alias_annotations", [])
    family_annotations = annotations.get("family_annotations", [])
    alias_token_ranges = set(annotations.get("alias_token_ranges", []))
    
    replacements = []
    consumption_metadata = {
        "has_alias": len(alias_annotations) > 0,
        "alias_replacements": [],
        "family_replacements": [],
        "suppressed_families": []
    }
    
    # Step 2a: If alias present, consume it and suppress overlapping families
    if alias_annotations:
        for alias_ann in alias_annotations:
            start = alias_ann["start_token"]
            end = alias_ann["end_token"]
            text = alias_ann["text"]
            tenant_service_id = alias_ann["tenant_service_id"]
            
            replacements.append((start, end, "servicetenanttoken"))
            consumption_metadata["alias_replacements"].append({
                "span": text,
                "tenant_service_id": tenant_service_id,
                "replaced_with": "servicetenanttoken"
            })
        
        # Suppress FAMILY tokens that overlap with aliases
        for family_ann in family_annotations:
            start = family_ann["start_token"]
            end = family_ann["end_token"]
            
            overlaps = any(
                not (end <= a_start or start >= a_end)
                for a_start, a_end in alias_token_ranges
            )
            if overlaps:
                consumption_metadata["suppressed_families"].append({
                    "span": family_ann["text"],
                    "canonical_family": family_ann["canonical_family"],
                    "reason": "overlaps_with_alias"
                })
            else:
                # Family token not suppressed - will be parameterized
                replacements.append((start, end, "servicefamilytoken"))
                consumption_metadata["family_replacements"].append({
                    "span": family_ann["text"],
                    "canonical_family": family_ann["canonical_family"],
                    "replaced_with": "servicefamilytoken"
                })
    else:
        # Step 2b: No alias present - parameterize all family tokens
        for family_ann in family_annotations:
            start = family_ann["start_token"]
            end = family_ann["end_token"]
            text = family_ann["text"]
            canonical_family = family_ann["canonical_family"]
            
            replacements.append((start, end, "servicefamilytoken"))
            consumption_metadata["family_replacements"].append({
                "span": text,
                "canonical_family": canonical_family,
                "replaced_with": "servicefamilytoken"
            })
    
    # Apply replacements backwards (end-to-start) to avoid index shifting
    replacements.sort(key=lambda x: (x[1], x[0]), reverse=True)
    for start, end, placeholder in replacements:
        tokens[start:end] = [placeholder]
    
    psentence = " ".join(tokens)
    
    log.debug(
        f"[consumption] Step 2: Consumed {len(consumption_metadata['alias_replacements'])} aliases, "
        f"{len(consumption_metadata['family_replacements'])} families, "
        f"{len(consumption_metadata['suppressed_families'])} suppressed"
    )
    
    return psentence, consumption_metadata

