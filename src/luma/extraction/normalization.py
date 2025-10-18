"""
Text normalization utilities for entity extraction.

Handles pre-processing, post-processing, and text cleaning operations
before and after entity extraction.
"""
import re
import unicodedata
from typing import Dict

# Lazy import for optional spaCy dependency
try:
    import spacy  # type: ignore  # noqa: F401
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False

# ===== CONFIGURATION =====
from luma.config import config, debug_print


def normalize_hyphens(text: str) -> str:
    """
    Normalize all dash-like characters to a simple hyphen and
    remove spaces around them. Ensures variants like 'coca – cola'
    or 'coca - cola' become 'coca-cola'.
    
    Args:
        text: Input text with potentially varied dash characters
        
    Returns:
        Normalized text with standard hyphens
        
    NOTE: Matches semantics/nlp_processor.py lines 22-33 exactly
    """
    text = unicodedata.normalize("NFKC", text)
    # Replace en/em/minus dashes etc. with simple hyphen
    text = re.sub(r"[‐-‒–—−]", "-", text)
    # Remove spaces around hyphens
    text = re.sub(r"\s*-\s*", "-", text)
    return text


def pre_normalization(text: str) -> str:
    """
    Normalize text before spaCy processing:
    - Handle apostrophes and curly quotes (Kellogg's → Kelloggs)
    - Split digit-letter boundaries (5kg → 5 kg)
    - Convert "a/an/one + unit" → "1 + unit"
    - Add spaces around punctuation
    - Lowercase and normalize whitespace
    
    Args:
        text: Raw input text
        
    Returns:
        Normalized text ready for spaCy processing
        
    NOTE: Matches semantics/nlp_processor.py lines 514-555 exactly
    """
    # 1️⃣ Normalize Unicode (e.g., curly quotes)
    text = unicodedata.normalize("NFKC", text)
    
    # ✅ NEW: Fix spaced or dash variants (e.g. "coca - cola" → "coca-cola")
    text = re.sub(r"\s*[-–—−]\s*", "-", text)
    
    # 2️⃣ Normalize apostrophes and possessives
    text = text.replace("'", "'").replace("`", "'")
    text = re.sub(r"(\w)'s\b", r"\1s", text)
    text = re.sub(r"(\w)'(\w)", r"\1\2", text)
    
    # 3️⃣ Split digits and letters (5kg → 5 kg)
    text = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", text)
    text = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", text)
    
    # 4️⃣ Convert "a/an/one + unit" → "1 + unit"
    unit_pattern = r"\b(bag|tin|crate|bottle|pack|box|carton|kg|g|lb|liter|ml|case|sack|jar|can)s?\b"
    text = re.sub(rf"\b(a|an|one)\s+({unit_pattern})", r"1 \2", text, flags=re.IGNORECASE)
    
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
    Ensures placeholders are space-separated, punctuation is spaced, and
    text is clean for downstream token-level alignment or model input.
    
    Args:
        text: Parameterized text with tokens
        
    Returns:
        Cleaned parameterized text
        
    NOTE: Matches semantics/nlp_processor.py lines 557-584 exactly
    NOTE: Includes quantitytoken (unlike NER model) - semantics has it here
    """
    placeholder_pattern = r"(producttoken|brandtoken|varianttoken|unittoken|quantitytoken)"
    
    # 1️⃣ Split consecutive placeholders
    text = re.sub(rf"({placeholder_pattern})(?={placeholder_pattern})", r"\1 ", text)
    
    # 2️⃣ Add space between placeholder and adjacent letters
    text = re.sub(rf"({placeholder_pattern})([a-zA-Z])", r"\1 \2", text)
    text = re.sub(rf"([a-zA-Z])({placeholder_pattern})", r"\1 \2", text)
    
    # 3️⃣ Add spaces around punctuation near placeholders
    text = re.sub(rf"({placeholder_pattern})([.,!?;:])", r"\1 \2", text)
    text = re.sub(rf"([.,!?;:])({placeholder_pattern})", r"\1 \2", text)
    
    # 4️⃣ Collapse multiple spaces and trim
    text = re.sub(r"\s+", " ", text).strip()
    
    # 5️⃣ Lowercase
    text = text.lower()
    
    return text


def normalize_longest_phrases(text: str, synonym_map: Dict[str, str], max_n: int = 5) -> str:
    """
    Normalize using the longest valid phrase from synonym_map.
    Ensures 'soft drink' overrides 'soft' or 'drink'.
    
    Args:
        text: Input text to normalize
        synonym_map: Dictionary mapping phrases to canonical forms
        max_n: Maximum phrase length to check (default 5)
        
    Returns:
        Normalized text with longest phrases replaced
        
    NOTE: Matches semantics/nlp_processor.py lines 110-142 exactly
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
        matched_canonical = None
        
        # check from longest to shortest phrase
        for n in range(max_n, 0, -1):
            span = " ".join(words[i:i + n])
            if span in synonym_map:
                matched_len = n
                matched_canonical = synonym_map[span]
                break  # longest match wins
        
        if matched_canonical:
            normalized[i:i + matched_len] = [matched_canonical]
            skip_until = i + matched_len
        i += 1
    debug_print("normalized: ", normalized)
    
    return " ".join(normalized)


def normalize_plural_to_singular(text: str, nlp) -> str:
    """
    Converts plural nouns to singular using the provided spaCy pipeline.
    Reuses the already-loaded nlp instance to avoid reloading models.
    
    Args:
        text: Input text
        nlp: spaCy language model
        
    Returns:
        Text with plurals converted to singular
        
    NOTE: Matches semantics/nlp_processor.py lines 144-151 exactly
    """
    if not SPACY_AVAILABLE:
        raise ImportError("spaCy is required for plural normalization. Install with: pip install spacy")
    doc = nlp(text)
    normalized_tokens = [token.lemma_ if token.pos_ == "NOUN" else token.text for token in doc]
    return " ".join(normalized_tokens)

