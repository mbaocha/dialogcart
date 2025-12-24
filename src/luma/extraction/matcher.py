"""
Stage 1: Entity Extraction & Parameterization

Service and reservation entity matcher for DialogCart.

Extracts and parameterizes entities for service-based appointment booking
and reservation systems.
"""
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
import re

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

from .normalization import (
    normalize_hyphens,
    pre_normalization,
    normalize_orthography,
    normalize_natural_language_variants,
)

from .vocabulary_normalization import (
    load_vocabularies,
    compile_vocabulary_maps,
    normalize_vocabularies,
    validate_vocabularies,
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

from .entity_processing import (
    extract_entities_from_doc,
    build_parameterized_sentence,
)


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
                    "canonical": canonical,
                }
            )
            used_ranges.append((start_char, end_char))

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

        # Protect alias spans from being collapsed to generic service families
        if alias_spans:
            raw_result = merge_alias_spans_into_services(
                raw_result, doc, alias_spans)

        # Detect range tails like "oct 5th to 9th" where only the first absolute
        # date is extracted. If safe, synthesize a second absolute date using the
        # same month/year. If unsafe (tail < start), mark ambiguity for downstream
        # clarification.
        self._maybe_expand_absolute_date_range_tail(raw_result)

        # 3️⃣ Map SERVICE_FAMILY entities to canonical business category IDs
        business_categories = []
        if "services" in raw_result:
            # spaCy extracts SERVICE_FAMILY entities as "services" in raw_result
            for entity in raw_result["services"]:
                entity_text = entity.get("text", "").lower()
                # Map to canonical business category ID (e.g., "beauty_and_wellness.haircut")
                canonical_id = entity.get(
                    "canonical") or self.business_category_map.get(entity_text)

                # Convert position/length to start/end token indices
                position = entity.get("position", 0)
                length = entity.get("length", 1)
                start = position
                end = position + length

                if canonical_id:
                    business_categories.append({
                        "text": entity_text,
                        "canonical": canonical_id,
                        "start": start,
                        "end": end
                    })
                else:
                    # Fallback: use original text if mapping not found
                    business_categories.append({
                        "text": entity_text,
                        "canonical": None,
                        "start": start,
                        "end": end
                    })

        # 4️⃣ Build parameterized sentence
        # Phase 1: Service/tenant alias replacement (already done in normalization)
        # Track what was replaced for logging
        phase1_replacements = []
        for entity in raw_result.get("services", []):
            entity_text = entity.get("text", "").lower()
            canonical_id = self.business_category_map.get(entity_text)
            if canonical_id:
                phase1_replacements.append({
                    "span": entity_text,
                    "rule": "global_service_family",
                    "replaced_with": "servicefamilytoken"
                })

        psentence, phase2_replacements = build_parameterized_sentence(
            doc, raw_result)
        # Noise is preserved in psentence (not removed)

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
