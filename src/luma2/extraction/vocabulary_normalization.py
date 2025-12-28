"""
Vocabulary-based normalization for Luma.

Loads vocabularies with { canonical: { synonyms, typos } } structure.
Normalizes input by mapping synonyms and typos to canonical forms.
"""
from pathlib import Path
from typing import Dict, Tuple, Set, Any
import json

from luma.config import debug_print


def load_vocabularies(global_json_path: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """
    Load vocabularies from global JSON with new structure:
    {
        "vocabularies": {
            "weekdays": {
                "monday": {
                    "synonyms": ["mon", "mondays"],
                    "typos": ["moneday", "munday"]
                }
            }
        }
    }
    
    Returns:
        Dictionary mapping vocabulary category -> { canonical -> { synonyms: [...], typos: [...] } }
    """
    with open(global_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    vocabularies = data.get("normalization", {}).get(
        "normalization", {}).get("vocabularies", {})
    return vocabularies


def compile_vocabulary_maps(
    vocabularies: Dict[str, Dict[str, Dict[str, Any]]]
) -> Tuple[Dict[str, str], Dict[str, str], Set[str]]:
    """
    Compile vocabulary maps from new structure.
    
    Returns:
        Tuple of:
        - synonym_map: variant -> canonical (for valid language)
        - typo_map: variant -> canonical (for invalid-but-recoverable)
        - all_canonicals: set of all canonical values
    """
    synonym_map: Dict[str, str] = {}
    typo_map: Dict[str, str] = {}
    all_canonicals: Set[str] = set()
    
    for category, vocab_dict in vocabularies.items():
        if category.startswith("_"):
            continue
        
        if not isinstance(vocab_dict, dict):
            continue
        
        for canonical, variants_dict in vocab_dict.items():
            if not isinstance(variants_dict, dict):
                continue
            
            canonical_lower = canonical.lower()
            all_canonicals.add(canonical_lower)
            
            # Process synonyms (valid language)
            synonyms = variants_dict.get("synonyms", [])
            if isinstance(synonyms, list):
                for synonym in synonyms:
                    if isinstance(synonym, str):
                        synonym_lower = synonym.lower()
                        if synonym_lower in synonym_map or synonym_lower in typo_map:
                            raise ValueError(
                                f"Duplicate variant '{synonym}' in category '{category}': "
                                f"appears in both synonyms and typos"
                            )
                        synonym_map[synonym_lower] = canonical_lower
            
            # Process typos (invalid-but-recoverable)
            typos = variants_dict.get("typos", [])
            if isinstance(typos, list):
                for typo in typos:
                    if isinstance(typo, str):
                        typo_lower = typo.lower()
                        if typo_lower in synonym_map or typo_lower in typo_map:
                            raise ValueError(
                                f"Duplicate variant '{typo}' in category '{category}': "
                                f"appears in both synonyms and typos"
                            )
                        typo_map[typo_lower] = canonical_lower
    
    return synonym_map, typo_map, all_canonicals


def normalize_vocabularies(
    text: str,
    synonym_map: Dict[str, str],
    typo_map: Dict[str, str]
) -> Tuple[str, bool]:
    """
    Normalize text using vocabulary synonyms and typos.
    
    Args:
        text: Input text (should already be lowercase and normalized)
        synonym_map: Dictionary mapping synonym variants to canonical forms
        typo_map: Dictionary mapping typo variants to canonical forms
    
    Returns:
        Tuple of (normalized_text, normalized_from_correction)
        - normalized_text: Text with synonyms and typos normalized to canonical
        - normalized_from_correction: True if any typos were corrected, False otherwise
    """
    if not synonym_map and not typo_map:
        return text, False
    
    words = text.split()
    normalized = words[:]
    normalized_from_correction = False
    
    for i, word in enumerate(words):
        word_lower = word.lower()
        canonical = None
        is_typo = False
        
        # Check typos first (typos take precedence if there's overlap)
        if word_lower in typo_map:
            canonical = typo_map[word_lower]
            is_typo = True
            normalized_from_correction = True
        elif word_lower in synonym_map:
            canonical = synonym_map[word_lower]
            is_typo = False
        
        if canonical:
            # Preserve original case pattern if possible
            if word and word[0].isupper():
                canonical = canonical.capitalize()
            normalized[i] = canonical
    
    result = " ".join(normalized)
    if normalized_from_correction:
        debug_print("vocabulary normalization (with typos):", result)
    else:
        debug_print("vocabulary normalization:", result)
    
    return result, normalized_from_correction


def validate_vocabularies(
    vocabularies: Dict[str, Dict[str, Dict[str, Any]]],
    entity_types: Dict[str, Any],
    business_categories: Dict[str, Any]
) -> None:
    """
    Validate vocabulary structure and ensure canonicals exist in entity_types or business_categories.
    
    Rules:
    1. Canonicals must exist in entity_types or business_categories
    2. No value may appear in both synonyms and typos
    3. typos must never introduce new semantic values
    
    Raises ValueError if validation fails.
    """
    errors = []
    
    # Build sets of valid canonicals from entity_types
    valid_weekdays = set()
    weekday_to_number = entity_types.get("date", {}).get("weekday", {}).get("to_number", {})
    valid_weekdays.update(weekday_to_number.keys())
    
    valid_months = set()
    month_to_number = entity_types.get("date", {}).get("month", {}).get("to_number", {})
    valid_months.update(month_to_number.keys())
    
    valid_date_relatives = set()
    date_relatives = entity_types.get("date", {}).get("relative", [])
    for rel_date in date_relatives:
        value = rel_date.get("value", "")
        if value:
            valid_date_relatives.add(value.lower())
    
    valid_time_windows = set()
    time_keywords = entity_types.get("time", {}).get("keywords", [])
    for keyword in time_keywords:
        value = keyword.get("value", "")
        if value:
            valid_time_windows.add(value.lower())
    
    # Build sets of valid canonicals from business_categories
    valid_business_categories = set()
    for category_id, category_data in business_categories.items():
        if isinstance(category_data, dict):
            for service_id, service_data in category_data.items():
                if isinstance(service_data, dict):
                    valid_business_categories.add(service_id.lower())
    
    # Track all variants to detect duplicates
    all_variants: Dict[str, Tuple[str, str]] = {}  # variant -> (category, type: synonym|typo)
    
    # Validate each vocabulary category
    for category, vocab_dict in vocabularies.items():
        if category.startswith("_"):
            continue
        
        if not isinstance(vocab_dict, dict):
            continue
        
        for canonical, variants_dict in vocab_dict.items():
            if not isinstance(variants_dict, dict):
                continue
            
            canonical_lower = canonical.lower()
            
            # Validate canonical exists in entity_types or business_categories
            if category == "weekdays":
                if canonical_lower not in valid_weekdays:
                    errors.append(
                        f"Canonical '{canonical}' in vocabularies.weekdays not found in "
                        f"entity_types.date.weekday.to_number"
                    )
            elif category == "months":
                if canonical_lower not in valid_months:
                    errors.append(
                        f"Canonical '{canonical}' in vocabularies.months not found in "
                        f"entity_types.date.month.to_number"
                    )
            elif category == "date_relative":
                if canonical_lower not in valid_date_relatives:
                    errors.append(
                        f"Canonical '{canonical}' in vocabularies.date_relative not found in "
                        f"entity_types.date.relative[].value"
                    )
            elif category == "time_window":
                if canonical_lower not in valid_time_windows:
                    errors.append(
                        f"Canonical '{canonical}' in vocabularies.time_window not found in "
                        f"entity_types.time.keywords[].value"
                    )
            # Service families validation would go here if needed
            
            # Check synonyms and typos for duplicates
            synonyms = variants_dict.get("synonyms", [])
            if isinstance(synonyms, list):
                for synonym in synonyms:
                    if isinstance(synonym, str):
                        synonym_lower = synonym.lower()
                        if synonym_lower in all_variants:
                            existing_category, existing_type = all_variants[synonym_lower]
                            errors.append(
                                f"Duplicate variant '{synonym}' appears in "
                                f"vocabularies.{existing_category} ({existing_type}) and "
                                f"vocabularies.{category} (synonym)"
                            )
                        all_variants[synonym_lower] = (category, "synonym")
            
            typos = variants_dict.get("typos", [])
            if isinstance(typos, list):
                for typo in typos:
                    if isinstance(typo, str):
                        typo_lower = typo.lower()
                        if typo_lower in all_variants:
                            existing_category, existing_type = all_variants[typo_lower]
                            errors.append(
                                f"Duplicate variant '{typo}' appears in "
                                f"vocabularies.{existing_category} ({existing_type}) and "
                                f"vocabularies.{category} (typo)"
                            )
                        all_variants[typo_lower] = (category, "typo")
    
    if errors:
        error_msg = "Vocabulary validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(error_msg)

