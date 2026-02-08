"""
Option-constrained resolution for clarification turns.

When explicit options are provided in tenant_context, user input is validated
only against those options. This enables deterministic resolution for clarification
turns such as "Which service do you want? 1. haircut 2. hairtrim"

Resolution rules (in order):
1. Numeric selection: "1" → first option, "2" → second option, etc.
2. Exact label match: case-insensitive, whitespace-normalized
3. Fuzzy label match: edit distance similarity ≥ 0.8
"""

import re
from typing import Dict, Any, Optional

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


def normalize_text(text: str) -> str:
    """
    Normalize text for matching: lowercase, collapse whitespace.
    
    Args:
        text: Input text
        
    Returns:
        Normalized text
    """
    if not text:
        return ""
    # Lowercase and collapse whitespace
    normalized = re.sub(r'\s+', ' ', text.lower().strip())
    return normalized


def resolve_option(text: str, options: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Resolve user input against provided options.
    
    Args:
        text: User input text
        options: Options dict with:
            - type: str (e.g., "service")
            - slot: str (e.g., "service_id")
            - choices: list of {id: str, label: str}
    
    Returns:
        {slot: str, value: str} on success, None on failure/ambiguity
    """
    if not options or not isinstance(options, dict):
        return None
    
    slot = options.get("slot")
    choices = options.get("choices", [])
    
    if not slot or not choices or not isinstance(choices, list):
        return None
    
    if not text or not isinstance(text, str):
        return None
    
    text_normalized = normalize_text(text)
    
    # Rule 1: Numeric selection
    # Match pure numeric strings (e.g., "1", "2", "10")
    numeric_match = re.match(r'^\s*(\d+)\s*$', text.strip())
    if numeric_match:
        try:
            index = int(numeric_match.group(1))
            # Convert to 0-based index
            choice_index = index - 1
            if 0 <= choice_index < len(choices):
                choice = choices[choice_index]
                if isinstance(choice, dict) and "id" in choice:
                    return {
                        "slot": slot,
                        "value": choice["id"]
                    }
            # Out of range
            return None
        except (ValueError, IndexError):
            return None
    
    # Rule 2: Exact label match (case-insensitive, whitespace-normalized)
    exact_matches = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        label = choice.get("label", "")
        if not label:
            continue
        
        label_normalized = normalize_text(label)
        if label_normalized == text_normalized:
            exact_matches.append(choice)
    
    if len(exact_matches) == 1:
        return {
            "slot": slot,
            "value": exact_matches[0]["id"]
        }
    elif len(exact_matches) > 1:
        # Multiple exact matches (shouldn't happen with normalized labels, but handle it)
        return None
    
    # Rule 3: Fuzzy label match (similarity ≥ 0.8)
    if not HAS_RAPIDFUZZ:
        # Fallback: no fuzzy matching available
        return None
    
    fuzzy_matches = []
    best_score = 0.0
    
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        label = choice.get("label", "")
        if not label:
            continue
        
        # Use ratio for overall similarity
        score = fuzz.ratio(text_normalized, normalize_text(label)) / 100.0
        
        if score >= 0.8:
            fuzzy_matches.append((choice, score))
            if score > best_score:
                best_score = score
    
    if len(fuzzy_matches) == 0:
        # No fuzzy matches
        return None
    elif len(fuzzy_matches) == 1:
        # Single fuzzy match
        return {
            "slot": slot,
            "value": fuzzy_matches[0][0]["id"]
        }
    else:
        # Multiple fuzzy matches - check if one is clearly better
        # Sort by score descending
        fuzzy_matches.sort(key=lambda x: x[1], reverse=True)
        
        # If top match is significantly better (≥0.05 difference), use it
        if fuzzy_matches[0][1] - fuzzy_matches[1][1] >= 0.05:
            return {
                "slot": slot,
                "value": fuzzy_matches[0][0]["id"]
            }
        else:
            # Ambiguous - multiple matches with similar scores
            return None

