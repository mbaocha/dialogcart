"""
Entity loading and pattern building for service-based extraction.

Handles:
- Loading normalization entities from JSON
- Building spaCy patterns for SERVICE only
- Creating lookup maps for service grounding
- Loading global noise set (not as entities)
"""
import os
import json
import re
import logging
from typing import List, Dict, Any, Set
from pathlib import Path

logger = logging.getLogger(__name__)

# Optional spaCy dependency
try:
    import spacy
    from spacy.tokenizer import Tokenizer
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False
    spacy = None
    Tokenizer = None


DEBUG_ENABLED = os.environ.get("DEBUG_NLP", "0") == "1"


def debug_print(*args):
    if DEBUG_ENABLED:
        print(*args)


# -------------------------------------------------------------------
# Loading
# -------------------------------------------------------------------

def load_normalization_entities(json_path: Path) -> List[Dict[str, Any]]:
    """
    DEPRECATED: Tenant files are currently UNUSED.

    This function is kept for backward compatibility but should not be called
    at runtime. Service families are now loaded from global JSON only.

    Returns empty list to indicate tenant files are not used.
    """
    # Tenant files are currently EMPTY and MUST NOT be used at runtime
    # Service families are loaded from global JSON via load_global_service_families()
    debug_print(
        "[WARNING] load_normalization_entities() called - tenant files are unused")
    _ = json_path  # Unused but kept for signature compatibility
    return []


def load_global_noise_set(global_json_path: Path) -> Set[str]:
    """
    Load global noise tokens from global normalization JSON.

    Noise is NOT an entity - it's a lightweight token filter.
    Returns a set of lowercase noise tokens.

    Updated to read from normalization.noise.values in new structure.
    """
    with open(global_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # New structure: normalization.noise.values
    noise_values = data.get("normalization", {}).get(
        "noise", {}).get("values", [])
    return {token.lower() for token in noise_values}


def compile_orthography_map(raw_orthography: Dict[str, List[str]]) -> Dict[str, str]:
    """
    Compile canonical-first orthography format into variant → canonical map.

    Converts:
        {
            "haircut": ["hair cut", "hair-cut"],
            "checkin": ["check in", "check-in"]
        }

    Into:
        {
            "hair cut": "haircut",
            "hair-cut": "haircut",
            "haircut": "haircut",  # no-op mapping
            "check in": "checkin",
            "check-in": "checkin",
            "checkin": "checkin"  # no-op mapping
        }

    Args:
        raw_orthography: Dictionary mapping canonical forms to lists of variants

    Returns:
        Dictionary mapping variants (and canonicals) to canonical forms

    Raises:
        ValueError: If canonical form contains underscores (canonical IDs)
    """
    compiled_map = {}

    for canonical, variants in raw_orthography.items():
        # Skip description/metadata keys
        if canonical == "description":
            continue

        # Validate canonical form is natural language (no underscores)
        canonical_lower = canonical.lower()
        if "_" in canonical_lower:
            debug_print(
                f"[WARNING] Skipping orthography entry with canonical ID: {canonical}")
            continue

        # Ensure variants is a list
        if not isinstance(variants, list):
            debug_print(
                f"[WARNING] Skipping orthography entry with non-list variants: {canonical}")
            continue

        # Map canonical to itself (no-op, but useful for consistency)
        compiled_map[canonical_lower] = canonical_lower

        # Map all variants to canonical
        for variant in variants:
            variant_lower = variant.lower()
            # Validate variant doesn't contain canonical IDs
            if "_" in variant_lower:
                debug_print(
                    f"[WARNING] Skipping variant with canonical ID: {variant} → {canonical}")
                continue
            compiled_map[variant_lower] = canonical_lower

    return compiled_map


def compile_typo_map(raw_typos: Dict[str, Dict[str, List[str]]]) -> Dict[str, str]:
    """
    Compile canonical-first typo format into variant → canonical map.

    Converts:
        {
            "date_relative": {
                "tomorrow": ["tomorow", "tommorow", "tmrw"],
                "today": ["todat", "tody"]
            },
            "time_window": {
                "morning": ["mornign"]
            },
            "weekday": {
                "monday": ["moneday"]
            }
        }

    Into:
        {
            "tomorow": "tomorrow",
            "tommorow": "tomorrow",
            "tmrw": "tomorrow",
            "todat": "today",
            "tody": "today",
            "mornign": "morning"
        }

    Args:
        raw_typos: Dictionary of category → {canonical: [variants]} mappings

    Returns:
        Dictionary mapping variants to canonical forms
    """
    compiled_map = {}

    for category, canonicals in raw_typos.items():
        # Skip metadata keys
        if category.startswith("_"):
            continue

        # Validate category is a dictionary
        if not isinstance(canonicals, dict):
            debug_print(
                f"[WARNING] Skipping invalid typo category: {category}")
            continue

        for canonical, variants in canonicals.items():
            # Validate canonical form is natural language (no underscores)
            canonical_lower = canonical.lower()
            if "_" in canonical_lower:
                debug_print(
                    f"[WARNING] Skipping typo entry with canonical ID: {canonical}")
                continue

            # Ensure variants is a list
            if not isinstance(variants, list):
                debug_print(
                    f"[WARNING] Skipping typo entry with non-list variants: {canonical}")
                continue

            # Map all variants to canonical
            for variant in variants:
                variant_lower = variant.lower()
                # Validate variant doesn't contain canonical IDs
                if "_" in variant_lower:
                    debug_print(
                        f"[WARNING] Skipping variant with canonical ID: {variant} → {canonical}")
                    continue
                compiled_map[variant_lower] = canonical_lower

    return compiled_map


def _validate_typo_config(
    typos: Dict[str, Any],
    entity_types: Dict[str, Any],
    vocabularies: Dict[str, Any]
) -> None:
    """
    Validate typo configuration against entity_types and vocabularies.

    Rules (v2 schema):
    1. Every typo canonical must exist in entity_types OR vocabularies
    2. No duplicate canonicals across typo categories
    3. weekday/month canonicals must exist in vocabularies (as keys)
    4. Every weekday/month in vocabularies must exist in entity_types.date.{weekday|month}.to_number
    5. time_window canonicals must exist in entity_types.time.keywords[].value
    6. date_relative canonicals must exist in entity_types.date.relative[].value
    7. No weekday/month variant appears under more than one canonical
    8. CRITICAL: Typo variants must NOT appear in vocabularies (typos = invalid, vocabularies = accepted)

    Raises ValueError with clear error message if validation fails.
    """
    errors = []

    # Build sets of valid canonicals
    # New structure: vocabularies.weekdays is canonical-first (canonical -> [variants])
    weekdays_dict = vocabularies.get("weekdays", {})
    valid_weekdays = set(weekdays_dict.keys()) if isinstance(
        weekdays_dict, dict) else set()

    # Build set of all weekday variants to check for duplicates
    all_weekday_variants: Dict[str, str] = {}  # variant -> canonical
    for canonical, variants in weekdays_dict.items():
        if isinstance(variants, list):
            for variant in variants:
                variant_lower = variant.lower()
                if variant_lower in all_weekday_variants:
                    errors.append(
                        f"Duplicate weekday variant '{variant}' appears under both "
                        f"'{all_weekday_variants[variant_lower]}' and '{canonical}'"
                    )
                else:
                    all_weekday_variants[variant_lower] = canonical
        # Canonical itself is also a valid variant
        canonical_lower = canonical.lower()
        if canonical_lower not in all_weekday_variants:
            all_weekday_variants[canonical_lower] = canonical

    # Validate that all weekdays in vocabularies exist in entity_types.date.weekday.to_number
    weekday_to_number = entity_types.get("date", {}).get(
        "weekday", {}).get("to_number", {})
    for weekday in valid_weekdays:
        if weekday.lower() not in weekday_to_number:
            errors.append(
                f"Weekday '{weekday}' in vocabularies.weekdays not found in "
                f"entity_types.date.weekday.to_number"
            )

    # Build sets of valid month canonicals
    months_dict = vocabularies.get("months", {})
    valid_months = set(months_dict.keys()) if isinstance(
        months_dict, dict) else set()

    # Build set of all month variants to check for duplicates
    all_month_variants: Dict[str, str] = {}  # variant -> canonical
    for canonical, variants in months_dict.items():
        if isinstance(variants, list):
            for variant in variants:
                variant_lower = variant.lower()
                if variant_lower in all_month_variants:
                    errors.append(
                        f"Duplicate month variant '{variant}' appears under both "
                        f"'{all_month_variants[variant_lower]}' and '{canonical}'"
                    )
                else:
                    all_month_variants[variant_lower] = canonical
        # Canonical itself is also a valid variant
        canonical_lower = canonical.lower()
        if canonical_lower not in all_month_variants:
            all_month_variants[canonical_lower] = canonical

    # Validate that all months in vocabularies exist in entity_types.date.month.to_number
    month_to_number = entity_types.get("date", {}).get(
        "month", {}).get("to_number", {})
    for month in valid_months:
        if month.lower() not in month_to_number:
            errors.append(
                f"Month '{month}' in vocabularies.months not found in "
                f"entity_types.date.month.to_number"
            )

    valid_time_windows = set()
    time_keywords = entity_types.get("time", {}).get("keywords", [])
    for keyword in time_keywords:
        value = keyword.get("value", "")
        if value:
            valid_time_windows.add(value.lower())

    valid_date_relatives = set()
    date_relatives = entity_types.get("date", {}).get("relative", [])
    for rel_date in date_relatives:
        value = rel_date.get("value", "")
        if value:
            valid_date_relatives.add(value.lower())

    # Build set of all vocabulary variants (weekdays, months) to check typos don't overlap
    # V2 Rule: Typos must NOT appear in vocabularies (typos = invalid, vocabularies = accepted)
    all_vocab_variants = set()
    # Add all weekday variants
    for canonical, variants in weekdays_dict.items():
        if isinstance(variants, list):
            for variant in variants:
                all_vocab_variants.add(variant.lower())
        all_vocab_variants.add(canonical.lower())
    # Add all month variants
    for canonical, variants in months_dict.items():
        if isinstance(variants, list):
            for variant in variants:
                all_vocab_variants.add(variant.lower())
        all_vocab_variants.add(canonical.lower())

    # Track all canonicals to detect duplicates
    all_canonicals: Dict[str, str] = {}  # canonical -> category

    # Validate each typo category
    for category, canonicals in typos.items():
        if category.startswith("_"):
            continue

        if not isinstance(canonicals, dict):
            continue

        for canonical, variants in canonicals.items():
            canonical_lower = canonical.lower()

            # Check for duplicates
            if canonical_lower in all_canonicals:
                errors.append(
                    f"Duplicate typo canonical '{canonical}' in both '{all_canonicals[canonical_lower]}' "
                    f"and '{category}'"
                )
            else:
                all_canonicals[canonical_lower] = category

            # Validate based on category
            if category == "weekday":
                if canonical_lower not in valid_weekdays:
                    errors.append(
                        f"Typo canonical '{canonical}' in category 'weekday' not found in "
                        f"vocabularies.weekdays (as a key)"
                    )
                # V2 Rule: Typo variants must NOT be in vocabularies (typos = invalid only)
                for variant in variants:
                    variant_lower = variant.lower()
                    if variant_lower in all_vocab_variants:
                        errors.append(
                            f"Typo variant '{variant}' in category 'weekday' appears in vocabularies. "
                            f"Typos must be invalid spellings only, not accepted variants."
                        )
            elif category == "time_window":
                if canonical_lower not in valid_time_windows:
                    errors.append(
                        f"Typo canonical '{canonical}' in category 'time_window' not found in "
                        f"entity_types.time.keywords[].value"
                    )
            elif category == "date_relative":
                if canonical_lower not in valid_date_relatives:
                    errors.append(
                        f"Typo canonical '{canonical}' in category 'date_relative' not found in "
                        f"entity_types.date.relative[].value"
                    )
            elif category == "month":
                if canonical_lower not in valid_months:
                    errors.append(
                        f"Typo canonical '{canonical}' in category 'month' not found in "
                        f"vocabularies.months (as a key)"
                    )
                # V2 Rule: Typo variants must NOT be in vocabularies (typos = invalid only)
                for variant in variants:
                    variant_lower = variant.lower()
                    if variant_lower in all_vocab_variants:
                        errors.append(
                            f"Typo variant '{variant}' in category 'month' appears in vocabularies. "
                            f"Typos must be invalid spellings only, not accepted variants."
                        )

    if errors:
        error_msg = "Typo configuration validation failed:\n" + \
            "\n".join(f"  - {e}" for e in errors)
        raise ValueError(error_msg)


def load_global_typo_config(global_json_path: Path) -> Dict[str, str]:
    """
    Load and validate global typo correction configuration from global JSON.

    The JSON uses canonical-first format:
        {
            "normalization": {
                "typos": {
                    "date_relative": {
                        "tomorrow": ["tomorow", "tommorow", "tmrw"],
                        "today": ["todat", "tody"]
                    },
                    "time_window": {
                        "morning": ["mornign"]
                    },
                    "weekday": {
                        "monday": ["moneday"]
                    }
                },
                "vocabularies": {
                    "weekdays": {
                        "monday": ["mon", "mondays"]
                    }
                }
            }
        }

    This function:
    1. Validates that all typo canonicals exist in entity_types or vocabularies
    2. Compiles typos into variant → canonical format
    3. Also includes vocabulary variants from vocabularies.weekdays and vocabularies.months
       (accepted variants like abbreviations and plurals) so they normalize to canonical form
       (e.g., "mon" → "monday", "mondays" → "monday", "jan" → "january")

    Returns a dictionary mapping typo variants to canonical forms.

    Raises ValueError if validation fails.

    Example output:
        {
            "tomorow": "tomorrow",
            "tommorow": "tomorrow",
            "tmrw": "tomorrow",
            "todat": "today",
            "mornign": "morning",
            "moneday": "monday",
            "mon": "monday",      # from vocabularies.weekdays (accepted variant)
            "mondays": "monday",  # from vocabularies.weekdays (accepted variant)
            "jan": "january"      # from vocabularies.months (accepted variant)
        }
    """
    with open(global_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Extract sections
    typos = data.get("normalization", {}).get("typos", {})
    entity_types = data.get("entity_types", {})
    vocabularies = data.get("normalization", {}).get("vocabularies", {})

    # Validate before compiling
    _validate_typo_config(typos, entity_types, vocabularies)

    # Compile canonical-first format to variant → canonical map
    compiled_map = compile_typo_map(typos)

    # V2: Also add vocabulary variants (weekdays, months) to the normalization map
    # This ensures accepted variants (abbreviations, plurals) normalize to canonical
    # Note: These are NOT typos - they're accepted language variants
    weekdays_dict = vocabularies.get("weekdays", {})
    if isinstance(weekdays_dict, dict):
        for canonical, variants in weekdays_dict.items():
            canonical_lower = canonical.lower()
            if isinstance(variants, list):
                for variant in variants:
                    variant_lower = variant.lower()
                    # Don't override existing entries (typos take precedence)
                    if variant_lower not in compiled_map:
                        compiled_map[variant_lower] = canonical_lower

    # Add month vocabulary variants (abbreviations like "jan" → "january")
    months_dict = vocabularies.get("months", {})
    if isinstance(months_dict, dict):
        for canonical, variants in months_dict.items():
            canonical_lower = canonical.lower()
            if isinstance(variants, list):
                for variant in variants:
                    variant_lower = variant.lower()
                    # Don't override existing entries (typos take precedence)
                    if variant_lower not in compiled_map:
                        compiled_map[variant_lower] = canonical_lower

    return compiled_map


def load_global_orthography_rules(global_json_path: Path) -> Dict[str, str]:
    """
    Load global orthographic normalization rules from global JSON.

    The JSON uses canonical-first format:
        {
            "normalization": {
                "orthography": {
                    "haircut": ["hair cut", "hair-cut"],
                    "checkin": ["check in", "check-in"]
                }
            }
        }

    This function compiles it into variant → canonical format for runtime use.

    Returns a dictionary mapping variants to preferred natural language forms.
    Rules are case-insensitive and preserve natural language only.

    Example output:
        {
            "hair cut": "haircut",
            "hair-cut": "haircut",
            "haircut": "haircut",
            "6pm": "6 pm",
            "pick up": "pickup"
        }

    Updated to read from normalization.orthography in new structure.
    """
    with open(global_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # New structure: normalization.orthography
    orthography = data.get("normalization", {}).get("orthography", {})

    # Compile canonical-first format to variant → canonical map
    compiled_map = compile_orthography_map(orthography)

    return compiled_map


# -------------------------------------------------------------------
# Service Families (GLOBAL)
# -------------------------------------------------------------------

def load_global_service_families(global_json_path: Path) -> Dict[str, Dict[str, Any]]:
    """
    Load global service families from global JSON.

    Service families are GLOBAL semantic concepts, not tenant-specific.
    They are stable, bounded, and long-lived.

    Structure:
        {
            "service_families": {
                "beauty_and_wellness": {
                    "haircut": {
                        "token": "servicefamilytoken",
                        "display_name": "Haircut",
                        "description": "...",
                        "synonym": ["haircut", "hair trim", ...]
                    },
                    ...
                }
            }
        }

    Returns a nested dictionary:
        {
            "beauty_and_wellness": {
                "haircut": {...},
                "beard_grooming": {...}
            }
        }
    """
    with open(global_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    service_families = data.get("service_families", {})

    # Remove metadata keys
    if "_comment" in service_families:
        service_families = {
            k: v for k, v in service_families.items() if not k.startswith("_")}

    return service_families


def build_service_family_synonym_map(service_families: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """
    Build a map from service family synonyms to their canonical service family IDs.

    Example:
        Input: {
            "beauty_and_wellness": {
                "haircut": {"synonym": ["haircut", "hair trim"]},
                "beard_grooming": {"synonym": ["beard trim"]}
            }
        }

        Output: {
            "haircut": "beauty_and_wellness.haircut",
            "hair trim": "beauty_and_wellness.haircut",
            "beard trim": "beauty_and_wellness.beard_grooming"
        }

    This map is used for entity extraction and fuzzy matching.
    """
    synonym_map = {}

    for category, families in service_families.items():
        if not isinstance(families, dict):
            continue

        for family_id, family_data in families.items():
            if not isinstance(family_data, dict):
                continue

            # Build canonical ID: category.family_id
            canonical_id = f"{category}.{family_id}"

            # Get synonyms
            synonyms = family_data.get("synonym", [])
            if not isinstance(synonyms, list):
                continue

            # Map each synonym to canonical ID
            for synonym in synonyms:
                if isinstance(synonym, str):
                    synonym_map[synonym.lower()] = canonical_id

    return synonym_map


def build_service_family_patterns(service_families: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Build spaCy EntityRuler patterns for service families.

    Creates patterns from all synonyms in service_families.
    All patterns are labeled as "SERVICE_FAMILY".

    Returns list of patterns for spaCy EntityRuler.
    """
    patterns = []

    for category, families in service_families.items():
        if not isinstance(families, dict):
            continue

        for family_id, family_data in families.items():
            if not isinstance(family_data, dict):
                continue

            synonyms = family_data.get("synonym", [])
            if not isinstance(synonyms, list):
                continue

            # Create pattern for each synonym
            for synonym in synonyms:
                if isinstance(synonym, str):
                    patterns.append({
                        "label": "SERVICE_FAMILY",
                        "pattern": synonym.lower()
                    })

    return patterns


# -------------------------------------------------------------------
# Entity Types (GLOBAL)
# -------------------------------------------------------------------

def load_global_entity_types(global_json_path: Path) -> Dict[str, Any]:
    """
    Load global entity types (date, time, duration) from global JSON.

    Entity types are GLOBAL semantic concepts used for extraction.

    Returns the entity_types section from global JSON.
    """
    with open(global_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("entity_types", {})


def build_date_patterns(entity_types: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Build spaCy EntityRuler patterns for DATE entities.

    Includes:
    - Relative dates from entity_types.date.relative (e.g., today, tomorrow, next week)
    - Weekday canonicals from entity_types.date.weekday.to_number (e.g., monday, friday)
    """
    patterns = []
    date_config = entity_types.get("date", {})
    relative_dates = date_config.get("relative", [])
    weekday_map = date_config.get("weekday", {}).get("to_number", {})

    for rel_date in relative_dates:
        if isinstance(rel_date, dict):
            value = rel_date.get("value", "")
            if value:
                patterns.append({
                    "label": "DATE",
                    "pattern": value.lower()
                })

    # Add weekday canonicals as DATE patterns (normalization maps variants to canonicals)
    for weekday in weekday_map.keys():
        patterns.append({
            "label": "DATE",
            "pattern": weekday.lower()
        })

    return patterns


def build_absolute_date_patterns(entity_types: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Build spaCy EntityRuler patterns for DATE_ABSOLUTE entities.

    Extracts absolute calendar date patterns from entity_types.date.absolute.patterns.
    Supports:
    - Day + Month: "15th dec", "15 dec", "15 december"
    - Day + Month + Year: "15 dec 2025", "15th december 2025"
    - Month + Day: "dec 15", "dec 15th", "december 15"
    - Numeric: "15/12", "15/12/2025", "15-12-2025"

    Uses token-based REGEX patterns for actual regex matching.
    """
    patterns = []
    date_config = entity_types.get("date", {})
    absolute_config = date_config.get("absolute", {})
    absolute_patterns = absolute_config.get("patterns", [])

    for pattern_def in absolute_patterns:
        if isinstance(pattern_def, dict):
            pattern_id = pattern_def.get("id", "")
            pattern_value = pattern_def.get("value", "")

            if not pattern_value:
                continue

            if pattern_id == "day_month_text":
                # Pattern: "15th dec", "15 dec", "15 december", "15th december 2025"
                # After tokenization with digit-letter split: "15th dec" → ["15", "th", "dec"]
                # Token pattern: number + optional ordinal suffix + month name + optional year
                patterns.append({
                    "label": "DATE_ABSOLUTE",
                    "pattern": [
                        {"TEXT": {"REGEX": "^\\d{1,2}$"}},
                        {"OP": "?", "LOWER": {"IN": ["st", "nd", "rd", "th"]}},
                        {"LOWER": {
                            "REGEX": "^(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|september|oct|october|nov|november|dec|december)$"}},
                        {"OP": "?", "TEXT": {"REGEX": "^\\d{4}$"}}
                    ]
                })
                # Also handle case where ordinal stays joined (if tokenizer doesn't split)
                patterns.append({
                    "label": "DATE_ABSOLUTE",
                    "pattern": [
                        {"TEXT": {"REGEX": "^\\d{1,2}(?:st|nd|rd|th)$"}},
                        {"LOWER": {
                            "REGEX": "^(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|september|oct|october|nov|november|dec|december)$"}},
                        {"OP": "?", "TEXT": {"REGEX": "^\\d{4}$"}}
                    ]
                })
            elif pattern_id == "month_day_text":
                # Pattern: "dec 15", "dec 15th", "december 15", "dec 15th 2025"
                # After tokenization with digit-letter split: "dec 15th" → ["dec", "15", "th"]
                # Token pattern: month name + number + optional ordinal suffix + optional year
                patterns.append({
                    "label": "DATE_ABSOLUTE",
                    "pattern": [
                        {"LOWER": {
                            "REGEX": "^(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|september|oct|october|nov|november|dec|december)$"}},
                        {"TEXT": {"REGEX": "^\\d{1,2}$"}},
                        {"OP": "?", "LOWER": {"IN": ["st", "nd", "rd", "th"]}},
                        {"OP": "?", "TEXT": {"REGEX": "^\\d{4}$"}}
                    ]
                })
                # Also handle case where ordinal stays joined (if tokenizer doesn't split)
                patterns.append({
                    "label": "DATE_ABSOLUTE",
                    "pattern": [
                        {"LOWER": {
                            "REGEX": "^(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|september|oct|october|nov|november|dec|december)$"}},
                        {"TEXT": {"REGEX": "^\\d{1,2}(?:st|nd|rd|th)$"}},
                        {"OP": "?", "TEXT": {"REGEX": "^\\d{4}$"}}
                    ]
                })
            elif pattern_id == "numeric_date":
                # Pattern: "15/12", "15/12/2025", "15-12-2025"
                # After tokenization: "15/12" → ["15/12"] (single token with separator)
                # Token pattern: single token with numeric date format
                patterns.append({
                    "label": "DATE_ABSOLUTE",
                    "pattern": [{"TEXT": {"REGEX": "^\\d{1,2}[/-]\\d{1,2}(?:[/-]\\d{2,4})?$"}}]
                })

    return patterns


def build_time_patterns(entity_types: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Build spaCy EntityRuler patterns for TIME entities (precise clock times only).

    Extracts regex patterns from entity_types.time.patterns.
    Time patterns must NOT swallow duration (strict word boundaries).

    Uses token-based REGEX patterns for actual regex matching (not exact string match).

    Note: Time window keywords (morning, afternoon, etc.) are handled separately
    in build_time_window_patterns().
    """
    patterns = []
    time_config = entity_types.get("time", {})

    # Diagnostic: Log entry
    logger.info("[time-extract]: building time patterns")

    # Add regex patterns - convert to token-based REGEX patterns
    # These represent precise clock times (9 am, 12:30 pm, etc.)
    time_patterns = time_config.get("patterns", [])
    for pattern_def in time_patterns:
        if isinstance(pattern_def, dict):
            pattern_id = pattern_def.get("id", "")
            if pattern_id == "time_12h":
                # Pattern matches: "9 am", "12pm", "12.30pm", etc.
                # The following patterns force greedy matching of AM/PM and separator.
                # Enforces that the whole time, with meridiem, is captured as a single TIME entity.
                # - "5pm", "5 pm", "5:30pm", "5:30 pm", "5.30pm", "5.30 pm"
                # Pattern 1: Split (space-separated, due to pre-normalizer):
                pattern_1 = {
                    "label": "TIME",
                    "pattern": [
                        {"TEXT": {"REGEX": "^(1[0-2]|0?[1-9])$"}},
                        {"OP": "?", "TEXT": {"REGEX": "^[:.][0-5][0-9]$"}},
                        {"OP": "?", "IS_SPACE": True},
                        {"LOWER": {"REGEX": "^(am|pm)$"}}
                    ]
                }
                logger.info(
                    "[time-extract]: built pattern=\"time_12h_pattern_1\" (hour + optional minutes + optional space + am/pm)")
                patterns.append(pattern_1)
                # Pattern 2: Single-token, greedy match for everything (handles "5.30pm", "5:30pm"):
                pattern_2 = {
                    "label": "TIME",
                    "pattern": [{"TEXT": {"REGEX": "^(1[0-2]|0?[1-9])([:.][0-5][0-9])?(am|pm)$"}}]
                }
                logger.info(
                    "[time-extract]: built pattern=\"time_12h_pattern_2\" (single token: hour + optional minutes + am/pm)")
                patterns.append(pattern_2)
                # Pattern 2b: Multi-token with dot separator and pm attached (handles "5.30pm" tokenized as ["5", ".", "30pm"]):
                patterns.append({
                    "label": "TIME",
                    "pattern": [
                        {"TEXT": {"REGEX": "^(1[0-2]|0?[1-9])$"}},
                        {"TEXT": {"REGEX": "^\\.$"}},
                        {"TEXT": {"REGEX": "^[0-5][0-9](am|pm)$"}}
                    ]
                })
                # Pattern 3: Case with optional space between minutes and meridiem (greedy):
                patterns.append({
                    "label": "TIME",
                    "pattern": [
                        {"TEXT": {"REGEX": "^(1[0-2]|0?[1-9])$"}},
                        {"OP": "?", "TEXT": {"REGEX": "^[:.][0-5][0-9]$"}},
                        {"OP": "?", "IS_SPACE": True},
                        {"TEXT": {"REGEX": "^(am|pm)$"}}
                    ]
                })
                # Pattern 4: Multi-token with dot separator and space before pm (handles "5.30 pm"):
                patterns.append({
                    "label": "TIME",
                    "pattern": [
                        {"TEXT": {"REGEX": "^(1[0-2]|0?[1-9])$"}},
                        {"TEXT": {"REGEX": "^\\.$"}},
                        {"TEXT": {"REGEX": "^[0-5][0-9]$"}},
                        {"OP": "?", "IS_SPACE": True},
                        {"TEXT": {"REGEX": "^(am|pm)$"}}
                    ]
                })
                # Pattern 5: Multi-token with colon and spaces around it (handles "9 : 30 am", "12 : 00 pm"):
                patterns.append({
                    "label": "TIME",
                    "pattern": [
                        {"TEXT": {"REGEX": "^(1[0-2]|0?[1-9])$"}},
                        {"OP": "?", "IS_SPACE": True},
                        {"TEXT": {"REGEX": "^:$"}},
                        {"OP": "?", "IS_SPACE": True},
                        {"TEXT": {"REGEX": "^[0-5][0-9]$"}},
                        {"OP": "?", "IS_SPACE": True},
                        {"LOWER": {"REGEX": "^(am|pm)$"}}
                    ]
                })
            elif pattern_id == "time_24h":
                # Pattern: HH:MM format (single token) - handles "14:00", "18:30"
                patterns.append({
                    "label": "TIME",
                    "pattern": [{"TEXT": {"REGEX": "^([01]?[0-9]|2[0-3]):[0-5][0-9]$"}}]
                })
                # Pattern: HH : MM format (multi-token with spaces around colon) - handles "14 : 00", "18 : 30"
                # After tokenization, "14 : 00" becomes ["14", ":", "00"] or ["14", " ", ":", " ", "00"]
                patterns.append({
                    "label": "TIME",
                    "pattern": [
                        {"TEXT": {"REGEX": "^([01]?[0-9]|2[0-3])$"}},
                        {"OP": "?", "IS_SPACE": True},
                        {"TEXT": {"REGEX": "^:$"}},
                        {"OP": "?", "IS_SPACE": True},
                        {"TEXT": {"REGEX": "^[0-5][0-9]$"}}
                    ]
                })
                # Pattern: HH.MM format (dot separator, e.g., "10.30")
                # This handles cases like "at 10.30" where dot is used instead of colon
                patterns.append({
                    "label": "TIME",
                    "pattern": [{"TEXT": {"REGEX": "^([01]?[0-9]|2[0-3])\\.[0-5][0-9]$"}}]
                })
                # Pattern: HH.MM format split into tokens (e.g., "10" "." "30")
                # After tokenization, "10.30" might become ["10", ".", "30"]
                patterns.append({
                    "label": "TIME",
                    "pattern": [
                        {"TEXT": {"REGEX": "^([01]?[0-9]|2[0-3])$"}},
                        {"TEXT": {"REGEX": "^\\.$"}},
                        {"TEXT": {"REGEX": "^[0-5][0-9]$"}}
                    ]
                })
            elif pattern_id == "time_bare_hour":
                # Pattern for bare hours (e.g., "at 2", "at 9", "at 10") - hour only without am/pm
                # Supports 24-hour format (0-23)
                # These will be flagged as ambiguous in semantic resolution
                # Pattern 1: "at 9", "at 10", "around 7", etc.
                pattern_bare_1 = {
                    "label": "TIME",
                    "pattern": [
                        {"LOWER": {"IN": ["at", "around", "about"]}},
                        # 0-23 hour range
                        {"TEXT": {"REGEX": "^([01]?[0-9]|2[0-3])$"}},
                        {"OP": "?", "TEXT": {"REGEX": "^(ish|'ish)$"}}
                    ]
                }
                logger.info(
                    "[time-extract]: built pattern=\"time_bare_hour_pattern_1\" (at/around/about + hour 0-23 + optional ish)")
                patterns.append(pattern_bare_1)
                # Pattern 2: Standalone hour after "at" (in case "at" is not captured)
                # Matches hour 0-23 without am/pm
                pattern_bare_2 = {
                    "label": "TIME",
                    "pattern": [
                        # 0-23 hour range
                        {"TEXT": {"REGEX": "^([01]?[0-9]|2[0-3])$"}},
                        {"OP": "?", "TEXT": {"REGEX": "^(ish|'ish)$"}},
                        {"OP": "?", "TEXT": {
                            # Negative lookahead for am/pm
                            "REGEX": "^(?!am|pm|a\\.m\\.|p\\.m\\.)"}}
                    ]
                }
                logger.info(
                    "[time-extract]: built pattern=\"time_bare_hour_pattern_2\" (standalone hour 0-23 + optional ish + negative lookahead for am/pm)")
                patterns.append(pattern_bare_2)

    # Diagnostic: Log total patterns built
    logger.info("[time-extract]: built %d time patterns total", len(patterns))

    return patterns


def build_time_window_patterns(entity_types: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Build spaCy EntityRuler patterns for TIME_WINDOW entities (coarse time ranges).

    Extracts keywords from entity_types.time.keywords where granularity="window".
    These represent coarse time windows: morning, afternoon, evening, night.

    Returns patterns labeled as TIME_WINDOW (distinct from precise TIME entities).
    """
    patterns = []
    time_config = entity_types.get("time", {})

    # Add keyword patterns (morning, afternoon, etc.) as TIME_WINDOW entities
    time_keywords = time_config.get("keywords", [])
    for keyword_def in time_keywords:
        if isinstance(keyword_def, dict):
            value = keyword_def.get("value", "")
            granularity = keyword_def.get("granularity", "")
            # Only extract keywords marked as "window" granularity
            if value and granularity == "window":
                patterns.append({
                    "label": "TIME_WINDOW",
                    "pattern": value.lower()
                })

    return patterns


def build_duration_patterns(entity_types: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Build spaCy EntityRuler patterns for DURATION entities.

    Extracts regex patterns and keywords from entity_types.duration.
    Converts regex patterns to token-based patterns for multi-token matching.
    """
    patterns = []
    duration_config = entity_types.get("duration", {})

    # Add regex patterns - convert to token-based REGEX patterns
    duration_patterns = duration_config.get("patterns", [])
    for pattern_def in duration_patterns:
        if isinstance(pattern_def, dict):
            pattern_value = pattern_def.get("value", "")
            unit = pattern_def.get("unit", "")
            if pattern_value and unit:
                # Pattern: \b(\d+)\s?(minutes|minute|mins|min)\b or \b(\d+)\s?(hours|hour|hrs|hr)\b
                # After tokenization: "30 mins" → ["30", "mins"]
                # Convert to token pattern with REGEX for actual regex matching
                if unit == "minutes":
                    patterns.append({
                        "label": "DURATION",
                        "pattern": [
                            {"TEXT": {"REGEX": "^\\d+$"}},
                            {"LOWER": {"REGEX": "^(minutes|minute|mins|min)$"}}
                        ]
                    })
                elif unit == "hours":
                    patterns.append({
                        "label": "DURATION",
                        "pattern": [
                            {"TEXT": {"REGEX": "^\\d+$"}},
                            {"LOWER": {"REGEX": "^(hours|hour|hrs|hr)$"}}
                        ]
                    })

    # Add keyword patterns (half hour, one hour, etc.) as simple string patterns
    duration_keywords = duration_config.get("keywords", [])
    for keyword_def in duration_keywords:
        if isinstance(keyword_def, dict):
            value = keyword_def.get("value", "")
            if value:
                patterns.append({
                    "label": "DURATION",
                    "pattern": value.lower()
                })

    return patterns


# -------------------------------------------------------------------
# Patterns
# -------------------------------------------------------------------

def build_entity_patterns(entities: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Build spaCy EntityRuler patterns for SERVICE only.

    Noise is NOT included - it's not an entity.
    """
    patterns = []

    for ent in entities:
        # Only process SERVICE entities
        if ent["type"] != ["service"]:
            continue

        label = "SERVICE"
        for phrase in sorted(ent["synonyms"], key=lambda x: -len(x.split())):
            patterns.append({
                "label": label,
                "pattern": phrase.lower()
            })

    return patterns


# -------------------------------------------------------------------
# Support Maps
# -------------------------------------------------------------------

def build_support_maps(
    entities: List[Dict[str, Any]]
) -> Dict[str, str]:
    """
    Build lookup map for service grounding.

    Returns only service_map. Noise is handled separately via load_global_noise_set().
    """
    service_map: Dict[str, str] = {}

    for ent in entities:
        canon = ent["canonical"]
        synonyms = [s.lower() for s in ent.get("synonyms", [])]
        types = ent.get("type", [])

        if types == ["service"]:
            for term in synonyms:
                service_map[term] = canon

    return service_map


# -------------------------------------------------------------------
# Tokenizer
# -------------------------------------------------------------------

def customize_tokenizer(nlp):
    """
    Custom tokenizer:
    - Preserves hyphenated phrases
    - Splits digits and letters (12pm → 12 pm)
    """
    if not SPACY_AVAILABLE:
        raise ImportError("spaCy required")

    infix_re = re.compile(r"(?<=\d)(?=[a-zA-Z])|(?<=[a-zA-Z])(?=\d)")
    prefix_re = re.compile(r'''^[\[\("']''')
    suffix_re = re.compile(r'''[\]\)"']$''')

    return Tokenizer(
        nlp.vocab,
        rules=nlp.Defaults.tokenizer_exceptions,
        prefix_search=prefix_re.search,
        suffix_search=suffix_re.search,
        infix_finditer=infix_re.finditer,
        token_match=None
    )


# -------------------------------------------------------------------
# Init
# -------------------------------------------------------------------

def init_nlp_with_service_families(global_json_path: Path):
    """
    Initialize spaCy with entity ruler for service families, dates, times, and durations.

    Extraction order (mandatory):
    1. Date extraction
    2. Time extraction (strict - must not swallow duration)
    3. Duration extraction
    4. Service family extraction

    Updated to use global service_families and entity_types instead of tenant entities.
    """
    if not SPACY_AVAILABLE:
        raise ImportError(
            "Install spaCy: pip install spacy && python -m spacy download en_core_web_sm"
        )

    nlp = spacy.load("en_core_web_sm")
    nlp.tokenizer = customize_tokenizer(nlp)

    # Load entity types and service families from global JSON
    entity_types = load_global_entity_types(global_json_path)
    service_families = load_global_service_families(global_json_path)

    # Build patterns in extraction order (date_absolute → date → time_window → time → duration → service_family)
    all_patterns = []

    # 1. Absolute date patterns (15th dec, 15/12/2025, etc.)
    # Must come FIRST to have higher priority and prevent conflicts with relative dates
    # This ensures "december 2025" matches as DATE_ABSOLUTE, not relative DATE
    all_patterns.extend(build_absolute_date_patterns(entity_types))

    # 2. Relative date patterns (today, tomorrow, etc.)
    # Lower priority - only matches if absolute patterns don't match
    all_patterns.extend(build_date_patterns(entity_types))

    # 3. Time window patterns (coarse ranges: morning, afternoon, etc.)
    # Must come before precise TIME patterns to avoid conflicts
    all_patterns.extend(build_time_window_patterns(entity_types))

    # 3. Time patterns (precise clock times: 9 am, 12:30 pm, etc.)
    # Strict - must not swallow duration
    all_patterns.extend(build_time_patterns(entity_types))

    # 4. Duration patterns
    all_patterns.extend(build_duration_patterns(entity_types))

    # 5. Service family patterns (last - lowest priority)
    all_patterns.extend(build_service_family_patterns(service_families))

    ruler = nlp.add_pipe("entity_ruler", before="ner",
                         config={"overwrite_ents": True})
    ruler.add_patterns(all_patterns)

    return nlp, service_families


def init_nlp_with_entities(json_path: Path):
    """
    DEPRECATED: Use init_nlp_with_service_families() instead.

    Kept for backward compatibility but tenant files are unused.
    """
    if not SPACY_AVAILABLE:
        raise ImportError(
            "Install spaCy: pip install spacy && python -m spacy download en_core_web_sm"
        )

    nlp = spacy.load("en_core_web_sm")
    nlp.tokenizer = customize_tokenizer(nlp)

    # Tenant entities are empty/unused
    _ = json_path  # Unused but kept for signature compatibility
    entities = []
    patterns = []

    ruler = nlp.add_pipe("entity_ruler", before="ner",
                         config={"overwrite_ents": True})
    ruler.add_patterns(patterns)

    return nlp, entities


def load_global_vocabularies(global_json_path: Path) -> Dict[str, Any]:
    """
    Load global vocabularies from global JSON.

    Returns vocabularies for weekdays, relative dates, vague patterns, etc.
    """
    with open(global_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    vocabularies = data.get("normalization", {}).get("vocabularies", {})
    return vocabularies


def load_relative_date_offsets(global_json_path: Path) -> Dict[str, int]:
    """
    Load relative date offsets from entity_types.date.relative.

    Returns dict mapping date strings to offset days.
    """
    with open(global_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    relative_dates = data.get("entity_types", {}).get(
        "date", {}).get("relative", [])
    offsets = {}
    for rel_date in relative_dates:
        value = rel_date.get("value", "")
        offset = rel_date.get("offset_days", 0)
        if value:
            offsets[value] = offset

    # Add weekend approximations (these are approximate, would need day-of-week logic)
    offsets["this weekend"] = 0
    offsets["next weekend"] = 7

    return offsets


def load_time_window_bounds(global_json_path: Path) -> Dict[str, Dict[str, str]]:
    """
    Load time window bounds from entity_types.time.keywords.

    Returns dict mapping window names to {start, end} bounds.
    """
    with open(global_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    time_keywords = data.get("entity_types", {}).get(
        "time", {}).get("keywords", [])
    bounds = {}
    for keyword in time_keywords:
        value = keyword.get("value", "")
        range_list = keyword.get("range", [])
        if value and len(range_list) >= 2:
            bounds[value] = {
                "start": range_list[0],
                "end": range_list[1]
            }

    return bounds


def load_booking_policy(global_json_path: Path) -> Dict[str, bool]:
    """
    Load booking policy configuration from global JSON.

    Returns dict with policy flags:
    - allow_time_windows: Whether time windows (e.g., "morning") are allowed
    - allow_constraint_only_time: Whether constraint-only times (e.g., "by 4pm") are acceptable as exact
    """
    with open(global_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    policy = data.get("booking_policy", {})
    return {
        "allow_time_windows": policy.get("allow_time_windows", True),
        "allow_constraint_only_time": policy.get("allow_constraint_only_time", True)
    }


def load_month_names(global_json_path: Path) -> Dict[str, int]:
    """
    Load month name to number mapping from entity_types.date.month.to_number.

    Returns dict mapping canonical month names to month numbers (1-12).
    Only canonical names (january, february, etc.) are returned.
    Variants (jan, feb, etc.) are normalized via vocabularies.months.
    """
    with open(global_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entity_types = data.get("entity_types", {})
    months = entity_types.get("date", {}).get("month", {}).get("to_number", {})
    return months
