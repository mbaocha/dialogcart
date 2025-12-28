"""
Stage 1: Entity Extraction & Parameterization

Service and reservation entity matcher for DialogCart.

Extracts and parameterizes entities for service-based appointment booking
and reservation systems.
"""
from .service_annotation import (
    annotate_service_tokens,
    consume_service_annotations,
)
from .entity_processing import (
    extract_entities_from_doc,
    build_parameterized_sentence,
)
from .entity_loading import (
    init_nlp_with_business_categories,
    load_global_noise_set,
    load_global_orthography_rules,
    load_global_business_categories,
    build_business_category_synonym_map,
    load_global_entity_types,
    get_global_json_path,
)
from .vocabulary_normalization import (
    load_vocabularies,
    compile_vocabulary_maps,
    normalize_vocabularies,
    validate_vocabularies,
)
from .normalization import (
    normalize_hyphens,
    pre_normalization,
    normalize_orthography,
    normalize_natural_language_variants,
)
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
import re
import logging

logger = logging.getLogger(__name__)

try:
    from luma.config import debug_print
    from luma.clarification import Clarification, ClarificationReason
except ImportError:  # pragma: no cover - fallback for static analysis
    def debug_print(*args, **kwargs):
        return None

    class Clarification:  # type: ignore
        def __init__(self, *args, **kwargs):
            pass

        def to_dict(self):
            return {}

    class ClarificationReason:  # type: ignore
        CONFLICTING_SIGNALS = "CONFLICTING_SIGNALS"
        CONTEXT_DEPENDENT_VALUE = "CONTEXT_DEPENDENT_VALUE"


# ------------------------------------------------------------------
# Domain configuration
# ------------------------------------------------------------------

DOMAIN_ENTITY_WHITELIST = {
    "service": {
        "business_categories", "dates", "dates_absolute", "times", "time_windows", "durations"
    },
    "reservation": {
        "business_categories", "dates", "dates_absolute", "times", "time_windows", "durations"
    }
}


def _build_natural_language_variant_map_from_business_categories(
    business_categories: Dict[str, Dict[str, Any]]
) -> Dict[str, str]:
    """
    Build natural language variant map from business categories.

    Maps business category synonyms to a preferred natural language form (first synonym).
    This map is used to normalize variants like "hair cut" → "haircut".

    CRITICAL: This map MUST NOT contain canonical IDs (e.g., "beauty_and_wellness.haircut").
    It only maps natural language variants to other natural language forms.

    Example:
        business_categories: {
            "beauty_and_wellness": {
                "haircut": {"synonym": ["haircut", "hair trim"]}
            }
        }
        Maps: "haircut" → "haircut", "hair trim" → "haircut"
        (Uses first synonym as preferred form)
    """
    variant_map = {}

    for _category, families in business_categories.items():
        if not isinstance(families, dict):
            continue

        for _family_id, family_data in families.items():
            if not isinstance(family_data, dict):
                continue

            synonyms = family_data.get("synonym", [])
            if not isinstance(synonyms, list) or not synonyms:
                continue

            # Use first synonym as the preferred natural language form
            preferred_form = synonyms[0].lower()

            # Map all synonyms (including preferred) to preferred form
            for synonym in synonyms:
                if isinstance(synonym, str):
                    variant_map[synonym.lower()] = preferred_form

    return variant_map


def detect_tenant_alias_spans(
    normalized_text: str, tenant_aliases: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    Detect tenant alias spans on normalized text BEFORE spaCy extraction.

    Returns list of dicts:
    [
        {
            "start_char": int,
            "end_char": int,
            "text": str,
            "canonical": str,
        }
    ]

    - Exact phrase match (case-insensitive) on normalized text
    - Longest-match wins (sort by phrase length desc)
    - Word-boundary safe
    - Fuzzy matching (90%+ threshold) - post-processing to handle typos

    Uses compiled alias structure for performance (with fallback to slow path).
    """
    if not tenant_aliases:
        return []

    # Try optimized compiled version first
    spans = None
    compiled_structure = None
    try:
        from luma.normalization.alias_compiler import get_compiled_aliases
        # Get compiled structure (cached) to reuse sorted aliases for fuzzy matching
        compiled_structure = get_compiled_aliases(tenant_aliases)
        if compiled_structure is not None:
            result = compiled_structure.detect_spans(normalized_text)
            # If result is not None, use it (empty list means no aliases, which is valid)
            if result is not None:
                spans = result
                # Ensure all spans have match_type for post-processing
                for span in spans:
                    if "match_type" not in span:
                        span["match_type"] = "exact"
    except Exception:
        # Fallback to slow path if import or call fails
        pass

    # If compiled version didn't work, use slow path (which includes fuzzy matching)
    if spans is None:
        return _detect_tenant_alias_spans_slow(normalized_text, tenant_aliases)

    # Compiled version was used - apply fuzzy matching as post-processing
    # to handle typos and prefer longer matches over shorter exact matches
    # Pass compiled_structure to reuse pre-sorted aliases
    return _apply_fuzzy_matching_post_process(
        normalized_text, tenant_aliases, spans, compiled_structure
    )


def _apply_fuzzy_matching_post_process(
    normalized_text: str,
    tenant_aliases: Dict[str, str],
    existing_spans: List[Dict[str, Any]],
    compiled_structure: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    Apply fuzzy matching as post-processing to handle typos in tenant aliases.

    Processes phrases by word count in descending order: 4-word → 3-word → 2-word → 1-word.
    This ensures longer matches are preferred over shorter ones.

    This runs even when exact matches exist (from compiled version), to prefer
    longer fuzzy matches over shorter exact matches.
    Example: "premium suite" (user) should fuzzy match "premum suite" (tenant typo)
    instead of just "suite" (shorter exact match).

    Single-word typos (e.g., "massge" → "massage") use fuzz.ratio() with 85% threshold.
    Multi-word phrases use fuzz.token_sort_ratio() with 90% threshold.

    Args:
        normalized_text: The normalized input text
        tenant_aliases: Dict mapping alias -> canonical (used if compiled_structure not available)
        existing_spans: Spans found by exact matching (from compiled or slow path)
        compiled_structure: Optional CompiledAliasStructure with pre-sorted aliases (for performance)

    Returns:
        Updated spans list with fuzzy matches applied
    """
    try:
        from rapidfuzz import fuzz
    except ImportError:
        # rapidfuzz not available, return existing spans
        return existing_spans

    text_lower = normalized_text.lower()
    spans = existing_spans.copy()
    used_ranges = [(s["start_char"], s["end_char"]) for s in spans]

    # Use pre-sorted aliases from compiled structure if available (avoid re-sorting)
    if compiled_structure and hasattr(compiled_structure, 'sorted_aliases_tuples'):
        sorted_aliases = compiled_structure.sorted_aliases_tuples
    else:
        # Fallback: sort aliases by token length desc, then char length desc for deterministic longest-first
        sorted_aliases = sorted(
            tenant_aliases.items(),
            key=lambda kv: (len(kv[0].split()), len(kv[0])),
            reverse=True,
        )

    tokens = text_lower.split()
    n_tokens = len(tokens)

    # Process by word count in descending order: 4 → 3 → 2 → 1
    # This ensures longer matches are preferred over shorter ones
    for word_count in range(4, 0, -1):  # 4, 3, 2, 1
        if word_count > n_tokens:
            continue

        # Generate candidate phrases of this word count
        candidate_phrases: List[Tuple[int, int, str]] = []

        for start in range(n_tokens - word_count + 1):
            end = start + word_count
            phrase = " ".join(tokens[start:end])

            # Find actual character positions in normalized text
            # For single words, use word boundaries to avoid partial matches
            if word_count == 1:
                pattern_escaped = r"\b" + re.escape(phrase) + r"\b"
            else:
                pattern_escaped = re.escape(phrase)
            match_obj = re.search(pattern_escaped, normalized_text.lower())
            if not match_obj:
                continue
            start_char_pos, end_char_pos = match_obj.span()

            # Skip if this phrase is completely contained within an existing span
            # (but allow longer phrases that contain existing spans - we'll remove the shorter ones)
            if any(
                start_char_pos >= u_start and end_char_pos <= u_end
                for u_start, u_end in used_ranges
            ):
                continue

            candidate_phrases.append((start_char_pos, end_char_pos, phrase))

        # Sort by position (left to right) for deterministic processing within same word count
        candidate_phrases.sort(key=lambda x: x[0])

        # Try fuzzy matching against tenant aliases for this word count
        for start_char_pos, end_char_pos, phrase in candidate_phrases:
            if not phrase or len(phrase.strip()) < 3:
                continue

            # Skip single words that are too short
            if word_count == 1 and len(phrase.strip()) < 4:
                continue

            best_match = None
            best_score = 0
            best_alias = None
            best_canonical = None

            # Filter aliases by word count: single words only match single-word aliases
            # Multi-word phrases can match any alias
            aliases_to_check = sorted_aliases
            if word_count == 1:
                aliases_to_check = [
                    (alias, canonical)
                    for alias, canonical in sorted_aliases
                    if isinstance(alias, str) and " " not in alias.lower().strip()
                ]

            for alias, canonical in aliases_to_check:
                if not isinstance(alias, str) or not isinstance(canonical, str):
                    continue
                alias_lower = alias.lower().strip()
                if not alias_lower:
                    continue

                # For single words, use ratio; for multi-word, use token_sort_ratio
                if word_count == 1:
                    score = fuzz.ratio(phrase, alias_lower)
                    threshold = 85  # Lower threshold for single-word typos
                else:
                    score = fuzz.token_sort_ratio(phrase, alias_lower)
                    threshold = 90  # Higher threshold for multi-word phrases

                if score >= threshold and score > best_score:
                    best_score = score
                    best_match = phrase
                    best_alias = alias
                    best_canonical = canonical

            if best_match and best_alias:
                # Check if this fuzzy match overlaps with any existing exact match
                overlapping_spans = [
                    s for s in spans
                    if not (end_char_pos <= s["start_char"] or start_char_pos >= s["end_char"])
                ]

                # If fuzzy match overlaps with exact matches, remove the shorter exact matches
                if overlapping_spans:
                    # Remove shorter exact matches that are contained within this longer fuzzy match
                    spans = [
                        s for s in spans
                        if not (
                            s["start_char"] >= start_char_pos and
                            s["end_char"] <= end_char_pos and
                            s.get("match_type", "exact") == "exact"
                        )
                    ]
                    # Update used_ranges to remove the removed spans
                    used_ranges = [
                        (u_start, u_end) for u_start, u_end in used_ranges
                        if not (
                            u_start >= start_char_pos and
                            u_end <= end_char_pos
                        )
                    ]

                # Add the fuzzy match
                spans.append(
                    {
                        "start_char": start_char_pos,
                        "end_char": end_char_pos,
                        "text": normalized_text[start_char_pos:end_char_pos],
                        "canonical": best_canonical,
                        "alias_key": best_alias,
                        "match_type": "fuzzy",
                        "fuzzy_score": best_score,
                    }
                )
                used_ranges.append((start_char_pos, end_char_pos))
                # Only match one fuzzy alias per phrase
                break

    return spans


def _detect_tenant_alias_spans_slow(
    normalized_text: str, tenant_aliases: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    Slow-path implementation of alias span detection.

    Used as fallback when compiled version is unavailable.

    Matching strategy:
    1. Exact phrase match (word-boundary) - primary method
    2. Fuzzy matching (90%+ threshold) - fallback when exact match fails
    3. Prefer longer fuzzy matches over shorter exact matches when they overlap
    """
    if not tenant_aliases:
        return []

    text_lower = normalized_text.lower()
    spans: List[Dict[str, Any]] = []

    # Sort aliases by token length desc, then char length desc for deterministic longest-first
    sorted_aliases = sorted(
        tenant_aliases.items(),
        key=lambda kv: (len(kv[0].split()), len(kv[0])),
        reverse=True,
    )

    used_ranges: List[Tuple[int, int]] = []

    # Phase 1: Exact matching
    for alias, canonical in sorted_aliases:
        if not isinstance(alias, str) or not isinstance(canonical, str):
            continue
        alias_lower = alias.lower().strip()
        if not alias_lower:
            continue

        pattern = r"\b" + re.escape(alias_lower) + r"\b"
        for match in re.finditer(pattern, text_lower):
            start_char, end_char = match.span()

            # Skip if overlaps an already-matched longer alias
            overlap = any(
                not (end_char <= u_start or start_char >= u_end)
                for u_start, u_end in used_ranges
            )
            if overlap:
                continue

            spans.append(
                {
                    "start_char": start_char,
                    "end_char": end_char,
                    "text": normalized_text[start_char:end_char],
                    "canonical": canonical,  # alias value (canonical_family)
                    "alias_key": alias,  # alias key (tenant_service_id)
                    "match_type": "exact",
                }
            )
            used_ranges.append((start_char, end_char))

    # Phase 2: Fuzzy matching (runs even if exact matches exist)
    # This handles typos in tenant aliases and prefers longer matches over shorter ones
    # Example: "premium suite" (user) should fuzzy match "premum suite" (tenant typo)
    # even if "suite" (shorter exact match) was found
    try:
        from rapidfuzz import fuzz
    except ImportError:
        # rapidfuzz not available, skip fuzzy matching
        return spans

    # Extract potential phrases from text (2-4 word spans)
    tokens = text_lower.split()
    n_tokens = len(tokens)
    candidate_phrases: List[Tuple[int, int, str]] = []

    # Generate candidate phrases (2-4 tokens)
    for n in range(2, min(5, n_tokens + 1)):
        for start in range(n_tokens - n + 1):
            end = start + n
            phrase = " ".join(tokens[start:end])

            # Find actual character positions in normalized text
            # Search for the phrase as it appears in the text
            pattern_escaped = re.escape(phrase)
            match_obj = re.search(pattern_escaped, normalized_text.lower())
            if not match_obj:
                continue
            start_char_pos, end_char_pos = match_obj.span()

            candidate_phrases.append((start_char_pos, end_char_pos, phrase))

    # Sort by length (longest first) for priority
    candidate_phrases.sort(key=lambda x: len(x[2]), reverse=True)

    # Try fuzzy matching against tenant aliases
    for start_char_pos, end_char_pos, phrase in candidate_phrases:
        if not phrase or len(phrase.strip()) < 3:
            continue

        best_match = None
        best_score = 0
        best_alias = None
        best_canonical = None

        for alias, canonical in sorted_aliases:
            if not isinstance(alias, str) or not isinstance(canonical, str):
                continue
            alias_lower = alias.lower().strip()
            if not alias_lower:
                continue

            # Use token_sort_ratio for better multi-word matching
            score = fuzz.token_sort_ratio(phrase, alias_lower)
            if score >= 90 and score > best_score:
                best_score = score
                best_match = phrase
                best_alias = alias
                best_canonical = canonical

        if best_match and best_alias:
            # Check if this fuzzy match overlaps with any existing exact match
            # If it does and the fuzzy match is longer, prefer the fuzzy match
            overlapping_spans = [
                s for s in spans
                if not (end_char_pos <= s["start_char"] or start_char_pos >= s["end_char"])
            ]

            # If fuzzy match overlaps with exact matches, remove the shorter exact matches
            if overlapping_spans:
                # Remove shorter exact matches that are contained within this longer fuzzy match
                spans = [
                    s for s in spans
                    if not (
                        s["start_char"] >= start_char_pos and
                        s["end_char"] <= end_char_pos and
                        s.get("match_type") == "exact"
                    )
                ]
                # Update used_ranges to remove the removed spans
                used_ranges = [
                    (u_start, u_end) for u_start, u_end in used_ranges
                    if not (
                        u_start >= start_char_pos and
                        u_end <= end_char_pos
                    )
                ]

            # Add the fuzzy match
            spans.append(
                {
                    "start_char": start_char_pos,
                    "end_char": end_char_pos,
                    "text": normalized_text[start_char_pos:end_char_pos],
                    "canonical": best_canonical,
                    "alias_key": best_alias,
                    "match_type": "fuzzy",
                    "fuzzy_score": best_score,
                }
            )
            used_ranges.append((start_char_pos, end_char_pos))
            # Only match one fuzzy alias per phrase
            break

    return spans


def _map_char_span_to_token_span(
    doc, start_char: int, end_char: int
) -> Optional[Tuple[int, int]]:
    """
    Map a character span to token start/end (exclusive) using spaCy doc.
    Returns None if no token overlaps span.
    """
    start_token = None
    end_token = None
    for i, tok in enumerate(doc):
        if tok.idx >= end_char:
            break
        if tok.idx + len(tok) <= start_char:
            continue
        # token overlaps
        if start_token is None:
            start_token = i
        end_token = i + 1
    if start_token is None or end_token is None:
        return None
    return start_token, end_token


def merge_alias_spans_into_services(
    raw_result: Dict[str, Any], doc, alias_spans: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Insert tenant alias spans as service entities and remove overlapping generic services.

    - Converts char spans to token spans against the spaCy doc
    - Adds services with full alias text + canonical
    - Removes generic services that overlap alias spans
    """
    services = raw_result.get("services", []) or []
    alias_services: List[Dict[str, Any]] = []
    alias_token_ranges: List[Tuple[int, int]] = []

    for span in alias_spans:
        mapped = _map_char_span_to_token_span(
            doc, span["start_char"], span["end_char"]
        )
        if not mapped:
            continue
        start_tok, end_tok = mapped
        alias_services.append(
            {
                "text": span["text"],
                "position": start_tok,
                "length": end_tok - start_tok,
                "canonical": span["canonical"],
            }
        )
        alias_token_ranges.append((start_tok, end_tok))

    # Filter out generic services that overlap alias spans
    filtered_services = []
    for svc in services:
        start = svc.get("position", 0)
        length = svc.get("length", 1)
        end = start + length
        overlaps = any(
            not (end <= a_start or start >= a_end)
            for a_start, a_end in alias_token_ranges
        )
        if overlaps:
            continue
        filtered_services.append(svc)

    # Merge alias services (ensure deterministic order: existing + alias)
    merged_services = filtered_services + alias_services
    raw_result["services"] = merged_services

    # Store spans for downstream context/debugging
    if alias_services:
        raw_result["_tenant_alias_spans"] = [
            {
                "text": svc["text"],
                "canonical": svc["canonical"],
                "start": svc["position"],
                "end": svc["position"] + svc["length"],
            }
            for svc in alias_services
        ]

    return raw_result


class EntityMatcher:
    """
    Service and reservation entity matching and parameterization system.

    This class does NOT decide intent.
    It only extracts and parameterizes domain-relevant entities.
    """

    def __init__(
        self,
        domain: str,
        entity_file: Optional[str] = None,
        lazy_load_spacy: bool = False
    ):
        """
        Args:
            domain: "service" | "reservation"
            entity_file: Path to entity JSON file
            lazy_load_spacy: Skip spaCy init (testing)
        """
        if domain not in DOMAIN_ENTITY_WHITELIST:
            raise ValueError(
                f"Unsupported domain: {domain}. Must be one of: {list(DOMAIN_ENTITY_WHITELIST.keys())}")

        self.domain = domain

        # Find global JSON file (required for business categories)
        entity_path = Path(entity_file).resolve() if entity_file else None
        if entity_path:
            # Look for global JSON in same directory as entity_file
            base_dir = entity_path.parent
        else:
            # Use standard location
            base_dir = None

        global_json_path = get_global_json_path(base_dir)

        # Load global business categories (GLOBAL semantic concepts)
        self.business_categories = load_global_business_categories(
            global_json_path)
        debug_print(
            "[EntityMatcher] Loaded business categories from global JSON")

        # Build natural language variant map from business categories
        # This maps variants to preferred natural language forms (NOT canonical IDs)
        self.variant_map = _build_natural_language_variant_map_from_business_categories(
            self.business_categories)

        # Build business category synonym map (for canonicalization)
        self.business_category_map = build_business_category_synonym_map(
            self.business_categories)

        # Load global normalization (orthography, noise, vocabularies)
        self.noise_set = load_global_noise_set(global_json_path)
        self.orthography_rules = load_global_orthography_rules(
            global_json_path)

        # Load vocabularies with synonyms and typos
        vocabularies = load_vocabularies(global_json_path)
        self.vocabularies = vocabularies
        entity_types = load_global_entity_types(global_json_path)
        business_categories = load_global_business_categories(global_json_path)

        # Validate vocabularies
        validate_vocabularies(vocabularies, entity_types, business_categories)

        # Compile vocabulary maps
        self.synonym_map, self.typo_map, self.all_canonicals = compile_vocabulary_maps(
            vocabularies)

        # Load global entity types (date, time, duration)
        self.entity_types = load_global_entity_types(global_json_path)

        # Tenant entities are unused (kept for backward compatibility)
        self.entities = []
        self.service_map = {}  # Empty - business categories use business_category_map instead

        # spaCy init with business categories
        self.nlp = None
        if not lazy_load_spacy:
            self.nlp, _ = init_nlp_with_business_categories(global_json_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_with_parameterization(
        self,
        text: str,
        debug_units: bool = False,
        request_id: Optional[str] = None,
        tenant_aliases: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Main extraction entry point.

        Pipeline:
        1. Normalize text (natural language only - no canonical IDs)
        2. Extract entities via spaCy
        3. Canonicalize services
        4. Build parameterized sentence
        5. Domain-specific filtering
        6. Return domain-native output
        """
        if self.nlp is None:
            raise RuntimeError("spaCy not initialized")

        # request_id is reserved for future logging/debug; unused for now
        _ = request_id

        # 1️⃣ Normalize (natural language only - NO canonical IDs)
        # Pipeline order: lowercase → orthography → vocabulary (synonyms + typos) → noise removal
        text = normalize_hyphens(text)
        normalized = pre_normalization(text)  # lowercase, unicode, etc.
        # Apply orthographic normalization (surface-form standardization)
        normalized = normalize_orthography(normalized, self.orthography_rules)
        # Apply vocabulary normalization (synonyms and typos → canonical)
        normalized, normalized_from_correction = normalize_vocabularies(
            normalized, self.synonym_map, self.typo_map
        )

        # 1.5️⃣ Pre-extraction tenant alias span detection (on normalized text BEFORE service-family reduction)
        alias_spans = detect_tenant_alias_spans(
            normalized, tenant_aliases or {})

        # Apply natural language variant normalization (synonym mapping for services)
        # ONLY if no tenant alias spans were found, to avoid collapsing aliases
        if not alias_spans:
            normalized = normalize_natural_language_variants(
                normalized, self.variant_map)

        # 2️⃣ Extract entities from spaCy doc
        raw_result, doc = extract_entities_from_doc(self.nlp, normalized)

        # 2.5️⃣ Apply temporal inference rules if enabled
        from ..config.temporal_rules import TEMPORAL_RULES
        if TEMPORAL_RULES.allow_partial_meridiem_propagation:
            raw_result = self._propagate_meridiem_in_ranges(
                normalized, raw_result, doc)
        if TEMPORAL_RULES.allow_time_of_day_inference:
            raw_result = self._infer_meridiem_from_time_window(
                normalized, raw_result, doc)

        # Detect range tails like "oct 5th to 9th" where only the first absolute
        # date is extracted. If safe, synthesize a second absolute date using the
        # same month/year. If unsafe (tail < start), mark ambiguity for downstream
        # clarification.
        self._maybe_expand_absolute_date_range_tail(raw_result)

        # 3️⃣ TWO-STEP SERVICE RESOLUTION PIPELINE
        # Step 1: Non-destructive annotation (mark ALIAS/FAMILY/MODIFIER tokens without removing them)
        service_annotations = annotate_service_tokens(
            doc=doc,
            alias_spans=alias_spans,
            services=raw_result.get("services", []),
            business_category_map=self.business_category_map,
            tenant_aliases=tenant_aliases
        )

        # Log annotated sentence (Step 1 output)
        if request_id:
            logger.debug(
                f"[annotation] Step 1 annotated sentence for request {request_id}",
                extra={
                    'request_id': request_id,
                    'alias_count': len(service_annotations.get("alias_annotations", [])),
                    'family_count': len(service_annotations.get("family_annotations", []))
                }
            )

        # Step 2: Deterministic consumption pass (replace ALIAS with servicetenanttoken, FAMILY with servicefamilytoken)
        psentence_services, consumption_metadata = consume_service_annotations(
            doc=doc,
            annotations=service_annotations,
            logger_instance=logger
        )

        # Log consumed sentence (Step 2 output)
        if request_id:
            logger.debug(
                f"[consumption] Step 2 consumed sentence for request {request_id}",
                extra={
                    'request_id': request_id,
                    'has_alias': consumption_metadata.get("has_alias", False),
                    'parameterized_sentence': psentence_services
                }
            )

        # Rebuild full parameterized sentence: services from consumption + other entities from build_parameterized_sentence
        final_tokens = [t.text.lower() for t in doc]
        all_replacements = []

        # Add service replacements from consumption (servicetenanttoken or servicefamilytoken)
        alias_ranges = service_annotations.get("alias_token_ranges", [])

        if consumption_metadata.get("has_alias"):
            # Aliases present: use servicetenanttoken
            for alias_ann in service_annotations.get("alias_annotations", []):
                start = alias_ann["start_token"]
                end = alias_ann["end_token"]
                all_replacements.append((start, end, "servicetenanttoken"))
        else:
            # No aliases: use servicefamilytoken for non-suppressed families
            for family_ann in service_annotations.get("family_annotations", []):
                start = family_ann["start_token"]
                end = family_ann["end_token"]
                # Check if not suppressed by alias
                suppressed = any(
                    not (end <= a_start or start >= a_end)
                    for a_start, a_end in alias_ranges
                )
                if not suppressed:
                    all_replacements.append((start, end, "servicefamilytoken"))

        # Add other entity replacements (dates, times, durations)
        placeholder_map = {
            "dates": "datetoken",
            "dates_absolute": "datetoken",
            "times": "timetoken",
            "time_windows": "timewindowtoken",
            "durations": "durationtoken"
        }
        for entity_type, ents in raw_result.items():
            if entity_type == "services" or entity_type.startswith("_"):
                continue
            placeholder = placeholder_map.get(entity_type)
            if placeholder:
                for e in ents:
                    start = e.get("position", 0)
                    end = start + e.get("length", 1)
                    all_replacements.append((start, end, placeholder))

        # Apply all replacements backwards (end-to-start) to avoid index shifting
        all_replacements.sort(key=lambda x: (x[1], x[0]), reverse=True)
        for start, end, placeholder in all_replacements:
            final_tokens[start:end] = [placeholder]

        psentence = " ".join(final_tokens)

        # Post-normalize parameterized text
        from .normalization import post_normalize_parameterized_text
        psentence = post_normalize_parameterized_text(psentence)

        # Build business_categories from annotations (for downstream use)
        # MODIFIER annotations are NOT included in business_categories (modifiers are not services)
        business_categories = []
        # Add aliases as business categories with tenant_service_id and canonical_family
        for alias_ann in service_annotations.get("alias_annotations", []):
            business_categories.append({
                "text": alias_ann["text"],
                # alias value = canonical_family
                "canonical": alias_ann.get("canonical_family"),
                # alias key = tenant_service_id
                "tenant_service_id": alias_ann["tenant_service_id"],
                "start": alias_ann["start_token"],
                "end": alias_ann["end_token"],
                "annotation_type": "ALIAS"
            })
        # Add families as business categories (only if not suppressed)
        # Note: MODIFIER annotations are excluded - modifiers are not services
        for family_ann in service_annotations.get("family_annotations", []):
            start = family_ann["start_token"]
            end = family_ann["end_token"]
            suppressed = any(
                not (end <= a_start or start >= a_end)
                for a_start, a_end in alias_ranges
            )
            if not suppressed:
                business_categories.append({
                    "text": family_ann["text"],
                    "canonical": family_ann["canonical_family"],
                    "start": start,
                    "end": end,
                    "annotation_type": "FAMILY"
                })

        # Store consumption metadata and annotations for decision layer
        raw_result["_service_consumption_metadata"] = consumption_metadata
        raw_result["_service_annotations"] = service_annotations

        # Build phase1_replacements for backward compatibility (empty - replaced by consumption_metadata)
        phase1_replacements = []

        # Build phase2_replacements for logging (non-service entities)
        phase2_replacements = []
        for entity_type, ents in raw_result.items():
            if entity_type in ("dates", "dates_absolute"):
                for e in ents:
                    phase2_replacements.append({
                        "type": "date",
                        "span": e.get("text", ""),
                        "replaced_with": "datetoken"
                    })
            elif entity_type in ("times", "time_windows"):
                for e in ents:
                    phase2_replacements.append({
                        "type": "time",
                        "span": e.get("text", ""),
                        "replaced_with": "timetoken" if entity_type == "times" else "timewindowtoken"
                    })

        osentence = normalized

        # 5️⃣ Build domain-native result with business_categories
        # Convert dates, times, durations to include start/end spans
        dates = []
        for date_ent in raw_result.get("dates", []):
            position = date_ent.get("position", 0)
            length = date_ent.get("length", 1)
            dates.append({
                "text": date_ent.get("text", ""),
                "start": position,
                "end": position + length
            })

        dates_absolute = []
        for date_abs_ent in raw_result.get("dates_absolute", []):
            position = date_abs_ent.get("position", 0)
            length = date_abs_ent.get("length", 1)
            dates_absolute.append({
                "text": date_abs_ent.get("text", ""),
                "start": position,
                "end": position + length
            })

        times = []
        for time_ent in raw_result.get("times", []):
            position = time_ent.get("position", 0)
            length = time_ent.get("length", 1)
            times.append({
                "text": time_ent.get("text", ""),
                "start": position,
                "end": position + length
            })

        time_windows = []
        for time_window_ent in raw_result.get("time_windows", []):
            position = time_window_ent.get("position", 0)
            length = time_window_ent.get("length", 1)
            time_window_text = time_window_ent.get("text", "").lower()

            # Build time window entity with symbolic label only
            # Numeric expansion is handled by calendar binding using configurable mapping
            time_window_obj = {
                "text": time_window_ent.get("text", ""),
                "start": position,
                "end": position + length,
                "time_window": time_window_text  # Symbolic semantic field only
            }

            time_windows.append(time_window_obj)

        durations = []
        for duration_ent in raw_result.get("durations", []):
            position = duration_ent.get("position", 0)
            length = duration_ent.get("length", 1)
            durations.append({
                "text": duration_ent.get("text", ""),
                "start": position,
                "end": position + length
            })

        # Check for unresolved date/time-like language after normalization
        # If vocabulary canonicals exist but no entities were extracted, require clarification
        needs_clarification = False
        clarification = None
        if raw_result.get("_date_range_ambiguous"):
            needs_clarification = True
            clarification = Clarification(
                reason=ClarificationReason.CONFLICTING_SIGNALS,
                data={"template": "ask_end_date",
                      "error_type": "ambiguous_date_range"}
            )

        if not dates and not dates_absolute and not times and not time_windows and not needs_clarification:
            # Check if normalized text contains vocabulary canonicals that should have been extracted
            normalized_lower = normalized.lower()
            normalized_words = set(normalized_lower.split())

            # Check if any canonical from vocabularies appears in normalized text
            # but wasn't extracted as an entity
            has_unresolved_vocab = False
            for canonical in self.all_canonicals:
                if canonical in normalized_words:
                    has_unresolved_vocab = True
                    break

            if has_unresolved_vocab:
                needs_clarification = True
                clarification = Clarification(
                    reason=ClarificationReason.CONTEXT_DEPENDENT_VALUE,
                    data={"text": normalized}
                )

        result = {
            "osentence": osentence,
            "psentence": psentence,
            "business_categories": business_categories,
            "dates": dates,
            "dates_absolute": dates_absolute,
            "times": times,
            "time_windows": time_windows,
            "durations": durations,
            "normalized_from_correction": normalized_from_correction,
            "date_modifiers_vocab": self.vocabularies.get("date_modifiers", []),
            # Store parameterization info for logging (will be included in final_result)
            "_phase1_replacements": phase1_replacements,
            "_phase2_replacements": phase2_replacements,
            "_tokens": raw_result.get("_tokens", []),
        }

        if needs_clarification:
            result["needs_clarification"] = True
            result["clarification"] = clarification.to_dict()

        # 6️⃣ Domain filtering
        allowed_keys = DOMAIN_ENTITY_WHITELIST[self.domain]
        final_result = {
            k: v for k, v in result.items()
            if k in allowed_keys or k in {"osentence", "psentence", "business_categories", "date_modifiers_vocab", "_phase1_replacements", "_phase2_replacements", "_tokens"}
        }

        if debug_units:
            debug_print("[DEBUG] Domain:", self.domain)
            debug_print("[DEBUG] Final result:", final_result)

        return final_result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _maybe_expand_absolute_date_range_tail(self, raw_result: Dict[str, Any]) -> None:
        """
        Detect patterns like "oct 5th to 9th" where spaCy only emits one DATE_ABSOLUTE.
        Safe case: tail_day >= start_day -> add second absolute date (same month/year).
        Unsafe case: tail_day < start_day -> mark ambiguity for downstream clarification.
        """
        dates_abs = raw_result.get("dates_absolute") or []
        dates_rel = raw_result.get("dates") or []

        # Work on a single date reference (absolute preferred, else relative)
        source_list = None
        if len(dates_abs) == 1:
            source_list = dates_abs
        elif len(dates_abs) == 0 and len(dates_rel) == 1:
            source_list = dates_rel
        else:
            return

        tokens: List[str] = raw_result.get("_tokens") or []
        if not tokens:
            return

        start_ent = source_list[0]
        start_pos = start_ent.get("position", 0)
        start_len = start_ent.get("length", 1)
        start_end = start_pos + start_len

        # Need marker + day after the first date
        if start_end + 1 >= len(tokens):
            return

        marker = tokens[start_end].lower()
        if marker not in {"to", "-", "until", "through", "thru"}:
            return

        tail_idx = start_end + 1
        tail_token = tokens[tail_idx]
        tail_next = tokens[tail_idx + 1] if tail_idx + \
            1 < len(tokens) else None

        def _parse_day(token: str) -> Optional[int]:
            m = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?$", token)
            return int(m.group(1)) if m else None

        day_val = _parse_day(tail_token)
        day_text = None

        if day_val is not None:
            day_text = tail_token
        else:
            # Try split ordinal: number token followed by suffix
            num_val = _parse_day(tail_token)
            if num_val is not None and tail_next and tail_next.lower() in {"st", "nd", "rd", "th"}:
                day_val = num_val
                day_text = f"{tail_token} {tail_next}"
            elif num_val is not None:
                day_val = num_val
                day_text = tail_token
            elif tail_next:
                suf_val = _parse_day(tail_next)
                if suf_val is not None:
                    day_val = suf_val
                    day_text = tail_next

        if day_val is None:
            return

        start_text = start_ent.get("text", "")
        month_match = re.search(
            r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)",
            start_text,
            re.IGNORECASE,
        )
        if not month_match:
            return
        month_text = month_match.group(1)

        start_day_match = re.search(r"(\d{1,2})", start_text)
        if not start_day_match:
            return
        start_day_val = int(start_day_match.group(1))

        # Unsafe rollover (tail before start) -> mark ambiguity, do not add end date
        if day_val < start_day_val:
            raw_result["_date_range_ambiguous"] = True
            return

        end_text = f"{month_text} {day_text}".strip()

        # Compute length to cover the tail tokens
        end_length = 1
        if day_text and " " in day_text:
            end_length = 2

        # Append to whichever source list we augmented
        source_list.append({
            "text": end_text,
            "position": tail_idx,
            "length": end_length
        })
        # Also append to dates_absolute to improve absolute detection downstream
        raw_result.setdefault("dates_absolute", []).append({
            "text": end_text,
            "position": tail_idx,
            "length": end_length
        })

    def _propagate_meridiem_in_ranges(
        self, normalized: str, raw_result: Dict[str, Any], doc: Any
    ) -> Dict[str, Any]:
        """
        Propagate AM/PM from one time to another in range patterns.

        Examples:
        - "between 2pm and 5" → "between 2pm and 5pm" (add TIME entity for "5pm")
        - "between 2 and 5pm" → "between 2pm and 5pm" (add TIME entity for "2pm")

        This ensures both times become timetoken in the parameterized sentence.
        """
        tokens = [t.text for t in doc]  # Keep original case for matching
        tokens_lower = [t.lower() for t in tokens]
        normalized_lower = normalized.lower()

        # Check for range patterns: "between X and Y" or "from X to Y"
        range_pattern = re.compile(
            r'\b(between|from)\s+(\d{1,2}(?:\s*(?:am|pm))?)\s+(and|to|-)\s+(\d{1,2}(?:\s*(?:am|pm))?)\b',
            re.IGNORECASE
        )

        match = range_pattern.search(normalized_lower)
        if not match:
            return raw_result

        start_part = match.group(2).strip()  # e.g., "2pm" or "2"
        end_part = match.group(4).strip()   # e.g., "5" or "5pm"

        # Extract meridiem from parts
        start_has_meridiem = bool(
            re.search(r'\b(am|pm)\b', start_part, re.IGNORECASE))
        end_has_meridiem = bool(
            re.search(r'\b(am|pm)\b', end_part, re.IGNORECASE))

        # Only propagate if exactly one has meridiem
        if start_has_meridiem == end_has_meridiem:
            return raw_result  # Both have it or both don't - no propagation needed

        # Extract the hour number and meridiem
        start_hour_match = re.search(r'(\d{1,2})', start_part)
        end_hour_match = re.search(r'(\d{1,2})', end_part)

        if not start_hour_match or not end_hour_match:
            return raw_result

        # Determine which one needs meridiem and find its position
        # Use smart propagation: if end hour > start hour, infer PM for end (same day)
        missing_pos = None
        missing_time_text = None

        if start_has_meridiem and not end_has_meridiem:
            # Propagate from start to end: "2pm and 5" → add "5pm"
            start_meridiem = re.search(r'\b(am|pm)\b', start_part,
                                       re.IGNORECASE).group(1).lower()
            start_hour = int(start_hour_match.group(1))
            end_hour = int(end_hour_match.group(1))
            missing_hour = end_hour_match.group(1)

            # Smart propagation: if end hour < start hour, infer opposite meridiem (same-day afternoon)
            # Example: "11am and 2" → "2pm" (not "2am") because 2 < 11 suggests afternoon same day
            # Example: "2pm and 5" → "5pm" (not "5am") because 5 > 2 suggests same period
            if end_hour < start_hour:
                # End hour is lower → likely same day afternoon, so use opposite meridiem
                inferred_meridiem = "pm" if start_meridiem == "am" else "am"
            else:
                # End hour >= start hour → use same meridiem (same period)
                inferred_meridiem = start_meridiem

            missing_time_text = f"{missing_hour}{inferred_meridiem}"
            # Find position of the missing hour in tokens
            for i, token_lower in enumerate(tokens_lower):
                if token_lower == missing_hour:
                    missing_pos = i
                    break
        elif end_has_meridiem and not start_has_meridiem:
            # Propagate from end to start: "2 and 5pm" → add "2pm"
            end_meridiem = re.search(r'\b(am|pm)\b', end_part,
                                     re.IGNORECASE).group(1).lower()
            start_hour = int(start_hour_match.group(1))
            end_hour = int(end_hour_match.group(1))
            missing_hour = start_hour_match.group(1)

            # Smart propagation: if start hour < end hour, infer same meridiem for same-day range
            # Example: "2 and 5pm" → "2pm" (not "2am") because 2 < 5 suggests same period
            if start_hour < end_hour:
                # Start hour is lower → likely same day, so use same meridiem
                inferred_meridiem = end_meridiem
            else:
                # Start hour >= end hour → use opposite meridiem (could be previous day or same period)
                inferred_meridiem = "pm" if end_meridiem == "am" else "am"

            missing_time_text = f"{missing_hour}{inferred_meridiem}"
            # Find position of the missing hour in tokens
            for i, token_lower in enumerate(tokens_lower):
                if token_lower == missing_hour:
                    missing_pos = i
                    break

        if missing_pos is None or missing_time_text is None:
            return raw_result

        # Check if a TIME entity already exists at this position
        existing_times = raw_result.get("times", [])
        for time_ent in existing_times:
            if time_ent.get("position") == missing_pos:
                # Already has a TIME entity here - don't add duplicate
                return raw_result

        # Add synthetic TIME entity
        from .entity_processing import add_entity
        add_entity(raw_result, "times", missing_time_text, missing_pos, 1)

        return raw_result

    def _infer_meridiem_from_time_window(
        self, normalized: str, raw_result: Dict[str, Any], doc: Any
    ) -> Dict[str, Any]:
        """
        Infer AM/PM for times without meridiem based on time window context.

        Examples:
        - "tomorrow morning between 2 and 5" → "2am and 5am" (both become timetoken)
        - "tomorrow afternoon between 2 and 5" → "2pm and 5pm" (both become timetoken)
        - "tomorrow evening between 2 and 5" → "2pm and 5pm" (both become timetoken)
        - "tomorrow night between 2 and 5" → "2pm and 5pm" (both become timetoken)

        This ensures both times become timetoken in the parameterized sentence when
        a time window provides context for meridiem inference.
        """
        # Check if time windows are present
        time_windows = raw_result.get("time_windows", [])
        if not time_windows:
            return raw_result  # No time window context available

        # Get time window word and determine meridiem
        time_window_text = time_windows[0].get("text", "").lower()
        inferred_meridiem = None

        if time_window_text in ["morning"]:
            inferred_meridiem = "am"
        elif time_window_text in ["afternoon", "evening", "night"]:
            inferred_meridiem = "pm"
        else:
            return raw_result  # Unknown time window

        # Check for times without AM/PM in range patterns
        tokens = [t.text for t in doc]
        tokens_lower = [t.lower() for t in tokens]
        normalized_lower = normalized.lower()

        # Check for range patterns: "between X and Y" or "from X to Y"
        range_pattern = re.compile(
            r'\b(between|from)\s+(\d{1,2}(?:\s*(?:am|pm))?)\s+(and|to|-)\s+(\d{1,2}(?:\s*(?:am|pm))?)\b',
            re.IGNORECASE
        )

        match = range_pattern.search(normalized_lower)
        if not match:
            return raw_result

        start_part = match.group(2).strip()  # e.g., "2pm" or "2"
        end_part = match.group(4).strip()   # e.g., "5" or "5pm"

        # Extract hour numbers
        start_hour_match = re.search(r'(\d{1,2})', start_part)
        end_hour_match = re.search(r'(\d{1,2})', end_part)

        if not start_hour_match or not end_hour_match:
            return raw_result

        # Check if times have AM/PM
        start_has_meridiem = bool(
            re.search(r'\b(am|pm)\b', start_part, re.IGNORECASE))
        end_has_meridiem = bool(
            re.search(r'\b(am|pm)\b', end_part, re.IGNORECASE))

        # Infer meridiem for times that don't have it
        from .entity_processing import add_entity

        if not start_has_meridiem:
            start_hour = start_hour_match.group(1)
            start_time_text = f"{start_hour}{inferred_meridiem}"
            # Find position of start hour in tokens
            for i, token_lower in enumerate(tokens_lower):
                if token_lower == start_hour:
                    # Check if TIME entity already exists at this position
                    existing_times = raw_result.get("times", [])
                    if not any(t.get("position") == i for t in existing_times):
                        add_entity(raw_result, "times", start_time_text, i, 1)
                    break

        if not end_has_meridiem:
            end_hour = end_hour_match.group(1)
            end_time_text = f"{end_hour}{inferred_meridiem}"
            # Find position of end hour in tokens
            for i, token_lower in enumerate(tokens_lower):
                if token_lower == end_hour:
                    # Check if TIME entity already exists at this position
                    existing_times = raw_result.get("times", [])
                    if not any(t.get("position") == i for t in existing_times):
                        add_entity(raw_result, "times", end_time_text, i, 1)
                    break

        return raw_result

    def _remove_noise_from_psentence(self, psentence: str) -> str:
        """
        Remove noise tokens from parameterized sentence.

        This is a lightweight post-processing step that removes ignorable
        tokens (like "me", "in", "for", "please") from the parameterized
        sentence while preserving sentence structure and important words
        (verbs, prepositions relevant for intent detection).

        Handles both single-word and multi-word noise phrases.

        Example:
            "book me in for servicetoken datetoken by timetoken"
            → "book servicetoken datetoken by timetoken"
        """
        if not self.noise_set:
            return psentence

        tokens = psentence.split()
        filtered_tokens = []
        i = 0

        while i < len(tokens):
            matched = False

            # Try matching multi-word phrases first (longest first)
            for noise_phrase in sorted(self.noise_set, key=lambda x: -len(x.split())):
                noise_words = noise_phrase.split()
                if len(noise_words) > 1:
                    # Check if this multi-word phrase matches starting at position i
                    if (i + len(noise_words) <= len(tokens) and
                            " ".join(tokens[i:i+len(noise_words)]).lower() == noise_phrase.lower()):
                        # Skip this noise phrase
                        i += len(noise_words)
                        matched = True
                        break

            if not matched:
                # Check single-word noise
                if tokens[i].lower() not in self.noise_set:
                    filtered_tokens.append(tokens[i])
                i += 1

        # Collapse multiple spaces
        result = " ".join(filtered_tokens)
        result = " ".join(result.split())  # Normalize whitespace

        return result
