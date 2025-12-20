"""
Stage 1: Entity Extraction & Parameterization

Service and reservation entity matcher for DialogCart.

Extracts and parameterizes entities for service-based appointment booking
and reservation systems.
"""
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
import re

from luma.config import debug_print

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
    init_nlp_with_service_families,
    load_global_noise_set,
    load_global_orthography_rules,
    load_global_service_families,
    build_service_family_synonym_map,
    load_global_entity_types,
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
        "service_families", "dates", "dates_absolute", "times", "time_windows", "durations"
    },
    "reservation": {
        "service_families", "dates", "dates_absolute", "times", "time_windows", "durations"
    }
}


def _build_natural_language_variant_map_from_service_families(
    service_families: Dict[str, Dict[str, Any]]
) -> Dict[str, str]:
    """
    Build natural language variant map from service families.

    Maps service family synonyms to a preferred natural language form (first synonym).
    This map is used to normalize variants like "hair cut" → "haircut".

    CRITICAL: This map MUST NOT contain canonical IDs (e.g., "beauty_and_wellness.haircut").
    It only maps natural language variants to other natural language forms.

    Example:
        service_families: {
            "beauty_and_wellness": {
                "haircut": {"synonym": ["haircut", "hair trim"]}
            }
        }
        Maps: "haircut" → "haircut", "hair trim" → "haircut"
        (Uses first synonym as preferred form)
    """
    variant_map = {}

    for _category, families in service_families.items():
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

        # Find global JSON file (required for service families)
        entity_path = Path(entity_file).resolve() if entity_file else None
        if entity_path:
            # Look for global.v2.json in same directory
            global_json_path = entity_path.parent / "global.v2.json"
        else:
            # Try to find global.v2.json in standard location
            # This is a fallback - ideally entity_file should point to a file in the normalization directory
            # From matcher.py: parent = extraction/, parent.parent = luma/, so luma/store/normalization/
            script_dir = Path(__file__).parent
            global_json_path = script_dir.parent / \
                "store" / "normalization" / "global.v2.json"
            if not global_json_path.exists():
                global_json_path = None

        if not global_json_path or not global_json_path.exists():
            raise ValueError(
                "global.v2.json not found. Please provide entity_file pointing to a file in the normalization directory.")

        # Load global service families (GLOBAL semantic concepts)
        self.service_families = load_global_service_families(global_json_path)
        debug_print(
            "[EntityMatcher] Loaded service families from global JSON 2")

        # Build natural language variant map from service families
        # This maps variants to preferred natural language forms (NOT canonical IDs)
        self.variant_map = _build_natural_language_variant_map_from_service_families(
            self.service_families)

        # Build service family synonym map (for canonicalization)
        self.service_family_map = build_service_family_synonym_map(
            self.service_families)

        # Load global normalization (orthography, noise, vocabularies)
        self.noise_set = load_global_noise_set(global_json_path)
        self.orthography_rules = load_global_orthography_rules(
            global_json_path)

        # Load vocabularies with synonyms and typos
        vocabularies = load_vocabularies(global_json_path)
        self.vocabularies = vocabularies
        entity_types = load_global_entity_types(global_json_path)
        service_families = load_global_service_families(global_json_path)

        # Validate vocabularies
        validate_vocabularies(vocabularies, entity_types, service_families)

        # Compile vocabulary maps
        self.synonym_map, self.typo_map, self.all_canonicals = compile_vocabulary_maps(
            vocabularies)

        # Load global entity types (date, time, duration)
        self.entity_types = load_global_entity_types(global_json_path)

        # Tenant entities are unused (kept for backward compatibility)
        self.entities = []
        self.service_map = {}  # Empty - service families use service_family_map instead

        # spaCy init with service families
        self.nlp = None
        if not lazy_load_spacy:
            self.nlp, _ = init_nlp_with_service_families(global_json_path)

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

        # Protect alias spans from being collapsed to generic service families
        if alias_spans:
            raw_result = merge_alias_spans_into_services(
                raw_result, doc, alias_spans)

        # 3️⃣ Map SERVICE_FAMILY entities to canonical service family IDs
        service_families = []
        if "services" in raw_result:
            # spaCy extracts SERVICE_FAMILY entities as "services" in raw_result
            for entity in raw_result["services"]:
                entity_text = entity.get("text", "").lower()
                # Map to canonical service family ID (e.g., "beauty_and_wellness.haircut")
                canonical_id = entity.get("canonical") or self.service_family_map.get(entity_text)

                # Convert position/length to start/end token indices
                position = entity.get("position", 0)
                length = entity.get("length", 1)
                start = position
                end = position + length

                if canonical_id:
                    service_families.append({
                        "text": entity_text,
                        "canonical": canonical_id,
                        "start": start,
                        "end": end
                    })
                else:
                    # Fallback: use original text if mapping not found
                    service_families.append({
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
            canonical_id = self.service_family_map.get(entity_text)
            if canonical_id:
                phase1_replacements.append({
                    "span": entity_text,
                    "rule": "global_service_family",
                    "replaced_with": "servicefamilytoken"
                })
        
        psentence, phase2_replacements = build_parameterized_sentence(doc, raw_result)
        # Noise is preserved in psentence (not removed)

        osentence = normalized

        # 5️⃣ Build domain-native result with service_families
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
        if not dates and not dates_absolute and not times and not time_windows:
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
                from ..clarification import Clarification, ClarificationReason
                needs_clarification = True
                clarification = Clarification(
                    reason=ClarificationReason.CONTEXT_DEPENDENT_VALUE,
                    data={"text": normalized}
                )

        result = {
            "osentence": osentence,
            "psentence": psentence,
            "service_families": service_families,
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
            if k in allowed_keys or k in {"osentence", "psentence", "service_families", "date_modifiers_vocab", "_phase1_replacements", "_phase2_replacements", "_tokens"}
        }

        if debug_units:
            debug_print("[DEBUG] Domain:", self.domain)
            debug_print("[DEBUG] Final result:", final_result)

        return final_result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
