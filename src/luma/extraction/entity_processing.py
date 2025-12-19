"""
Service-based entity extraction and parameterization.

Handles:
- SERVICE, DATE, TIME extraction
- Sentence parameterization
- Canonicalization of services

Note: Noise is NOT extracted as an entity.
"""

import logging
from typing import Dict, List, Any
from luma.config import debug_print
from .normalization import post_normalize_parameterized_text

logger = logging.getLogger(__name__)


def add_entity(
    result: Dict[str, List],
    entity_type: str,
    text: str,
    position: int,
    length: int = 1
):
    result.setdefault(entity_type, []).append({
        "text": text,
        "position": position,
        "length": length
    })


def extract_entities_from_doc(nlp, text: str) -> Dict[str, List]:
    """
    Extract SERVICE_FAMILY / DATE / DATE_ABSOLUTE / TIME / TIME_WINDOW / DURATION entities from spaCy doc.

    Updated to extract SERVICE_FAMILY (global semantic concept) instead of SERVICE.
    DATE represents relative dates (today, tomorrow, etc.).
    DATE_ABSOLUTE represents absolute calendar dates (15th dec, 15/12/2025, etc.).
    TIME_WINDOW represents coarse time ranges (morning, afternoon, etc.).
    TIME represents precise clock times (9 am, 12:30 pm, etc.).
    Noise is NOT extracted - it's not an entity.
    """
    # Diagnostic: Log entry of time extraction (using print to ensure visibility)
    print(f"[time-extract]: input_sentence=\"{text}\"")
    
    doc = nlp(text)
    
    # Diagnostic: Log tokenized sentence
    tokenized = " ".join([t.text for t in doc])
    print(f"[time-extract]: tokenized_sentence=\"{tokenized}\"")
    
    # Diagnostic: Log pre-normalized form (same as input for now)
    print(f"[time-extract]: pre_normalized=\"{text}\"")
    
    # Diagnostic: Manually test common time patterns against tokenized text
    # This helps diagnose why patterns aren't matching
    tokens = [t.text for t in doc]
    token_lowers = [t.lower_ for t in doc]
    print(f"[time-extract]: tokens={tokens} token_lowers={token_lowers}")
    
    # Test "at 10" pattern manually (updated for 0-23 range)
    if len(tokens) >= 2 and token_lowers[0] == "at" and tokens[1].isdigit():
        hour = int(tokens[1])
        if 0 <= hour <= 23:
            print(f"[time-extract]: trying pattern=\"time_bare_hour\" against text=\"{text}\" tokens={tokens}")
            print(f"[time-extract]: pattern=\"time_bare_hour\" -> POTENTIAL MATCH (at={token_lowers[0]}, hour={tokens[1]})")
        else:
            print(f"[time-extract]: trying pattern=\"time_bare_hour\" against text=\"{text}\" tokens={tokens}")
            print(f"[time-extract]: pattern=\"time_bare_hour\" -> NO MATCH (hour {hour} not in range 0-23)")
    elif len(tokens) >= 1 and tokens[0].isdigit():
        hour = int(tokens[0])
        if 0 <= hour <= 23:
            print(f"[time-extract]: trying pattern=\"time_bare_hour_standalone\" against text=\"{text}\" tokens={tokens}")
            print(f"[time-extract]: pattern=\"time_bare_hour_standalone\" -> POTENTIAL MATCH (hour={tokens[0]})")
        else:
            print(f"[time-extract]: trying pattern=\"time_bare_hour_standalone\" against text=\"{text}\" tokens={tokens}")
            print(f"[time-extract]: pattern=\"time_bare_hour_standalone\" -> NO MATCH (hour {hour} not in range 0-23)")
    else:
        print(f"[time-extract]: trying pattern=\"time_bare_hour\" against text=\"{text}\" tokens={tokens}")
        print(f"[time-extract]: pattern=\"time_bare_hour\" -> NO MATCH (no 'at' + hour pattern found)")
    
    result = {
        "services": [],  # Kept for compatibility - contains SERVICE_FAMILY entities
        "dates": [],  # Relative dates (today, tomorrow, etc.)
        # Absolute calendar dates (15th dec, 15/12/2025, etc.)
        "dates_absolute": [],
        "times": [],  # Precise clock times
        "time_windows": [],  # Coarse time ranges (morning, afternoon, etc.)
        "durations": []
    }

    # Diagnostic: Track TIME entities found
    time_entities_found = []
    
    for ent in doc.ents:
        label = ent.label_.upper()
        span_len = ent.end - ent.start

        if label == "SERVICE_FAMILY":
            # SERVICE_FAMILY entities are stored in "services" key for compatibility
            add_entity(result, "services", ent.text, ent.start, span_len)
        elif label == "DATE":
            # Relative dates (today, tomorrow, next week, etc.)
            add_entity(result, "dates", ent.text, ent.start, span_len)
        elif label == "DATE_ABSOLUTE":
            # Absolute calendar dates (15th dec, 15/12/2025, etc.)
            add_entity(result, "dates_absolute", ent.text, ent.start, span_len)
        elif label == "TIME":
            # Precise clock times (9 am, 12:30 pm, etc.)
            # Diagnostic: Log TIME entity match (using print to ensure visibility)
            # Check if this is an hour-only time (no colon, no am/pm)
            ent_text = ent.text.strip()
            is_hour_only = False
            hour_value = None
            
            # Detect hour-only pattern: "at 10", "10", etc. (no colon, no am/pm)
            if ":" not in ent_text and "am" not in ent_text.lower() and "pm" not in ent_text.lower():
                # Try to extract hour number
                import re
                hour_match = re.search(r'\b(\d{1,2})\b', ent_text)
                if hour_match:
                    hour_value = int(hour_match.group(1))
                    if 0 <= hour_value <= 23:
                        is_hour_only = True
                        print(
                            f"[time-extract]: matched hour-only time=\"{ent.text}\" → {hour_value:02d}:00")
            
            if not is_hour_only:
                print(f"[time-extract]: MATCH label=TIME raw=\"{ent.text}\" start={ent.start} end={ent.end}")
            time_entities_found.append(ent.text)
            add_entity(result, "times", ent.text, ent.start, span_len)
        elif label == "TIME_WINDOW":
            # Coarse time ranges (morning, afternoon, evening, night)
            add_entity(result, "time_windows", ent.text, ent.start, span_len)
        elif label == "DURATION":
            add_entity(result, "durations", ent.text, ent.start, span_len)
        # Noise is NOT extracted - it's not an entity

    # Diagnostic: Log final extracted times (using print to ensure visibility)
    print(f"[time-extract]: extracted_times={result['times']}")
    
    # Diagnostic: Check if timetoken was injected (will be checked after parameterization)
    # This will be logged in build_parameterized_sentence

    return result, doc


def build_parameterized_sentence(doc, entities: Dict[str, List]) -> str:
    """
    Build parameterized sentence by replacing entities with tokens.

    Noise is NOT replaced here - it's handled separately in post-processing.

    Replacement logic:
    - Each entity is replaced exactly once by its original token span (start, end)
    - Replacements are applied from end-to-start (backwards) to avoid index shifting
    - Multiple entities of same type are allowed (e.g., multiple timetoken)
    - No re-scanning or re-tokenization occurs during replacement
    """
    tokens = [t.text.lower() for t in doc]
    replacements = []

    placeholders = {
        "services": "servicefamilytoken",  # Updated to reflect SERVICE_FAMILY
        "dates": "datetoken",  # Relative dates (today, tomorrow, etc.)
        # Absolute calendar dates (15th dec, 15/12/2025, etc.)
        "dates_absolute": "datetoken",
        "times": "timetoken",  # Precise clock times (9 am, 12:30 pm, etc.)
        # Coarse time ranges (morning, afternoon, etc.)
        "time_windows": "timewindowtoken",
        "durations": "durationtoken"
    }

    # Collect all entity replacements with their exact token spans
    for entity_type, ents in entities.items():
        placeholder = placeholders.get(entity_type)
        if not placeholder:
            continue
        for e in ents:
            start = e["position"]
            end = start + e.get("length", 1)
            replacements.append((start, end, placeholder))

    # Sort by END position descending, then by START descending
    # This ensures we replace from right-to-left, preventing index shifts
    # Each entity is replaced exactly once by its original span
    replacements.sort(key=lambda x: (x[1], x[0]), reverse=True)

    # Apply replacements backwards (end-to-start) to avoid index shifting
    for start, end, placeholder in replacements:
        tokens[start:end] = [placeholder]

    psentence = " ".join(tokens)

    # Guard: Post-process to ensure no stray am/pm after timetoken replacement (merge them back)
    parts = psentence.split()
    merged = []
    i = 0
    while i < len(parts):
        if i < len(parts) - 1 and parts[i] == "timetoken" and parts[i+1] in {"am", "pm"}:
            # Merge am/pm into previous timetoken (just keep as one timetoken)
            merged.append("timetoken")
            i += 2  # skip the am/pm
        else:
            merged.append(parts[i])
            i += 1
    post_guarded = " ".join(merged)
    final_psentence = post_normalize_parameterized_text(post_guarded)
    
    # Diagnostic: Log whether timetoken was injected (using print to ensure visibility)
    has_timetoken = "timetoken" in final_psentence
    print(f"[time-extract]: psentence_after=\"{final_psentence}\" has_timetoken={has_timetoken}")
    
    return final_psentence


def canonicalize_services(
    services: List[Dict[str, Any]],
    service_map: Dict[str, str]
) -> List[str]:
    """
    Map natural language service text to canonical IDs.

    This function is called AFTER entity extraction.
    Input: entity["text"] contains natural language (e.g., "haircut")
    Output: canonical ID (e.g., "hair_trim")

    CRITICAL: This mapping happens AFTER spaCy processing.
    The doc tokens remain natural language for parameterization.
    Only the entity data structures are updated with canonical IDs.
    """
    canonical = []
    for s in services:
        text = s["text"].lower()  # Natural language text from extraction
        canonical.append(service_map.get(text, text))  # Map to canonical ID
    return canonical
