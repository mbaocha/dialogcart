"""
Text normalization utilities for service and reservation entity extraction.

Handles pre-processing, post-processing, and text cleaning operations
before and after entity extraction.

Supports entity types:
- service, room_type, amenity
- date, time, duration
"""
import re
import unicodedata
from typing import Dict

# ===== CONFIGURATION =====
from luma.config import debug_print


def normalize_hyphens(text: str) -> str:
    """
    Normalize all dash-like characters to a simple hyphen and
    remove spaces around them.
    """
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[‐-‒–—−]", "-", text)
    text = re.sub(r"\s*-\s*", "-", text)
    return text


def pre_normalization(text: str) -> str:
    """
    Normalize text before entity grounding:
    - Unicode normalization
    - Apostrophe cleanup
    - Digit-letter splitting (12pm → 12 pm)
    - Punctuation spacing
    - Lowercasing & whitespace normalization
    """
    # 1️⃣ Unicode normalization
    text = unicodedata.normalize("NFKC", text)

    # 2️⃣ Normalize hyphen spacing
    text = re.sub(r"\s*[-–—−]\s*", "-", text)

    # 3️⃣ Normalize apostrophes / possessives
    text = text.replace("`", "'")
    text = re.sub(r"(\w)'s\b", r"\1s", text)
    text = re.sub(r"(\w)'(\w)", r"\1\2", text)

    # 4️⃣ Split digits and letters (12pm → 12 pm)
    text = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", text)
    text = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", text)

    # 4.5️⃣ Normalize abbreviated times with punctuation (6p → 6 pm, 6p, → 6 pm ,)
    # Handle cases like "6p", "6p,", "6p.", "6 pm,", "6 pm."
    # Must happen after digit-letter splitting but before punctuation spacing
    # Pattern: "6 p" or "6 p," → "6 pm" or "6 pm ,"
    # Match: digit + space + 'a' or 'p' (not followed by 'm') + optional punctuation or word boundary
    # Use lookahead to ensure we don't match "6 pm" (already has 'm')
    text = re.sub(
        r"\b(\d{1,2})\s+([ap])(?!m)(?=\s|[,\.]|\b)", r"\1 \2m", text, flags=re.IGNORECASE)

    # 5️⃣ Add spaces around punctuation
    text = re.sub(r"([.!?;:,])(?=\S)", r"\1 ", text)
    text = re.sub(r"(?<=\S)([.!?;:,])", r" \1", text)

    # 6️⃣ Normalize spaces
    text = re.sub(r"\s+", " ", text).strip()

    # 7️⃣ Lowercase
    text = text.lower()

    return text


def post_normalize_parameterized_text(text: str) -> str:
    """
    Clean and normalize parameterized text AFTER placeholders are inserted.
    Ensures tokens are space-separated and punctuation-safe.

    Supports placeholders:
    - servicetoken, roomtypetoken, amenitytoken
    - datetoken, timetoken, durationtoken
    """
    placeholder_pattern = r"(servicetoken|roomtypetoken|amenitytoken|datetoken|timetoken|durationtoken)"

    # 1️⃣ Split consecutive placeholders
    text = re.sub(
        rf"({placeholder_pattern})(?={placeholder_pattern})",
        r"\1 ",
        text,
    )

    # 2️⃣ Space between placeholder and letters
    text = re.sub(rf"({placeholder_pattern})([a-zA-Z])", r"\1 \2", text)
    text = re.sub(rf"([a-zA-Z])({placeholder_pattern})", r"\1 \2", text)

    # 3️⃣ Space around punctuation
    text = re.sub(rf"({placeholder_pattern})([.,!?;:])", r"\1 \2", text)
    text = re.sub(rf"([.,!?;:])({placeholder_pattern})", r"\1 \2", text)

    # 4️⃣ Collapse spaces
    text = re.sub(r"\s+", " ", text).strip()

    # 5️⃣ Lowercase
    text = text.lower()

    return text


def normalize_typos(text: str, typo_map: Dict[str, str]) -> str:
    """
    Normalize typos in closed-vocabulary terms only.

    This function performs deterministic, rule-based typo correction for
    bounded, closed-vocabulary terms (e.g., relative dates, time keywords).

    CRITICAL CONSTRAINTS:
    - Only fixes typos in closed-vocabulary terms (dates, times, etc.)
    - Must NOT correct open vocabulary (services, names, free text)
    - Canonical values must exist elsewhere in the global config
    - Word-boundary safe (whole-word matches only)
    - Semantic-blind (string replacement only, no entity inference)

    The typo_map is compiled from canonical-first JSON format at load time.
    Categories exist for human safety/auditing, not runtime logic.
    This prevents silent corruption (e.g., "may" → "maybe").

    Examples:
        "book me a haircut tomorow by 6pm" → "book me a haircut tomorrow by 6pm"
        "mornign appointment" → "morning appointment"

    Args:
        text: Input text (should already be lowercase and normalized)
        typo_map: Dictionary mapping typo variants to canonical forms

    Returns:
        Text with typos corrected (only closed-vocabulary terms)
    """
    if not typo_map:
        return text

    # Split text into words for word-boundary matching
    words = text.split()
    normalized = words[:]

    # Replace whole words only (word-boundary safe)
    # Simple word-by-word replacement (no need for longest-match since we're matching whole words)
    for i, word in enumerate(words):
        word_lower = word.lower()
        if word_lower in typo_map:
            # Replace with canonical form, preserving original case pattern if possible
            canonical = typo_map[word_lower]
            # If original was capitalized, capitalize canonical
            if word and word[0].isupper():
                canonical = canonical.capitalize()
            normalized[i] = canonical

    result = " ".join(normalized)
    debug_print("typo normalization:", result)
    return result


def normalize_orthography(text: str, rules: Dict[str, str]) -> str:
    """
    Apply orthographic normalization rules to standardize surface-form variations.

    This function performs deterministic, rule-based replacement of known
    orthographic variants with preferred natural language forms.

    Rules:
    - Case-insensitive matching
    - Longest-match-first replacement
    - Word boundary preservation (does not replace substrings inside words)
    - Natural language only (no canonical IDs)

    Examples:
        "hair cut" → "haircut"
        "6pm" → "6 pm"
        "pick up" → "pickup"

    Args:
        text: Input text (should already be lowercase)
        rules: Dictionary mapping variants to preferred forms

    Returns:
        Normalized text with orthographic variants replaced
    """
    if not rules:
        return text

    # Ensure text is lowercase
    text_lower = text.lower()
    words = text_lower.split()

    # Sort rules by length (longest first) for longest-match-first
    sorted_rules = sorted(
        rules.items(), key=lambda x: len(x[0].split()), reverse=True)

    normalized = words[:]
    skip_until = -1
    i = 0

    while i < len(words):
        if i < skip_until:
            i += 1
            continue

        matched_len = 0
        matched_replacement = None

        # Try longest matches first
        for variant, preferred in sorted_rules:
            variant_words = variant.lower().split()
            variant_len = len(variant_words)

            # Check if we have enough words remaining
            if i + variant_len > len(words):
                continue

            # Check for exact word sequence match
            if words[i:i+variant_len] == variant_words:
                matched_len = variant_len
                matched_replacement = preferred
                break

        if matched_replacement:
            # Replace matched words with preferred form
            # Split replacement in case it's multi-word
            replacement_words = matched_replacement.lower().split()
            normalized[i:i+matched_len] = replacement_words
            skip_until = i + matched_len

        i += 1

    result = " ".join(normalized)
    debug_print("orthographic normalization:", result)
    return result


def normalize_natural_language_variants(
    text: str,
    variant_map: Dict[str, str],
    max_n: int = 5,
) -> str:
    """
    Normalize natural language variants to preferred forms.

    This function ONLY normalizes natural language variants (e.g., "hair cut" → "haircut").
    It NEVER injects canonical IDs (e.g., "hair_trim") into the text.

    The variant_map should map variants to preferred natural language forms,
    not to internal canonical identifiers. The map-building function ensures
    canonical IDs are excluded from the variant_map.

    Used for:
    - Collapsing spelling variants ("hair cut" → "haircut")
    - Normalizing spacing in compound terms
    - Standardizing natural language forms

    CRITICAL: Input to spaCy must remain natural language only.
    Canonical ID mapping happens AFTER entity extraction.
    """
    words = text.lower().split()
    normalized = words[:]
    skip_until = -1
    i = 0

    while i < len(words):
        if i < skip_until:
            i += 1
            continue

        matched_len = 0
        matched_value = None

        # Try longest matches first
        for n in range(max_n, 0, -1):
            span = " ".join(words[i: i + n])
            if span in variant_map:
                matched_len = n
                matched_value = variant_map[span]
                break

        if matched_value:
            normalized[i: i + matched_len] = [matched_value]
            skip_until = i + matched_len

        i += 1

    debug_print("normalized (natural language only):", normalized)
    return " ".join(normalized)
