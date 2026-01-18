"""
Semantic Resolver

Resolves semantic meaning from extracted entities and grouped intents.
Decides what the user means without binding to actual calendar dates.

This layer answers: "What does the user mean?"
NOT: "What actual dates does this correspond to?"
"""
# Provenance marker to verify loaded module version
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Set, List
from ..extraction.vocabulary_normalization import (
    load_vocabularies,
    compile_vocabulary_maps,
    normalize_vocabularies,
)
from ..extraction.entity_loading import (
    load_global_vocabularies,
    load_global_entity_types,
)
from ..clarification import Clarification, ClarificationReason
from ..config import debug_print
from ..config.temporal import (
    ALLOW_BARE_WEEKDAY_BINDING,
    ALLOW_BARE_WEEKDAY_RANGE_BINDING,
    APPOINTMENT_TEMPORAL_TYPE,
    DateMode,
    RESERVATION_TEMPORAL_TYPE,
    TimeMode,
)
from ..config.temporal_rules import TEMPORAL_RULES
from ..config.intent_meta import get_intent_registry
import logging
import re

logger = logging.getLogger(__name__)


def _get_global_config_path() -> Path:
    """Get path to global normalization config JSON."""
    from ..extraction.entity_loading import get_global_json_path
    # Use standard location based on configured version
    return get_global_json_path()


# Lazy-loaded config (loaded on first use)
_VOCAB_CACHE: Dict[str, Any] = {}
_VOCAB_LOADED: bool = False  # Flag to track if vocabularies are loaded
# (synonym_map, typo_map, all_canonicals)
_VOCAB_MAPS_CACHE: Tuple[Dict[str, str], Dict[str, str], Set[str]] = None
_ENTITY_TYPES_CACHE: Dict[str, Any] = {}
_ENTITY_TYPES_LOADED: bool = False  # Flag to track if entity types are loaded

# Performance tracking
_VOCAB_CALL_COUNT: int = 0
_VOCAB_CACHE_HITS: int = 0


def _load_vocabularies() -> Dict[str, Any]:
    """
    Load and cache vocabularies from JSON.

    Uses a flag-based cache check for better performance and reliability.
    Tracks cache hits/misses for performance monitoring.
    """
    global _VOCAB_CACHE, _VOCAB_LOADED, _VOCAB_CALL_COUNT, _VOCAB_CACHE_HITS

    _VOCAB_CALL_COUNT += 1

    if not _VOCAB_LOADED:
        # Cache miss - load vocabularies
        config_path = _get_global_config_path()
        _VOCAB_CACHE.update(load_global_vocabularies(config_path))
        _VOCAB_LOADED = True
        logger.debug(
            f"[vocab_cache] Loaded vocabularies (call #{_VOCAB_CALL_COUNT})",
            extra={"cache_hit": False, "total_calls": _VOCAB_CALL_COUNT}
        )
    else:
        # Cache hit
        _VOCAB_CACHE_HITS += 1
        if _VOCAB_CALL_COUNT % 50 == 0:  # Log every 50th call to avoid spam
            logger.debug(
                f"[vocab_cache] Cache hit (call #{_VOCAB_CALL_COUNT}, "
                f"hits: {_VOCAB_CACHE_HITS}, miss rate: "
                f"{((_VOCAB_CALL_COUNT - _VOCAB_CACHE_HITS) / _VOCAB_CALL_COUNT * 100):.1f}%)",
                extra={
                    "cache_hit": True,
                    "total_calls": _VOCAB_CALL_COUNT,
                    "cache_hits": _VOCAB_CACHE_HITS
                }
            )

    return _VOCAB_CACHE


def _load_entity_types() -> Dict[str, Any]:
    """Load and cache entity_types from JSON."""
    global _ENTITY_TYPES_CACHE, _ENTITY_TYPES_LOADED

    if not _ENTITY_TYPES_LOADED:
        config_path = _get_global_config_path()
        _ENTITY_TYPES_CACHE.update(load_global_entity_types(config_path))
        _ENTITY_TYPES_LOADED = True
    return _ENTITY_TYPES_CACHE


def _load_vocabulary_maps() -> Tuple[Dict[str, str], Dict[str, str], Set[str]]:
    """Load and cache vocabulary maps (synonyms and typos) from JSON."""
    global _VOCAB_MAPS_CACHE
    if _VOCAB_MAPS_CACHE is None:
        config_path = _get_global_config_path()
        vocabularies = load_vocabularies(config_path)
        _VOCAB_MAPS_CACHE = compile_vocabulary_maps(vocabularies)
    return _VOCAB_MAPS_CACHE


def initialize_vocabularies(force_reload: bool = False) -> None:
    """
    Pre-load vocabularies and entity types at startup.

    This function should be called during pipeline initialization to avoid
    first-request latency and ensure vocabularies are loaded once.

    Args:
        force_reload: If True, reload vocabularies even if already loaded
    """
    global _VOCAB_CACHE, _VOCAB_LOADED, _ENTITY_TYPES_CACHE, _ENTITY_TYPES_LOADED

    if force_reload:
        _VOCAB_CACHE.clear()
        _ENTITY_TYPES_CACHE.clear()
        _VOCAB_LOADED = False
        _ENTITY_TYPES_LOADED = False

    # Pre-load vocabularies
    if not _VOCAB_LOADED:
        logger.info("[vocab_cache] Pre-loading vocabularies at startup")
        _load_vocabularies()
        logger.info(
            f"[vocab_cache] Vocabularies loaded: {len(_VOCAB_CACHE)} keys")

    # Pre-load entity types
    if not _ENTITY_TYPES_LOADED:
        logger.info("[vocab_cache] Pre-loading entity types at startup")
        _load_entity_types()
        logger.info(
            f"[vocab_cache] Entity types loaded: {len(_ENTITY_TYPES_CACHE)} keys")

    # Pre-load vocabulary maps
    if _VOCAB_MAPS_CACHE is None:
        logger.info("[vocab_cache] Pre-loading vocabulary maps at startup")
        _load_vocabulary_maps()
        logger.info("[vocab_cache] Vocabulary maps loaded")


def get_vocab_cache_stats() -> Dict[str, Any]:
    """
    Get vocabulary cache statistics for performance monitoring.

    Returns:
        Dictionary with cache statistics
    """
    return {
        "vocab_loaded": _VOCAB_LOADED,
        "entity_types_loaded": _ENTITY_TYPES_LOADED,
        "vocab_maps_loaded": _VOCAB_MAPS_CACHE is not None,
        "vocab_call_count": _VOCAB_CALL_COUNT,
        "vocab_cache_hits": _VOCAB_CACHE_HITS,
        "vocab_cache_misses": _VOCAB_CALL_COUNT - _VOCAB_CACHE_HITS,
        "cache_hit_rate": (_VOCAB_CACHE_HITS / _VOCAB_CALL_COUNT * 100) if _VOCAB_CALL_COUNT > 0 else 0.0
    }


@dataclass
class SemanticResolutionResult:
    """
    Semantic resolution result.

    Contains resolved booking semantics without calendar binding.
    """
    resolved_booking: Dict[str, Any]
    needs_clarification: bool = False
    clarification: Optional[Clarification] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            "resolved_booking": self.resolved_booking,
            "needs_clarification": self.needs_clarification,
        }
        if self.clarification is not None:
            result["clarification"] = self.clarification.to_dict()
        else:
            result["clarification"] = None
        return result


def _normalize_canonical_to_full(canonical: str, booking_mode: str = "service") -> str:
    """
    Normalize short canonical form to full canonical form.

    Args:
        canonical: Canonical form (short like "haircut" or full like "beauty_and_wellness.haircut")
        booking_mode: Booking mode ("service" or "reservation") to determine domain prefix

    Returns:
        Full canonical form (e.g., "beauty_and_wellness.haircut" or "hospitality.room")
    """
    if "." in canonical:
        # Already full canonical form
        return canonical

    # Short form - add domain prefix based on booking_mode
    if booking_mode == "reservation":
        return f"hospitality.{canonical}"
    else:
        # Default to service domain
        return f"beauty_and_wellness.{canonical}"


def _build_variants_by_family(tenant_context: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """
    Build variants_by_family dictionary from tenant_context.aliases.
    Normalizes short canonical forms to full canonical forms for consistent matching.

    Args:
        tenant_context: Optional tenant context with aliases mapping
                       Contains: 
                       - aliases (Dict[str, str] mapping alias -> service_family)
                       - booking_mode (str, optional: "service" or "reservation")

    Returns:
        Dictionary mapping service_family (full canonical) -> list of tenant alias strings
    """
    variants_by_family: Dict[str, List[str]] = {}

    if not tenant_context:
        logger.debug("[semantic] no tenant_context provided")
        return variants_by_family

    aliases = tenant_context.get("aliases", {})
    if not isinstance(aliases, dict):
        logger.debug(f"[semantic] aliases is not a dict: {type(aliases)}")
        return variants_by_family

    # Get booking_mode for canonical normalization
    booking_mode = tenant_context.get("booking_mode", "service")
    if not isinstance(booking_mode, str):
        booking_mode = "service"

    logger.info(
        f"[semantic] building variants_by_family from {len(aliases)} aliases (booking_mode: {booking_mode})")

    # Build reverse mapping: service_family -> list of aliases
    # Normalize service_family to full canonical form for consistent matching
    for alias, service_family in aliases.items():
        if not isinstance(alias, str) or not isinstance(service_family, str):
            continue

        # Normalize service_family to full canonical form
        normalized_family = _normalize_canonical_to_full(
            service_family, booking_mode)

        if normalized_family not in variants_by_family:
            variants_by_family[normalized_family] = []
        variants_by_family[normalized_family].append(alias)
        logger.debug(
            f"[semantic] mapped alias '{alias}' -> service_family '{service_family}' -> normalized '{normalized_family}'")

    # Sort aliases for deterministic output
    for service_family in variants_by_family:
        variants_by_family[service_family].sort()

    logger.info(f"[semantic] built variants_by_family: {variants_by_family}")
    return variants_by_family


def _track_explicit_alias_match(
    services: List[Dict[str, Any]],
    entities: Dict[str, Any],
    variants_by_family: Dict[str, List[str]],
    booking_mode: str = "service"
) -> None:
    """
    Track tenant alias matches and store in service dicts.

    Rules:
    - If exactly ONE alias exists for canonical → always use it (even if not explicitly mentioned)
    - If MULTIPLE aliases exist → only use if explicitly matched in input

    When a tenant alias is matched (explicitly or by default), store it in the service
    dict so it can be preserved in the final output.

    Args:
        services: List of service dictionaries (modified in place)
        entities: Raw extraction output containing osentence
        variants_by_family: Dictionary mapping service_family (full canonical) -> list of tenant alias strings
        booking_mode: Booking mode ("service" or "reservation") for canonical normalization
    """
    if not variants_by_family or not services:
        return

    osentence = entities.get("osentence", "").lower()
    if not osentence:
        return

    # For each service, check alias matching
    for service in services:
        if not isinstance(service, dict):
            continue

        canonical = service.get("canonical")
        if not canonical or not isinstance(canonical, str):
            continue

        # Normalize canonical to full form for matching (variants_by_family uses full canonicals)
        normalized_canonical = _normalize_canonical_to_full(
            canonical, booking_mode)

        variants = variants_by_family.get(normalized_canonical, [])
        if not variants:
            continue

        # Check if any variant (tenant alias) appears in the original sentence
        matched_alias = None
        for variant in variants:
            variant_lower = variant.lower()
            # Check if variant appears as a phrase in the original sentence
            pattern = r'\b' + re.escape(variant_lower) + r'\b'
            if re.search(pattern, osentence):
                matched_alias = variant  # Store original case-preserved alias
                logger.info(
                    f"[semantic] explicit alias match: '{matched_alias}' → {normalized_canonical}")
                break

        # NEW: If no explicit match but exactly one alias exists, use it by default
        if not matched_alias and len(variants) == 1:
            matched_alias = variants[0]  # Use the single alias
            logger.info(
                f"[semantic] single alias default: '{matched_alias}' → {normalized_canonical} (not explicitly mentioned)")

        # Store matched alias in service dict for later use
        if matched_alias:
            service["resolved_alias"] = matched_alias


def _check_service_variant_ambiguity(
    services: List[Dict[str, Any]],
    entities: Dict[str, Any],
    variants_by_family: Dict[str, List[str]],
    booking_mode: str = "service"
) -> Optional[Clarification]:
    """
    Check for service variant ambiguity when a service family maps to multiple tenant variants.

    Ambiguity exists if:
    - service family is resolved
    - tenant_context contains >1 alias mapping to that family
    - user input did NOT explicitly match one alias

    Args:
        services: List of resolved service dictionaries from booking
                  Each service dict has "text" (natural language) and "canonical" (service family ID)
        entities: Raw extraction output containing business_categories
        variants_by_family: Dictionary mapping service_family -> list of tenant alias strings

    Returns:
        Clarification object if ambiguity detected, None otherwise
    """
    if not variants_by_family:
        logger.debug(
            "[semantic] no variants_by_family, skipping service variant ambiguity check")
        return None

    if not services:
        logger.debug(
            "[semantic] no services, skipping service variant ambiguity check")
        return None

    logger.info(
        f"[semantic] checking service variant ambiguity: {len(services)} services, {len(variants_by_family)} families with variants")

    # Get service families from resolved services and normalize to full canonical form
    # Services are dicts with "canonical" field containing service family ID (e.g., "beauty_and_wellness.haircut" or "haircut")
    service_families = []
    for service in services:
        if isinstance(service, dict):
            # Check canonical field first (primary source of service family ID)
            canonical = service.get("canonical")
            if canonical and isinstance(canonical, str):
                # Normalize to full canonical form for matching (variants_by_family uses full canonicals)
                normalized_canonical = _normalize_canonical_to_full(
                    canonical, booking_mode)
                service_families.append(normalized_canonical)
                logger.debug(
                    f"[semantic] found service family from canonical: {canonical} -> normalized: {normalized_canonical}")
            # Fallback: check if text field contains canonical format
            elif service.get("text") and "." in str(service.get("text", "")):
                text_canonical = str(service.get("text", ""))
                normalized_text_canonical = _normalize_canonical_to_full(
                    text_canonical, booking_mode)
                service_families.append(normalized_text_canonical)
                logger.debug(
                    f"[semantic] found service family from text: {service.get('text')} -> normalized: {normalized_text_canonical}")

    if not service_families:
        logger.debug("[semantic] no service families extracted from services")
        return None

    # Check each resolved service family for ambiguity
    for service_family in service_families:
        variants = variants_by_family.get(service_family, [])
        logger.info(
            f"[semantic] service_family={service_family}, variants={variants}, count={len(variants)}")

        if len(variants) <= 1:
            logger.debug(
                f"[semantic] skipping {service_family}: {len(variants)} variants (no ambiguity)")
            continue  # No ambiguity if 0 or 1 variant

        # Check if resolved_alias was already set by _track_explicit_alias_match
        # This is more reliable than re-checking the sentence
        explicit_alias_match = False
        for service in services:
            if isinstance(service, dict):
                resolved_alias = service.get("resolved_alias")
                if resolved_alias and resolved_alias in variants:
                    # Explicit alias match already found by _track_explicit_alias_match - no ambiguity
                    logger.info(
                        f"[semantic] resolved_alias already set to '{resolved_alias}' for service_family '{service_family}' - skipping ambiguity check"
                    )
                    explicit_alias_match = True
                    break

        # If resolved_alias wasn't found, check the sentence directly
        if not explicit_alias_match:
            # Check if user input explicitly matched one of the aliases
            # Check the original sentence to see if any variant (tenant alias) appears in it
            osentence = entities.get("osentence", "").lower()
            logger.info(
                f"[semantic] checking osentence for explicit alias match: '{osentence}'")

            if osentence:
                # Check if any variant (tenant alias) appears in the original sentence
                for variant in variants:
                    variant_lower = variant.lower()
                    # Check if variant appears as a phrase in the original sentence
                    # Use word boundary matching to avoid false positives
                    # Pattern: variant as a phrase (with word boundaries)
                    pattern = r'\b' + re.escape(variant_lower) + r'\b'
                    if re.search(pattern, osentence):
                        explicit_alias_match = True
                        logger.info(
                            f"[semantic] explicit alias match found: '{variant_lower}' in '{osentence}'")
                        break
                    else:
                        logger.debug(
                            f"[semantic] no match for variant '{variant_lower}' in '{osentence}'")

        # If no explicit alias match, ambiguity exists
        if not explicit_alias_match:
            logger.info(
                f"[semantic] detected service variant ambiguity: {service_family} → {variants}"
            )
            return Clarification(
                reason=ClarificationReason.MULTIPLE_MATCHES,
                data={
                    "options": variants
                    # service_family removed - redundant with context.services[0].canonical
                }
            )

    return None


def _validate_temporal_shape_completeness(
    intent_name: Optional[str],
    resolved_booking: Dict[str, Any],
    date_resolution: Dict[str, Any],
    entities: Optional[Dict[str, Any]] = None,
    memory_state: Optional[Dict[str, Any]] = None
) -> Optional[Clarification]:
    """
    Validate that resolved booking satisfies temporal shape requirements from config.
    
    Also normalizes unanchored weekday ranges for CREATE_RESERVATION to ensure
    both start_date and end_date are marked as missing.

    Returns:
        Clarification if temporal shape incomplete, None if complete.
    """
    if not intent_name:
        return None

    # Get temporal shape from IntentRegistry (sole policy source)
    registry = get_intent_registry()
    intent_meta = registry.get(intent_name)
    temporal_shape = intent_meta.temporal_shape if intent_meta else None

    if not temporal_shape:
        # No temporal shape requirement for this intent
        return None

    date_mode = resolved_booking.get("date_mode")
    time_mode = resolved_booking.get("time_mode")
    date_refs = resolved_booking.get("date_refs", [])
    time_constraint = resolved_booking.get("time_constraint")

    # Normalize unanchored weekday ranges for CREATE_RESERVATION
    # This must happen BEFORE temporal shape validation to ensure both dates are marked missing
    if temporal_shape == RESERVATION_TEMPORAL_TYPE and entities:
        # Check entities directly (not date_refs) since date_refs may be empty if already blocked
        dates = entities.get("dates", [])
        if len(dates) >= 2:
            # Extract date texts from entities (original extraction, before normalization/blocking)
            date_texts = [d.get("text", "") for d in dates[:2]]
            # Check if this is an unanchored weekday-only range
            if _is_weekday_only_range(date_texts, DateMode.RANGE.value, entities, memory_state):
                # Normalize: treat as fully unresolved, mark both dates as missing
                # This overrides any partial binding that may have occurred
                return Clarification(
                    reason=ClarificationReason.MISSING_DATE_RANGE,
                    data={
                        "missing_slots": ["start_date", "end_date"]
                    }
                )

    if temporal_shape == APPOINTMENT_TEMPORAL_TYPE:
        # Require date_mode != None and time_mode in {"exact", "range", "window"}
        # Also accept fuzzy time_constraint (will be bound via FUZZY_TIME_WINDOWS)
        has_valid_date = date_mode is not None and date_mode != DateMode.FLEXIBLE.value and len(
            date_refs) > 0
        has_valid_time = (
            time_mode in {TimeMode.EXACT.value,
                          TimeMode.RANGE.value, TimeMode.WINDOW.value}
            or (time_constraint and time_constraint.get("mode") in {TimeMode.EXACT.value, TimeMode.WINDOW.value, TimeMode.FUZZY.value})
        )

        missing_slots = []
        if not has_valid_date:
            missing_slots.append("date")
        if not has_valid_time:
            missing_slots.append("time")

        if missing_slots:
            return Clarification(
                reason=ClarificationReason.MISSING_TIME if "time" in missing_slots else ClarificationReason.MISSING_DATE,
                data={
                    "missing_slots": missing_slots
                }
            )

    elif temporal_shape == RESERVATION_TEMPORAL_TYPE:
        # Require start_date AND end_date (two date_refs or date_mode == range)
        # Note: Unanchored weekday ranges are already handled above
        has_start = len(date_refs) >= 1 or date_mode == DateMode.RANGE.value
        has_end = (
            len(date_refs) >= 2
            or date_mode == DateMode.RANGE.value
            or (date_resolution.get("mode") == DateMode.RANGE.value and len(date_refs) >= 2)
        )

        if not has_start:
            return Clarification(
                reason=ClarificationReason.MISSING_DATE,
                data={
                    "missing_slots": ["start_date"]
                }
            )
        if not has_end:
            return Clarification(
                reason=ClarificationReason.MISSING_DATE,
                data={
                    "missing_slots": ["end_date"]
                }
            )

    return None


def resolve_semantics(
    intent_result: Dict[str, Any],
    entities: Dict[str, Any],
    tenant_context: Optional[Dict[str, Any]] = None
) -> Tuple[SemanticResolutionResult, Dict[str, Any]]:
    """
    Resolve semantic meaning from intent result and entities.

    This function decides what the user means (exact time vs window,
    single date vs range, etc.) without binding to actual calendar dates.

    Args:
        intent_result: Result from appointment_grouper.group_appointment()
                     Contains: intent, booking, structure, status, reason
        entities: Raw extraction output from EntityMatcher
                 Contains: service_families, dates, dates_absolute, times,
                          time_windows, durations
        tenant_context: Optional tenant context with aliases mapping
                       Contains: aliases (Dict[str, str] mapping alias -> service_family)

    Returns:
        SemanticResolutionResult with resolved booking semantics

    Resolution Rules (ordered):
    1. Time precedence: exact > range > window > none
    2. Date precedence: absolute > relative
    3. Window + exact time: both preserved (e.g., "tomorrow morning at 9am")
    4. Range semantics: between X and Y → flexible range
    5. Duration: applies to entire booking unless structure says otherwise
    6. Ambiguity: detect conflicts and set needs_clarification
    """
    booking = intent_result.get("booking", {})
    structure = intent_result.get("structure", {})

    # Extract services
    services = booking.get("services", [])

    # Detect time constraints BEFORE time resolution (to exclude them from binding)
    booking_time_constraint = booking.get("time_constraint")
    time_constraint = booking_time_constraint or _detect_time_constraint(
        entities)

    # Filter out constraint times from entities for regular time resolution
    filtered_entities = _filter_constraint_times(entities, time_constraint)

    # Resolve time semantics (excluding constraint times)
    time_resolution, time_extraction_trace, time_issues = _resolve_time_semantics(
        filtered_entities, structure, entities)

    # Store time_extraction_trace for later use in trace
    if not time_constraint:
        inferred_constraint = _build_time_constraint_from_resolution(
            time_resolution)
        if inferred_constraint:
            time_constraint = inferred_constraint

    # Resolve date semantics
    date_resolution = _resolve_date_semantics(
        entities, structure, intent_result.get("intent"))

    # Guard: modifier + relative date is invalid (e.g., "next tomorrow")
    date_refs = date_resolution.get("refs", [])
    date_modifiers = date_resolution.get("modifiers", []) or []
    if date_refs and date_modifiers:
        entity_types = _load_entity_types()
        relative_defs = entity_types.get("date", {}).get("relative", [])
        relative_values = {
            rd.get("value", "").lower()
            for rd in relative_defs
            if isinstance(rd, dict) and rd.get("value")
        }
        first_ref = str(date_refs[0]).lower()
        first_mod = str(date_modifiers[0]).lower()
        if first_ref in relative_values:
            clarification = Clarification(
                reason=ClarificationReason.CONFLICTING_SIGNALS,
                data={
                    "modifier": first_mod,
                    "date": first_ref,
                },
            )
            # Extract date_roles from date_resolution
            date_roles = date_resolution.get("date_roles", [])
            resolved_booking = {
                "services": booking.get("services", []),
                "date_mode": date_resolution["mode"],
                "date_refs": date_resolution["refs"],
                "date_modifiers": date_modifiers,
                "date_roles": date_roles,  # Persist date_roles for reservations
                "time_mode": time_resolution["mode"],
                "time_refs": time_resolution["refs"],
                "duration": booking.get("duration"),
            }
            result = SemanticResolutionResult(
                resolved_booking=resolved_booking,
                needs_clarification=True,
                clarification=clarification,
            )
            trace = {
                "semantic": {
                    "service_ids": [s.get("text", "") if isinstance(s, dict) else str(s) for s in booking.get("services", [])],
                    "date_mode": resolved_booking.get("date_mode", "none"),
                    "time_mode": resolved_booking.get("time_mode", "none"),
                    "time_refs": resolved_booking.get("time_refs", []),
                    "time_constraint": None,
                    "needs_clarification": True,
                    "clarification_reason": clarification.reason.value if clarification else None,
                    "time_extraction_trace": time_extraction_trace
                }
            }
            return result, trace

    # Extract duration
    duration = booking.get("duration")

    # Build variants_by_family from tenant_context (if provided)
    variants_by_family = _build_variants_by_family(tenant_context)
    # Removed per-stage logging - trace data returned instead

    # Get booking_mode for canonical normalization
    booking_mode = tenant_context.get(
        "booking_mode", "service") if tenant_context else "service"

    # Track explicit alias matches (before ambiguity check)
    # This stores matched aliases in service dicts for later use
    _track_explicit_alias_match(
        services, entities, variants_by_family, booking_mode)

    # Check for service variant ambiguity (before other ambiguity checks)
    clarification = _check_service_variant_ambiguity(
        services, entities, variants_by_family, booking_mode
    )

    # Check for conflicts and ambiguity (only if no service variant ambiguity)
    if clarification is None:
        clarification = _check_ambiguity(
            entities, structure, time_resolution, date_resolution
        )

    # Guard: Check for weekday-like patterns that weren't normalized
    # This prevents silent failure when normalization doesn't succeed
    if clarification is None:
        clarification = _check_unresolved_weekday_patterns(
            intent_result, date_resolution, entities
        )

    # Extract date_roles from date_resolution
    date_roles = date_resolution.get("date_roles", [])
    logger.info(
        f"[date_role] Extracted date_roles from date_resolution: {date_roles}, "
        f"date_resolution_keys={list(date_resolution.keys())}",
        extra={'intent': intent_result.get(
            "intent") if intent_result else None}
    )
    resolved_booking = {
        "services": services,
        "date_mode": date_resolution["mode"],
        "date_refs": date_resolution["refs"],
        "date_modifiers": date_resolution.get("modifiers", []),
        "date_roles": date_roles,  # Persist date_roles for reservations
        "time_mode": time_resolution["mode"],
        "time_refs": time_resolution["refs"],
        "duration": duration,
        "time_constraint": time_constraint,
        # Optional: list of time parsing issues (e.g., ambiguous meridiem)
        "time_issues": time_issues
    }
    logger.info(
        f"[date_role] Stored date_roles in resolved_booking: {resolved_booking.get('date_roles')}",
        extra={'intent': intent_result.get(
            "intent") if intent_result else None}
    )

    # Validate temporal shape completeness (authoritative - must pass for RESOLVED)
    # This runs after all ambiguity checks but before finalizing status
    # Pass entities for weekday-only range normalization (Luma is stateless)
    intent_name = intent_result.get("intent")
    
    # Delta normalization for MODIFY_BOOKING: slot normalization (semantic shaping), not calendar binding
    # This runs BEFORE temporal shape validation to normalize modification deltas
    # Rules:
    # - Do NOT run for CREATE intents (CREATE_APPOINTMENT, CREATE_RESERVATION)
    # - Do NOT calendar-bind (actual date resolution happens later)
    # - Do NOT infer missing values
    if (intent_name == "MODIFY_BOOKING" and
        clarification is None and
        tenant_context):
        
        booking_mode = tenant_context.get("booking_mode", "service")
        
        # Appointment (service) rules: normalize datetime_range for MODIFY_BOOKING
        if booking_mode == "service":
            time_refs = resolved_booking.get("time_refs", [])
            time_mode = resolved_booking.get("time_mode")
            date_refs = resolved_booking.get("date_refs", [])
            date_mode = resolved_booking.get("date_mode")
            
            # Check if time is present (time_refs exist or time_mode is valid)
            has_time = bool(time_refs) or time_mode in {
                TimeMode.EXACT.value, TimeMode.RANGE.value, TimeMode.WINDOW.value
            } or resolved_booking.get("time_constraint")
            
            # Check if date is present (date_refs exist and date_mode is valid)
            has_date = bool(date_refs) and date_mode is not None and date_mode != DateMode.FLEXIBLE.value
            
            # For MODIFY_BOOKING appointments: any time OR date change → set has_datetime = true
            # Time-only modifications are valid (no date required)
            # Date-only modifications are valid (no time required) - date can anchor time from existing booking
            if has_time or has_date:
                # Set has_datetime = true for any time-related or date-related change
                resolved_booking["has_datetime"] = True
                
                # Build minimal datetime_range structure only if time_refs exist
                # Calendar binder will resolve to actual dates/times
                if time_refs:
                    # Build minimal datetime_range with time references
                    # The calendar binder will resolve these to actual datetime values
                    resolved_booking["datetime_range"] = {
                        "start": time_refs[0] if time_refs else None,
                        "end": time_refs[0] if time_refs else None  # Same for minimal range
                    }
                    logger.info(
                        f"[semantic] MODIFY_BOOKING appointment: set has_datetime=True and built datetime_range "
                        f"from time_refs={time_refs} (calendar binder will resolve to actual dates)"
                    )
                elif has_date:
                    # Date-only modification: set has_datetime=True (date can anchor time from existing booking)
                    logger.info(
                        f"[semantic] MODIFY_BOOKING appointment: date-only modification, set has_datetime=True "
                        f"(date_refs={date_refs}, calendar binder will anchor time from existing booking)"
                    )
                
                # DO NOT require clarification for time-only or date-only MODIFY_BOOKING
                # Both are valid modifications - decision layer will determine readiness
        
        # Reservation rules: normalize date_range for MODIFY_BOOKING
        elif booking_mode == "reservation":
            date_refs = resolved_booking.get("date_refs", [])
            date_mode = resolved_booking.get("date_mode")
            
            # If exactly TWO explicit dates: emit date_range {start, end}
            if len(date_refs) == 2:
                # Emit date_range structure in resolved_booking for MODIFY_BOOKING
                # The calendar binder will bind the date_refs to actual dates
                # Store date_refs in date_range structure (calendar binder will resolve to actual dates)
                resolved_booking["date_range"] = {
                    "start": date_refs[0] if len(date_refs) > 0 else None,
                    "end": date_refs[1] if len(date_refs) > 1 else None
                }
                logger.info(
                    f"[semantic] MODIFY_BOOKING reservation: emitted date_range from exactly two dates: "
                    f"date_refs={date_refs}, date_mode={date_mode} (calendar binder will resolve to actual dates)"
                )
            # If exactly ONE date: DO NOT build date_range, require clarification
            elif len(date_refs) == 1:
                # Single date detected - do NOT collapse into date_range (NEVER collapse a single date)
                # Require clarification for missing date (end_date or start_date)
                # For simplicity, require end_date clarification
                clarification = Clarification(
                    reason=ClarificationReason.MISSING_DATE,
                    data={
                        "missing_slots": ["end_date"]
                    }
                )
                logger.info(
                    f"[semantic] MODIFY_BOOKING reservation: single date detected, requiring clarification: "
                    f"date_refs={date_refs} (NEVER collapse single date into date_range)"
                )

    # Validate temporal shape completeness (authoritative - must pass for RESOLVED)
    # This runs AFTER delta normalization for MODIFY_BOOKING
    # For CREATE intents, this validation still applies
    # For MODIFY_BOOKING, skip temporal shape validation (delta normalization handles it)
    if intent_name != "MODIFY_BOOKING":
        temporal_clarification = _validate_temporal_shape_completeness(
            intent_name, resolved_booking, date_resolution, entities, None  # Luma is stateless
        )
        if temporal_clarification:
            # Temporal shape incomplete - force needs_clarification
            # This overrides any prior RESOLVED decision
            clarification = temporal_clarification

    result = SemanticResolutionResult(
        resolved_booking=resolved_booking,
        needs_clarification=clarification is not None,
        clarification=clarification
    )

    # Build trace fragment
    trace = {
        "semantic": {
            "service_ids": [s.get("text", "") if isinstance(s, dict) else str(s) for s in services],
            "date_mode": resolved_booking.get("date_mode", "none"),
            "date_refs": resolved_booking.get("date_refs", []),
            "time_mode": resolved_booking.get("time_mode", "none"),
            "time_refs": resolved_booking.get("time_refs", []),
            "time_constraint": resolved_booking.get("time_constraint"),
            # Time parsing issues (e.g., ambiguous meridiem)
            "time_issues": time_issues,
            "needs_clarification": clarification is not None,
            "clarification_reason": clarification.reason.value if clarification else None,
            "time_extraction_trace": time_extraction_trace
        }
    }

    return result, trace


def _is_fuzzy_hour(time_text: str) -> bool:
    """
    Check if time is a fuzzy hour pattern (6ish, around 6, about 6).

    Examples:
    - "6ish" → True
    - "around 6" → True
    - "about 6" → True
    - "6pm" → False
    """
    time_lower = time_text.lower().strip()
    fuzzy_patterns = ["ish", "around", "about"]
    return any(pattern in time_lower for pattern in fuzzy_patterns)


def _extract_hour_only_time(entities: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract hour-only time patterns from original sentence.

    Patterns:
    - Direct: "at 9", "at 10", "by 4", "before 6", "after 3"
    - Modification patterns: "make it 10", "set it to 9", "change it 11", "move it to 14"
      (for single-turn modifications or explicit MODIFY_BOOKING intent, not continuation inference)

    Only extracts if:
    - No full time already extracted (CRITICAL: skip if TIME tokens with am/pm exist)
    - No time window already extracted
    - Pattern matches hour-only expression
    - No time constraint pattern (constraints handled separately)

    NOTE: This function only extracts time values from the sentence. It does NOT infer intent.
    Fragmentary inputs without explicit booking verbs must return UNKNOWN intent via intent resolver.

    Args:
        entities: Raw extraction output containing osentence

    Returns:
        Dict with "hour" (int 0-23) and "text" (original match) if found, None otherwise
    """
    # CRITICAL: Skip fallback if TIME tokens with am/pm already exist
    times = entities.get("times", [])
    if times:
        # Check if any time has am/pm - if so, don't use fallback
        for time_entity in times:
            time_text = time_entity.get("text", "").lower()
            if "am" in time_text or "pm" in time_text:
                return None  # TIME token with am/pm exists, skip fallback

    osentence = entities.get("osentence", "")
    if not osentence:
        return None

    # Pattern 1: Direct hour-only patterns (exclude constraint patterns)
    # (at)\s+(\d{1,2})\b - only "at" for direct times, not "by/before/after" (those are constraints)
    # Matches: "at 9", "at 10" (but NOT "by 4", "before 6", "after 3" - those are constraints)
    direct_pattern = re.compile(
        r'\b(at)\s+(\d{1,2})\b',
        re.IGNORECASE
    )

    match = direct_pattern.search(osentence)
    if match:
        hour_str = match.group(2)
        hour = int(hour_str)

        # Validate hour range (0-23)
        if 0 <= hour <= 23:
            return {
                "hour": hour,
                "text": match.group(0)  # Full match like "at 10"
            }

    # Pattern 2: Modification patterns (for single-turn modifications or explicit MODIFY_BOOKING)
    # (make|set|change|update|move)\s+(it|this)\s+(to\s+)?(\d{1,2})\b
    # Matches: "make it 10", "set it to 9", "change it 11", "move it to 14"
    # NOTE: These patterns are for time extraction only, not continuation inference
    # If input lacks explicit booking verbs, intent resolver will return UNKNOWN
    modification_pattern = re.compile(
        r'\b(make|set|change|update|move)\s+(it|this)\s+(to\s+)?(\d{1,2})\b',
        re.IGNORECASE
    )

    match = modification_pattern.search(osentence)
    if match:
        # Hour is in group 4 (after verb, pronoun, optional "to")
        hour_str = match.group(4)
        hour = int(hour_str)

        # Validate hour range (0-23)
        if 0 <= hour <= 23:
            return {
                "hour": hour,
                "text": match.group(0)  # Full match like "make it 10"
            }

    return None


def _resolve_time_semantics(
    entities: Dict[str, Any],
    structure: Dict[str, Any],
    original_entities: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """
    Resolve time semantics following precedence rules.

    Precedence: exact > range > window > none

    Args:
        entities: Raw extraction output (filtered)
        structure: Structure interpretation result
        original_entities: Original entities before filtering (for trace)

    Returns:
        Tuple of (Dict with "mode" and "refs" keys, time_extraction_trace, time_issues)
    """
    times = entities.get("times", [])
    time_windows = entities.get("time_windows", [])
    time_type = structure.get("time_type", TimeMode.NONE.value)

    # Now compute the actual time resolution first
    time_resolution = None
    # Track time parsing issues (e.g., ambiguous meridiem)
    time_issues: List[Dict[str, Any]] = []

    # Rule 1: Window + exact time - exact time wins, window is discarded
    # Example: "tomorrow morning at 9am" → resolves to 9am, not morning window
    # Exact time always overrides time windows for appointment booking
    if time_windows and times:
        # Discard windows, use only exact time
        time_resolution = {
            "mode": TimeMode.EXACT.value,
            "refs": [times[0].get("text")]  # Only exact time, no windows
        }

    # Rule 1.5: Fuzzy hours (6ish, around 6) → treat as range ONLY if time window exists
    # If no time window, flag as ambiguous (consistent with bare hour policy)
    if not time_resolution and times:
        for time_entity in times:
            time_text = time_entity.get("text", "")
            if _is_fuzzy_hour(time_text):
                # Extract hour from fuzzy pattern
                hour_match = re.search(r'(\d+)', time_text)
                if hour_match and time_windows:
                    # Time window provides context, treat as range
                    hour = int(hour_match.group(1))
                    time_resolution = {
                        "mode": TimeMode.RANGE.value,
                        "refs": [f"{hour}:00", f"{hour+1}:00"]
                    }
                    break
                # If no time window, will be flagged as ambiguous in _check_ambiguity

    # Rule 2: Detect ambiguous meridiem patterns FIRST (before range resolution)
    # Check original sentence for "between X and Y" patterns where X and Y are numeric hours
    # This must run regardless of time_type because numbers without AM/PM may not be extracted as TIME entities
    osentence = entities.get("osentence", "")
    if osentence and TEMPORAL_RULES.require_explicit_meridiem:
        # Check for ambiguous meridiem pattern: "between X and Y" where X and Y are numeric hours without AM/PM
        range_pattern = re.compile(
            r'\b(between|from)\s+(\d{1,2})\s+(and|to|-)\s+(\d{1,2})\b',
            re.IGNORECASE
        )
        match = range_pattern.search(osentence.lower())

        if match:
            start_hour = int(match.group(2))
            end_hour = int(match.group(4))

            # Check if times were extracted and if they have AM/PM
            has_am_pm = False
            if times:
                has_am_pm = any(
                    "am" in str(t.get("text", "")).lower() or "pm" in str(
                        t.get("text", "")).lower()
                    for t in times
                )

            # If no AM/PM found, create ambiguity issue
            if not has_am_pm:
                # Extract the raw text for the range
                raw_range = match.group(0)
                time_issue = {
                    "kind": "ambiguous_meridiem",
                    "raw": raw_range,
                    "start_hour": start_hour,
                    "end_hour": end_hour,
                    "reason": "missing_am_pm",
                    "candidates": ["am", "pm"]
                }
                time_issues.append(time_issue)
                # Do not output time_refs - set to NONE
                time_resolution = {
                    "mode": TimeMode.NONE.value,
                    "refs": []
                }

    # Rule 2b: Range (between X and Y) - check before single exact time
    # Only proceed if we haven't already set time_resolution due to ambiguous meridiem
    if not time_resolution and (time_type == TimeMode.RANGE or time_type == TimeMode.RANGE.value):
        if times:
            time_resolution = {
                "mode": TimeMode.RANGE.value,
                "refs": [t.get("text") for t in times[:2]]
            }

    # Rule 3: Exact time wins (if present, single or multiple)
    if not time_resolution and times:
        if len(times) == 1:
            time_resolution = {
                "mode": TimeMode.EXACT.value,
                "refs": [times[0].get("text")]
            }
        elif len(times) >= 2:
            # Multiple times without range marker → ambiguity
            time_resolution = {
                "mode": TimeMode.EXACT.value,  # Default to exact, but flag ambiguity later
                "refs": [t.get("text") for t in times]
            }

    # Rule 4: Window (coarse time ranges) - only if no exact time
    if not time_resolution and time_windows:
        time_resolution = {
            "mode": TimeMode.WINDOW.value,
            "refs": [tw.get("text") for tw in time_windows]
        }

    # Rule 5: Hour-only fallback - extract hour-only patterns if no times/windows extracted
    # CRITICAL: Only trigger if no TIME tokens exist (prevents fallback from overriding parsed times)
    # Also skip if time_constraint exists (constraints are handled separately)
    if not time_resolution and not times and not time_windows:
        # Check if there's a time constraint - if so, don't use fallback
        # (time constraints are handled separately and should not trigger hour-only fallback)
        osentence = entities.get("osentence", "")
        has_constraint_pattern = re.search(
            r'\b(by|before|after)\s+\d+', osentence, re.IGNORECASE)

        # Only use fallback if no constraint pattern and no extracted times
        if not has_constraint_pattern:
            hour_only_match = _extract_hour_only_time(entities)
            if hour_only_match:
                hour = hour_only_match["hour"]
                # Normalize to HH:00 format (24-hour)
                time_ref = f"{hour:02d}:00"
                print(
                    f"[time-extract]: extracted hour-only time from semantic fallback: {hour_only_match['text']} → {time_ref}")
                time_resolution = {
                    "mode": TimeMode.EXACT.value,
                    "refs": [time_ref],
                    "precision": "hour"  # Mark as hour-only precision
                }

    # Rule 6: None (only if no resolution found yet)
    if not time_resolution:
        time_resolution = {
            "mode": TimeMode.NONE.value,
            "refs": []
        }

    # Now build time extraction trace based on the resolution
    osentence = (original_entities or entities).get("osentence", "")

    # Detect time-like tokens in original sentence
    time_like_tokens = []
    if osentence:
        # Extract tokens that might be time-related
        tokens = osentence.lower().split()
        time_keywords = ["between", "and", "at", "by", "before",
                         "after", "morning", "afternoon", "evening", "night"]
        time_patterns = re.findall(r'\b\d{1,2}\b', osentence)  # Numbers
        for token in tokens:
            if token in time_keywords or any(char.isdigit() for char in token):
                time_like_tokens.append(token)
        # Add standalone numbers
        time_like_tokens.extend(
            [t for t in time_patterns if t not in time_like_tokens])

    # Build authoritative time extraction trace with mandatory schema
    time_language_present = bool(time_like_tokens or times or time_windows)

    # Initialize trace with mandatory schema
    time_extraction_trace = {
        "time_language_present": time_language_present,
        "raw_tokens": time_like_tokens if time_like_tokens else [],
        "detected_pattern": "none",
        "normalization_attempted": False,
        "normalization_result": "rejected",
        "rejection_reason": None
    }

    # If time-like tokens exist, populate trace
    if time_language_present:
        time_extraction_trace["normalization_attempted"] = True

        # Detect pattern - MUST NOT be "none" if raw_tokens exist
        detected_pattern = "none"

        # Check for constraint patterns first (after, before, by)
        osentence_lower = osentence.lower() if osentence else ""
        if re.search(r'\b(after|before|by)\s+\d+', osentence_lower):
            if "after" in osentence_lower:
                detected_pattern = "after"
            elif "before" in osentence_lower:
                detected_pattern = "before"
            else:
                detected_pattern = "after"  # "by" treated as "after"
        elif time_type == TimeMode.RANGE or time_type == TimeMode.RANGE.value:
            detected_pattern = "between_range"
        elif times:
            # Check if times have AM/PM
            has_am_pm = any("am" in str(t.get("text", "")).lower() or "pm" in str(
                t.get("text", "")).lower() for t in times)
            if has_am_pm:
                detected_pattern = "exact_time"
            else:
                detected_pattern = "exact_time"  # Still exact_time, just missing meridiem
        elif time_windows:
            detected_pattern = "window"
        elif any(_is_fuzzy_hour(str(t.get("text", ""))) for t in times):
            detected_pattern = "fuzzy"

        # ENFORCEMENT: If raw_tokens exist, pattern must NOT be "none"
        if time_extraction_trace["raw_tokens"] and detected_pattern == "none":
            # Fallback: if we have tokens but couldn't detect pattern, mark as detected
            detected_pattern = "exact_time"  # Default assumption

        time_extraction_trace["detected_pattern"] = detected_pattern

        # Determine normalization result based on actual resolution
        normalization_result = "rejected"
        rejection_reason = None

        if time_resolution["mode"] != TimeMode.NONE.value:
            normalization_result = "accepted"
            rejection_reason = None
        else:
            # Determine why it was rejected - use normalized enum values only
            if detected_pattern == "between_range":
                # Check if times have AM/PM
                if times:
                    has_am_pm = any("am" in str(t.get("text", "")).lower() or "pm" in str(
                        t.get("text", "")).lower() for t in times)
                    if not has_am_pm:
                        rejection_reason = "missing_am_pm"
                    elif len(times) < 2:
                        rejection_reason = "zero_length_window"
                    else:
                        rejection_reason = "missing_am_pm"  # Default for between_range rejection
                else:
                    rejection_reason = "missing_am_pm"  # No time tokens in range
            elif detected_pattern == "exact_time":
                # Check if it's missing AM/PM
                if times:
                    has_am_pm = any("am" in str(t.get("text", "")).lower() or "pm" in str(
                        t.get("text", "")).lower() for t in times)
                    if not has_am_pm:
                        rejection_reason = "missing_am_pm"
                    else:
                        rejection_reason = "ambiguous_time"  # Has AM/PM but still rejected
                else:
                    rejection_reason = "missing_am_pm"
            elif detected_pattern == "fuzzy":
                rejection_reason = "fuzzy_not_allowed_for_intent"
            elif detected_pattern in ["after", "before"]:
                rejection_reason = "time_without_date"  # Constraints need date context
            elif detected_pattern == "window":
                # Windows are generally accepted, but if resolution is NONE, something went wrong
                rejection_reason = "contradictory_window"
            else:
                # Should not happen if pattern detection is correct
                rejection_reason = "ambiguous_time"

        time_extraction_trace["normalization_result"] = normalization_result
        time_extraction_trace["rejection_reason"] = rejection_reason

    return time_resolution, time_extraction_trace, time_issues


def _detect_time_constraint(
    entities: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Detect time constraint patterns like "by 4pm", "before 5pm", or "after 10am".

    Args:
        entities: Raw extraction output containing psentence and times

    Returns:
        Dict with "type" ("before", "after", "by") and "time" (HH:MM format) if constraint detected, None otherwise
    """
    psentence = entities.get("psentence", "")
    osentence = entities.get("osentence", "")
    if not psentence or not osentence:
        return None

    # Check for constraint patterns: "by timetoken", "before timetoken", "after timetoken"
    # Pattern: constraint word + timetoken
    constraint_pattern = re.compile(
        r'\b(by|before|after)\s+timetoken\b', re.IGNORECASE)
    match = constraint_pattern.search(psentence)
    if not match:
        return None

    constraint_type = match.group(1).lower()  # "by", "before", or "after"

    # If constraint pattern found, find which time entity corresponds to it
    times = entities.get("times", [])
    if not times:
        return None

    # Find the time that appears after constraint word in original sentence
    # Extract the time text and convert to 24-hour format if needed
    if len(times) == 1:
        # Single time with constraint pattern → it's the constraint
        time_text = times[0].get("text", "")
        if time_text:
            # Convert time to 24-hour format (preserve am/pm semantics)
            time_24h = _convert_time_to_24h(time_text)
            if time_24h:
                return {
                    "type": constraint_type,
                    "time": time_24h
                }
    else:
        # Multiple times: find which one is after constraint word
        # Match constraint word position in original sentence
        constraint_word_match = re.search(
            r'\b(by|before|after)\s+', osentence, re.IGNORECASE)
        if constraint_word_match:
            constraint_pos = constraint_word_match.end()
            # Find first time entity after constraint position
            for time_entity in times:
                time_start = time_entity.get("start", 0)
                if time_start >= constraint_pos:
                    time_text = time_entity.get("text", "")
                    if time_text:
                        time_24h = _convert_time_to_24h(time_text)
                        if time_24h:
                            return {
                                "type": constraint_type,
                                "time": time_24h
                            }
        # Fallback: use first time if position matching fails
        time_text = times[0].get("text", "")
        if time_text:
            time_24h = _convert_time_to_24h(time_text)
            if time_24h:
                return {
                    "type": constraint_type,
                    "time": time_24h
                }

    return None


def _convert_time_to_24h(time_text: str) -> Optional[str]:
    """
    Convert time text to 24-hour format (HH:MM), preserving am/pm semantics.

    Examples:
    - "4pm" → "16:00"
    - "10am" → "10:00"
    - "12:30pm" → "12:30"
    - "14:00" → "14:00" (already 24-hour)
    """
    time_lower = time_text.lower().strip()

    # Check if already 24-hour format (HH:MM or HH.MM)
    if re.match(r'^([01]?[0-9]|2[0-3])[:.][0-5][0-9]$', time_text):
        # Already 24-hour, return as-is
        return time_text.replace('.', ':')

    # Extract hour, minutes, and meridiem
    match = re.match(r'^(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?$', time_lower)
    if not match:
        return None

    hour = int(match.group(1))
    minutes = int(match.group(2)) if match.group(2) else 0
    meridiem = match.group(3)

    # Convert to 24-hour format
    if meridiem == "pm" and hour != 12:
        hour = hour + 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    return f"{hour:02d}:{minutes:02d}"


def _filter_constraint_times(
    entities: Dict[str, Any],
    time_constraint: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Filter out constraint times from entities so they don't get bound as regular times.

    Args:
        entities: Raw extraction output
        time_constraint: Time constraint dict with "type" and "time" (or legacy "latest_time"), or None

    Returns:
        Filtered entities dict with constraint times removed
    """
    if not time_constraint:
        return entities

    # Create a copy to avoid mutating original
    filtered = entities.copy()

    # Support both new format ("time") and legacy format ("latest_time")
    constraint_time = time_constraint.get(
        "time") or time_constraint.get("latest_time", "")

    if not constraint_time:
        return filtered

    # Filter out times that match the constraint time
    # Need to match both original format and 24-hour format
    times = filtered.get("times", [])
    if times:
        filtered_times = []
        for t in times:
            time_text = t.get("text", "")
            # Don't filter if time doesn't match constraint (may be different format)
            # Only filter exact matches to avoid false positives
            if time_text != constraint_time:
                # Also check if time_text converts to same 24h format
                time_24h = _convert_time_to_24h(time_text)
                if time_24h != constraint_time:
                    filtered_times.append(t)
        filtered["times"] = filtered_times

    return filtered


# Date pattern matching helpers
def _normalize_date_text(text: str) -> str:
    """
    Normalize date text using vocabulary synonyms and typos.

    Note: This is a fallback for entity text that wasn't normalized during extraction.
    Primary normalization should occur in extraction stage.
    """
    text_lower = text.lower().strip()

    # Load vocabulary maps (synonyms and typos)
    synonym_map, typo_map, _ = _load_vocabulary_maps()

    # Apply vocabulary normalization (synonyms and typos → canonical)
    normalized, _ = normalize_vocabularies(text_lower, synonym_map, typo_map)

    return normalized


def _is_simple_relative_day(text: str) -> bool:
    """Check if text is a simple relative day: today, tomorrow, day after tomorrow, tonight."""
    text_lower = text.lower()
    entity_types = _load_entity_types()
    # Get relative dates from entity_types.date.relative
    relative_dates = entity_types.get("date", {}).get("relative", [])
    simple_days = [rd.get("value", "")
                   for rd in relative_dates if rd.get("value")]
    # Also check for "day after tomorrow" (not in entity_types but common pattern)
    simple_days.append("day after tomorrow")
    return any(day in text_lower for day in simple_days)


def _is_week_based(text: str) -> bool:
    """Check if text is week-based: this week, next week."""
    text_lower = text.lower()
    entity_types = _load_entity_types()
    # Get relative dates from entity_types.date.relative
    relative_dates = entity_types.get("date", {}).get("relative", [])
    week_patterns = [rd.get(
        "value", "") for rd in relative_dates if "week" in rd.get("value", "").lower()]
    # Also check for "this week" (may not be in entity_types)
    if "this week" not in week_patterns:
        week_patterns.append("this week")
    return any(pattern in text_lower for pattern in week_patterns)


def _has_concrete_date_anchor(text: str, entities: Dict[str, Any]) -> bool:
    """
    Check if a relative date phrase has a concrete anchor that allows resolution.

    Concrete anchors include:
    - Weekday (e.g., "Wednesday" in "next week Wednesday")
    - Explicit date (e.g., "15th", "Jan 15" in "next week 15th")
    - Range delimiter (e.g., "between", "from" in "between Monday and Wednesday")

    Args:
        text: The normalized date text to check
        entities: Raw extraction output (for checking dates_absolute)

    Returns:
        True if phrase has a concrete anchor, False otherwise
    """
    text_lower = text.lower()

    # Check for weekday presence
    vocab = _load_vocabularies()
    weekdays_dict = vocab.get("weekdays", {})
    weekdays = []
    if isinstance(weekdays_dict, dict):
        # New structure: vocabularies.weekdays is canonical-first (canonical -> [variants])
        weekdays = list(weekdays_dict.keys())
        # Also check all variants (accepted variants from vocabularies)
        for _canonical, variants in weekdays_dict.items():
            if isinstance(variants, list):
                weekdays.extend(variants)
    # Normalize weekdays to lowercase for comparison
    weekdays = [day.lower() for day in weekdays if isinstance(day, str)]

    has_weekday = any(day in text_lower for day in weekdays)
    if has_weekday:
        return True

    # Check for explicit date (ordinal like "15th", "12th" or month+day like "Jan 15")
    # Pattern: ordinal number (1st, 2nd, 3rd, 4th, etc.)
    ordinal_pattern = r'\b\d{1,2}(?:st|nd|rd|th)\b'
    if re.search(ordinal_pattern, text_lower):
        return True

    # Check for month + day pattern (e.g., "Jan 15", "December 12")
    month_names = [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "sept", "oct", "nov", "dec"
    ]
    for month in month_names:
        if month in text_lower:
            # Check if there's a day number nearby
            # Look for pattern like "month day" or "day month"
            day_pattern = r'\b\d{1,2}\b'
            if re.search(day_pattern, text_lower):
                return True

    # Check for range delimiter keywords
    range_delimiters = ["between", "from", "to", "until", "till", "through"]
    has_range_delimiter = any(
        delimiter in text_lower for delimiter in range_delimiters)
    if has_range_delimiter:
        return True

    # Check if there are absolute dates in entities (handles cases where date is extracted separately)
    dates_absolute = entities.get("dates_absolute", [])
    if dates_absolute:
        return True

    return False


def _is_weekend_reference(text: str) -> bool:
    """Check if text is a weekend reference: this weekend, next weekend."""
    text_lower = text.lower()
    # Weekend patterns are common but may not be in entity_types, so check both
    weekend_patterns = ["this weekend", "next weekend"]
    return any(pattern in text_lower for pattern in weekend_patterns)


def _is_specific_weekday(text: str) -> bool:
    """Check if text is a specific weekday: this Monday, next Monday, coming Friday."""
    text_lower = text.lower()
    vocab = _load_vocabularies()
    # New structure: vocabularies.weekdays is canonical-first (canonical -> [variants])
    weekdays_dict = vocab.get("weekdays", {})
    weekdays = list(weekdays_dict.keys()) if isinstance(
        weekdays_dict, dict) else []
    # Also check all variants (accepted variants from vocabularies)
    for _canonical, variants in weekdays_dict.items():
        if isinstance(variants, list):
            weekdays.extend(variants)
    modifiers = vocab.get("date_modifiers", [])
    has_weekday = any(day in text_lower for day in weekdays)
    has_modifier = any(mod in text_lower for mod in modifiers)
    return has_weekday and has_modifier and not _is_plural_weekday(text)


def _is_month_relative(text: str) -> bool:
    """Check if text is month-relative: this month, next month."""
    text_lower = text.lower()
    entity_types = _load_entity_types()
    # Get relative dates from entity_types.date.relative
    relative_dates = entity_types.get("date", {}).get("relative", [])
    month_patterns = [rd.get(
        "value", "") for rd in relative_dates if "month" in rd.get("value", "").lower()]
    # Also check for "this month" (may not be in entity_types)
    if "this month" not in month_patterns:
        month_patterns.append("this month")
    return any(pattern in text_lower for pattern in month_patterns)


def _is_fine_grained_modifier(text: str) -> bool:
    """Check if text has fine-grained modifiers: early/mid/end of next week/month."""
    text_lower = text.lower()
    vocab = _load_vocabularies()
    modifiers = vocab.get("fine_grained_modifiers", [])
    has_modifier = any(mod in text_lower for mod in modifiers)
    has_period = "week" in text_lower or "month" in text_lower
    return has_modifier and has_period


def _is_locale_ambiguous(text: str) -> bool:
    """Check if date format is locale-ambiguous (e.g., 07/12 could be July 12 or Dec 7)."""
    # Pattern: DD/MM or MM/DD (ambiguous)
    pattern = r'^\d{1,2}/\d{1,2}(?:/\d{2,4})?$'
    if re.match(pattern, text):
        parts = text.split('/')
        if len(parts) >= 2:
            first = int(parts[0])
            second = int(parts[1])
            # If both parts could be months (1-12), it's ambiguous
            if 1 <= first <= 12 and 1 <= second <= 12:
                return True
    return False


def _is_plural_weekday(text: str) -> bool:
    """Check if text contains plural weekday (e.g., 'next Mondays', 'Fridays next week')."""
    text_lower = text.lower()
    vocab = _load_vocabularies()
    # New structure: vocabularies.weekdays is canonical-first (canonical -> [variants])
    weekdays_dict = vocab.get("weekdays", {})
    # Extract plural variants (end with 's' or 'days')
    weekdays_plural = []
    for _canonical, variants in weekdays_dict.items():
        if isinstance(variants, list):
            for variant in variants:
                if variant.endswith("s") or variant.endswith("days"):
                    weekdays_plural.append(variant)
    return any(day in text_lower for day in weekdays_plural)


def _is_vague_date_reference(text: str) -> bool:
    """Check if text is a vague date reference requiring clarification."""
    text_lower = text.lower()
    vocab = _load_vocabularies()
    vague_patterns = vocab.get("vague_date_patterns", [])
    return any(pattern in text_lower for pattern in vague_patterns)


def _is_context_dependent(text: str) -> bool:
    """Check if text is context-dependent requiring clarification."""
    text_lower = text.lower()
    vocab = _load_vocabularies()
    context_patterns = vocab.get("context_dependent_patterns", [])
    return any(pattern in text_lower for pattern in context_patterns)


def _is_bare_weekday(text: str) -> bool:
    """
    Check if text is a bare weekday without modifier (this/next).

    Examples:
    - "saturday" → True (ambiguous)
    - "this saturday" → False (resolved)
    - "next monday" → False (resolved)
    - "friday morning" → True (ambiguous - bare weekday)
    """
    text_lower = text.lower().strip()
    vocab = _load_vocabularies()
    # New structure: vocabularies.weekdays is canonical-first (canonical -> [variants])
    weekdays_dict = vocab.get("weekdays", {})
    weekdays = list(weekdays_dict.keys()) if isinstance(
        weekdays_dict, dict) else []
    # Also check all variants (accepted variants from vocabularies)
    for _canonical, variants in weekdays_dict.items():
        if isinstance(variants, list):
            weekdays.extend(variants)

    # Check if text is exactly a weekday or starts with a weekday
    for weekday in weekdays:
        if text_lower == weekday:
            return True
        # Check if it starts with weekday but doesn't have modifier
        if text_lower.startswith(weekday):
            # Check for modifiers that make it unambiguous
            modifiers = ["this", "next", "last", "coming", "following"]
            # Look for modifier before the weekday
            for modifier in modifiers:
                if modifier in text_lower and text_lower.index(modifier) < text_lower.index(weekday):
                    return False
            # If weekday is at start and no modifier before it, it's bare
            if text_lower.startswith(weekday):
                return True

    return False


def _is_weekday_only_range(
    date_refs: list,
    date_mode: str,
    entities: Dict[str, Any],
    memory_state: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Check if a date range contains only weekdays without anchors.
    
    A weekday-only range is ambiguous if:
    - date_mode is RANGE
    - date_refs contains only weekdays (monday, tuesday, etc.)
    - No modifiers (this/next/last)
    - No explicit dates/months/years
        - No anchored date (Luma is stateless)
    
    Args:
        date_refs: List of date reference strings
        date_mode: Date mode ("range", "single_day", etc.)
        entities: Raw extraction output (for checking dates_absolute)
        memory_state: Optional memory state (unused - Luma is stateless, always None)
    
    Returns:
        True if this is a weekday-only range without anchors, False otherwise
    """
    if date_mode != DateMode.RANGE.value or len(date_refs) < 2:
        return False
    
    if not ALLOW_BARE_WEEKDAY_RANGE_BINDING:
        # Check if all refs are weekdays
        vocab = _load_vocabularies()
        weekdays_dict = vocab.get("weekdays", {})
        weekdays = list(weekdays_dict.keys()) if isinstance(weekdays_dict, dict) else []
        
        # Collect all weekday variants (canonical forms and synonyms)
        weekday_variants = []
        for canonical, variants in weekdays_dict.items():
            if isinstance(canonical, str):
                weekday_variants.append(canonical.lower())
            if isinstance(variants, list):
                weekday_variants.extend([v.lower() for v in variants if isinstance(v, str)])
            elif isinstance(variants, dict):
                # New structure: { "synonyms": [...], "typos": [...] }
                synonyms = variants.get("synonyms", [])
                if isinstance(synonyms, list):
                    weekday_variants.extend([v.lower() for v in synonyms if isinstance(v, str)])
                typos = variants.get("typos", [])
                if isinstance(typos, list):
                    weekday_variants.extend([v.lower() for v in typos if isinstance(v, str)])
        
        weekday_variants = list(set(weekday_variants))  # Remove duplicates
        
        # Check if all date_refs are weekdays (using word-boundary matching for accuracy)
        all_weekdays = True
        has_modifier = False
        has_explicit_date = False
        
        # Combine all refs into a single string for checking modifiers and dates
        combined_refs = " ".join(str(ref).lower() for ref in date_refs)
        
        # Check each ref to see if it's a weekday
        for ref in date_refs:
            ref_lower = str(ref).lower().strip()
            if not ref_lower:
                all_weekdays = False
                break
            
            # Check if ref exactly matches a weekday or contains a weekday as a whole word
            # Use word boundaries to avoid false matches (e.g., "sunday" in "sundays" should match, but "day" shouldn't match "sunday")
            is_weekday = False
            for weekday in weekday_variants:
                # Exact match or word-boundary match
                if ref_lower == weekday or re.search(r'\b' + re.escape(weekday) + r'\b', ref_lower):
                    is_weekday = True
                    break
            
            if not is_weekday:
                all_weekdays = False
                break
            
            # Check for modifiers in this specific ref (not combined, to avoid false positives)
            modifiers = ["this", "next", "last", "coming", "following"]
            if any(re.search(r'\b' + re.escape(mod) + r'\b', ref_lower) for mod in modifiers):
                has_modifier = True
                break
        
        # Check for explicit dates/months in entities
        dates_absolute = entities.get("dates_absolute", [])
        if dates_absolute:
            has_explicit_date = True
        
        # Check for explicit dates in combined refs (ordinal, month names, etc.)
        month_names = [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
            "jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "sept", "oct", "nov", "dec"
        ]
        ordinal_pattern = r'\b\d{1,2}(?:st|nd|rd|th)\b'
        if re.search(ordinal_pattern, combined_refs):
            has_explicit_date = True
        if any(re.search(r'\b' + re.escape(month) + r'\b', combined_refs) for month in month_names):
            has_explicit_date = True
        
        # Check for anchored date in memory (Luma is stateless, so this is always False)
        has_memory_anchor = False
        if memory_state:
            booking_state = memory_state.get("booking_state", {})
            date_range = booking_state.get("date_range") or booking_state.get("datetime_range")
            if date_range:
                # If there's a prior resolved date, we have an anchor
                has_memory_anchor = True
        
        # Weekday-only range without anchors: exactly 2 weekdays, no modifiers, no explicit dates, no memory anchor
        # Since Luma is stateless, has_memory_anchor is always False
        if all_weekdays and len(date_refs) == 2 and not has_modifier and not has_explicit_date and not has_memory_anchor:
            return True
    
    return False


def _is_bare_hour(time_text: str) -> bool:
    """
    Check if time is a bare hour without am/pm or time window.

    Examples:
    - "2" → True (ambiguous)
    - "2pm" → False (resolved)
    - "2 am" → False (resolved)
    - "2" with time window → False (resolved by context)
    """
    time_lower = time_text.lower().strip()

    # Remove common separators and whitespace
    time_clean = time_lower.replace(":", "").replace(".", "").replace(" ", "")

    # Check if it's just digits (bare hour)
    if time_clean.isdigit():
        # Check if it has am/pm indicator
        if "am" in time_lower or "pm" in time_lower or "a.m." in time_lower or "p.m." in time_lower:
            return False
        # If it's just digits without am/pm, it's bare
        return True

    # Check for patterns like "2 o'clock" without am/pm
    if "o'clock" in time_lower or "oclock" in time_lower:
        if "am" not in time_lower and "pm" not in time_lower:
            return True

    return False


def _has_fine_grained_modifier(text: str) -> bool:
    """Check if text has fine-grained modifier (early/mid/end)."""
    return _is_fine_grained_modifier(text)


def _build_time_constraint_from_resolution(time_resolution: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Build a canonical time_constraint object from time resolution output.
    """
    mode = time_resolution.get("mode")
    refs = time_resolution.get("refs") or []
    if mode == TimeMode.EXACT.value and refs:
        start = _convert_time_to_24h(str(refs[0])) or refs[0]
        return {"mode": TimeMode.EXACT.value, "start": start, "end": start, "label": None}
    if mode == TimeMode.WINDOW.value and len(refs) >= 2:
        start = _convert_time_to_24h(str(refs[0])) or refs[0]
        end = _convert_time_to_24h(str(refs[1])) or refs[1]
        return {"mode": TimeMode.WINDOW.value, "start": start, "end": end, "label": None}
    if mode == TimeMode.RANGE.value and len(refs) >= 2:
        start = _convert_time_to_24h(str(refs[0])) or refs[0]
        end = _convert_time_to_24h(str(refs[1])) or refs[1]
        return {"mode": TimeMode.WINDOW.value, "start": start, "end": end, "label": None}
    return None


def _parse_month_year(date_text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract month token and optional year from a date text."""
    month_pattern = re.compile(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t)?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
    )
    year_pattern = re.compile(r"\b(20\d{2}|19\d{2})\b")
    lower = str(date_text).lower()
    m = month_pattern.search(lower)
    y = year_pattern.search(lower)
    return (m.group(0) if m else None, y.group(0) if y else None)


def _get_next_month(month_token: str) -> str:
    """
    Get the next month name from a given month token.

    Args:
        month_token: Month name (e.g., "oct", "october")

    Returns:
        Next month name in same format (short or long)
    """
    month_map = {
        "jan": "feb", "january": "february",
        "feb": "mar", "february": "march",
        "mar": "apr", "march": "april",
        "apr": "may", "april": "may",
        "may": "jun",  # May has no abbreviation variation
        "jun": "jul", "june": "july",
        "jul": "aug", "july": "august",
        "aug": "sep", "august": "september",
        "sep": "oct", "september": "october",
        "oct": "nov", "october": "november",
        "nov": "dec", "november": "december",
        "dec": "jan", "december": "january"  # Wrap around
    }
    month_lower = month_token.lower()
    # Fallback to same if not found
    return month_map.get(month_lower, month_token)


def _extract_day_number(date_text: str) -> Optional[int]:
    """
    Extract day number from date text.

    Examples:
        "oct 29th" -> 29
        "15th dec" -> 15
        "2nd" -> 2
    """
    day_match = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\b', date_text.lower())
    if day_match:
        return int(day_match.group(1))
    return None


def _maybe_complete_shorthand_date_range(
    osentence: str,
    normalized_absolute: List[str],
    intent_name: Optional[str]
) -> Optional[List[str]]:
    """
    Complete shorthand date ranges for reservations when only day is provided for end.

    Examples:
        "oct 5th to 9th" -> ["oct 5th", "oct 9th"]  # Same month
        "oct 29th to 2nd" -> ["oct 29th", "nov 2nd"]  # Next month (smart inference)
    """
    # Get temporal shape from IntentRegistry (sole policy source)
    registry = get_intent_registry()
    intent_meta = registry.get(intent_name)
    temporal_shape = intent_meta.temporal_shape if intent_meta else None

    if temporal_shape != RESERVATION_TEMPORAL_TYPE:
        return None

    if len(normalized_absolute) != 1:
        return None

    # Only apply when exactly one absolute date exists and no second absolute date
    start_text = normalized_absolute[0]
    month_token, year_token = _parse_month_year(start_text)
    if not month_token:
        return None

    sentence_lower = str(osentence or "").lower()
    # Require a range connector
    connector_match = re.search(
        r"\b(to|until|till|through)\b|[-–—]", sentence_lower)
    if not connector_match:
        return None

    after = sentence_lower[connector_match.end():]
    # If another month appears after connector, do nothing (already explicit or cross-month)
    if re.search(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t)?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", after):
        return None

    # Look for day-only token
    day_match = re.search(r"\b(\d{1,2})(st|nd|rd|th)?\b", after)
    if not day_match:
        return None

    day_token = day_match.group(0)

    # Extract day numbers for smart month inference
    start_day = _extract_day_number(start_text)
    end_day = _extract_day_number(day_token)

    # Smart month inference: if end_day < start_day, infer next month
    inferred_month = month_token
    if start_day is not None and end_day is not None and end_day < start_day:
        inferred_month = _get_next_month(month_token)

    end_text = f"{inferred_month} {day_token}"
    if year_token:
        end_text = f"{end_text} {year_token}"

    return [start_text, end_text]


def _detect_date_role(
    entities: Dict[str, Any],
    date_index: int,
    intent_name: Optional[str] = None
) -> Optional[str]:
    """
    Detect date role (START_DATE or END_DATE) for reservation dates.

    Only applies to CREATE_RESERVATION intent.
    Uses normalized vocabulary matching, not hardcoded strings.

    Args:
        entities: Raw extraction output with osentence
        date_index: Index of date in date_refs (0 = first, 1 = second, etc.)
        intent_name: Intent name (CREATE_RESERVATION, CREATE_APPOINTMENT, or BOOK_APPOINTMENT)

    Returns:
        "START_DATE", "END_DATE", or None
    """
    # Use existing logger (already imported at module level)
    # Apply to CREATE_RESERVATION, BOOK_APPOINTMENT (which may become CREATE_RESERVATION), or MODIFY_BOOKING
    # The merge logic in resolve_service.py will filter out appointments, so we can safely detect roles for BOOK_APPOINTMENT
    if intent_name not in ("CREATE_RESERVATION", "BOOK_APPOINTMENT", "MODIFY_BOOKING"):
        logger.debug(
            f"[date_role] Skipping detection: intent={intent_name} (not CREATE_RESERVATION, BOOK_APPOINTMENT, or MODIFY_BOOKING)",
            extra={'intent': intent_name, 'date_index': date_index}
        )
        return None
    
    # For MODIFY_BOOKING, apply stricter rules:
    # - Assign start_date/end_date ONLY if:
    #   a) Two dates + range syntax (from X to Y), OR
    #   b) Explicit role keyword is present
    # - Never infer roles from intent alone (no default position-based assignment)
    is_modify_booking = (intent_name == "MODIFY_BOOKING")

    osentence = str(entities.get("osentence", "")).lower()
    if not osentence:
        logger.debug(
            f"[date_role] Skipping detection: no osentence",
            extra={'intent': intent_name, 'date_index': date_index}
        )
        return None

    logger.info(
        f"[date_role] Starting detection: intent={intent_name}, date_index={date_index}, "
        f"osentence='{osentence}'",
        extra={'intent': intent_name, 'date_index': date_index}
    )

    # START_DATE signals (range syntax and role keywords)
    start_signals = ["from", "starting", "beginning", "since"]
    start_role_keywords = ["start date", "start_date", "check-in date", "check-in", "checkin date", "arrival date"]
    
    # END_DATE signals (range syntax and role keywords)
    end_signals = ["to", "until", "till", "through", "ending"]
    end_role_keywords = ["end date", "end_date", "check-out date", "check-out", "checkout date", "check out date", "departure date"]
    
    # Find date positions in sentence
    # Note: date_index refers to position in the final date_refs list, not in all_dates
    # We need to check both dates and dates_absolute, but prioritize absolute
    dates = entities.get("dates", [])
    dates_absolute = entities.get("dates_absolute", [])
    
    # For MODIFY_BOOKING: check if we have two dates with range syntax
    dates_count = len(dates) + len(dates_absolute)
    has_range_syntax = False
    if is_modify_booking:
        # Check for range syntax: "from X to Y" or "between X and Y"
        range_patterns = [
            r'\bfrom\s+.*?\s+to\s+.*',
            r'\bbetween\s+.*?\s+and\s+.*'
        ]
        for pattern in range_patterns:
            if re.search(pattern, osentence, re.IGNORECASE):
                has_range_syntax = True
                break

    # EARLY GUARD: MODIFY_BOOKING single date without explicit range cue → return None immediately
    # This prevents any role assignment (END_DATE/START_DATE) for single dates without explicit range cues
    # Rule-subtractive fix: single dates in MODIFY_BOOKING must be treated as generic "date", not "end_date" or "start_date"
    if is_modify_booking and dates_count == 1:
        if not _has_explicit_range_cue(osentence):
            logger.info(
                f"[date_role] MODIFY_BOOKING early guard: Single date without explicit range cue → returning None. "
                f"dates_count={dates_count}, has_range_syntax={has_range_syntax}, osentence='{osentence}'",
                extra={'intent': intent_name, 'date_index': date_index, 'dates_count': dates_count, 'osentence': osentence}
            )
            return None

    logger.info(
        f"[date_role] Entity check: dates_count={len(dates)}, dates_absolute_count={len(dates_absolute)}, "
        f"date_index={date_index}",
        extra={'intent': intent_name, 'date_index': date_index}
    )

    # Determine which date entity corresponds to date_index
    # If dates_absolute exist, they come first in the final refs list
    if dates_absolute and date_index < len(dates_absolute):
        date_entity = dates_absolute[date_index]
        logger.info(
            f"[date_role] Using dates_absolute[{date_index}]: {date_entity}",
            extra={'intent': intent_name, 'date_index': date_index}
        )
    elif dates and date_index < len(dates):
        # Adjust index if we have absolute dates before relative ones
        adjusted_index = date_index - \
            len(dates_absolute) if dates_absolute else date_index
        if adjusted_index >= 0 and adjusted_index < len(dates):
            date_entity = dates[adjusted_index]
            logger.info(
                f"[date_role] Using dates[{adjusted_index}]: {date_entity}",
                extra={'intent': intent_name, 'date_index': date_index}
            )
        else:
            logger.warning(
                f"[date_role] No date entity found: adjusted_index={adjusted_index}, dates_len={len(dates)}",
                extra={'intent': intent_name, 'date_index': date_index}
            )
            return None
    else:
        logger.warning(
            f"[date_role] No date entity found: date_index={date_index} out of range",
            extra={'intent': intent_name, 'date_index': date_index, 'dates_count': len(
                dates), 'dates_absolute_count': len(dates_absolute)}
        )
        return None

    # FIX: Entities have "position" (token index), not "start"/"end" (character positions)
    # Find the date text in the sentence to get its character position
    date_text = date_entity.get("text", "")
    if not date_text:
        logger.warning(
            f"[date_role] No date text in entity: {date_entity}",
            extra={'intent': intent_name, 'date_index': date_index}
        )
        return None

    logger.info(
        f"[date_role] Date entity text: '{date_text}', entity_keys={list(date_entity.keys())}",
        extra={'intent': intent_name, 'date_index': date_index}
    )

    # Find the date text in the sentence (case-insensitive search)
    # Use the first occurrence (should be unique for the date we're checking)
    date_text_lower = date_text.lower()
    osentence_lower = osentence.lower()
    date_char_start = osentence_lower.find(date_text_lower)

    logger.info(
        f"[date_role] Text search: date_text='{date_text}', date_text_lower='{date_text_lower}', "
        f"osentence_lower='{osentence_lower}', date_char_start={date_char_start}",
        extra={'intent': intent_name, 'date_index': date_index}
    )

    if date_char_start < 0:
        # Date text not found in sentence - fallback to checking entire sentence
        logger.warning(
            f"[date_role] Date text '{date_text}' not found in sentence '{osentence}' - checking entire sentence",
            extra={'intent': intent_name, 'date_index': date_index}
        )
        sentence_before_date = osentence
    else:
        # Look back up to 50 characters before the date
        lookback_start = max(0, date_char_start - 50)
        sentence_before_date = osentence[lookback_start:date_char_start]
        logger.info(
            f"[date_role] Found date at char position {date_char_start}, checking before: '{sentence_before_date}'",
            extra={'intent': intent_name, 'date_index': date_index}
        )

    # For MODIFY_BOOKING: Check explicit role keywords FIRST (highest priority)
    # If role keyword is present, assign ONLY that role to dates that appear after it
    # Ignore dates that appear before the role keyword as contextual
    if is_modify_booking:
        # Check entire sentence for explicit role keywords (not just before date)
        sentence_for_role_check = osentence  # Use full sentence for role keyword detection
        
        # Check for START_DATE role keywords (e.g., "start date", "check-in date")
        for keyword in start_role_keywords:
            pattern = rf"\b{re.escape(keyword)}\b"
            match = re.search(pattern, sentence_for_role_check, re.IGNORECASE)
            if match:
                keyword_pos = match.start()
                keyword_end = match.end()
                # Only assign role if date appears AFTER the role keyword (ignore dates before it as contextual)
                if date_char_start >= 0:
                    # Date found in sentence - check if it appears after the role keyword
                    if keyword_end <= date_char_start <= keyword_end + 150:
                        # Date appears after role keyword - assign START_DATE role
                        logger.info(
                            f"[date_role] MODIFY_BOOKING: Detected START_DATE role keyword '{keyword}' for date_index={date_index} "
                            f"(keyword at {keyword_pos}-{keyword_end}, date at {date_char_start} - date appears after keyword)",
                            extra={'sentence': osentence, 'date_entity': date_entity.get('text', ''), 'keyword': keyword}
                        )
                        return "START_DATE"
                    else:
                        # Date appears before role keyword - ignore as contextual (don't assign role)
                        logger.debug(
                            f"[date_role] MODIFY_BOOKING: Ignoring date before START_DATE role keyword '{keyword}' (date at {date_char_start}, keyword at {keyword_pos}-{keyword_end})",
                            extra={'intent': intent_name, 'date_index': date_index, 'keyword': keyword}
                        )
                        return None
                else:
                    # Date text not found - check if we have only one date (single date with role keyword)
                    # CRITICAL: For MODIFY_BOOKING, only assign role if explicit range cue exists
                    # Single dates with role keywords should NOT get roles assigned unless there's explicit range syntax
                    if dates_count == 1:
                        # For single dates, only assign role if explicit range cue is present
                        if _has_explicit_range_cue(osentence):
                            logger.info(
                                f"[date_role] MODIFY_BOOKING: Detected START_DATE role keyword '{keyword}' for single date with explicit range cue (date_index={date_index})",
                                extra={'sentence': osentence, 'date_entity': date_entity.get('text', ''), 'keyword': keyword}
                            )
                            return "START_DATE"
                        else:
                            logger.debug(
                                f"[date_role] MODIFY_BOOKING: Ignoring START_DATE role keyword '{keyword}' for single date - no explicit range cue",
                                extra={'sentence': osentence, 'date_entity': date_entity.get('text', ''), 'keyword': keyword, 'dates_count': dates_count}
                            )
                            return None
        
        # Check for END_DATE role keywords (e.g., "end date", "check-out date")
        for keyword in end_role_keywords:
            pattern = rf"\b{re.escape(keyword)}\b"
            match = re.search(pattern, sentence_for_role_check, re.IGNORECASE)
            if match:
                keyword_pos = match.start()
                keyword_end = match.end()
                # Only assign role if date appears AFTER the role keyword (ignore dates before it as contextual)
                if date_char_start >= 0:
                    # Date found in sentence - check if it appears after the role keyword
                    if keyword_end <= date_char_start <= keyword_end + 150:
                        # Date appears after role keyword - assign END_DATE role
                        logger.info(
                            f"[date_role] MODIFY_BOOKING: Detected END_DATE role keyword '{keyword}' for date_index={date_index} "
                            f"(keyword at {keyword_pos}-{keyword_end}, date at {date_char_start} - date appears after keyword)",
                            extra={'sentence': osentence, 'date_entity': date_entity.get('text', ''), 'keyword': keyword}
                        )
                        return "END_DATE"
                    else:
                        # Date appears before role keyword - ignore as contextual (don't assign role)
                        logger.debug(
                            f"[date_role] MODIFY_BOOKING: Ignoring date before END_DATE role keyword '{keyword}' (date at {date_char_start}, keyword at {keyword_pos}-{keyword_end})",
                            extra={'intent': intent_name, 'date_index': date_index, 'keyword': keyword}
                        )
                        return None
                else:
                    # Date text not found - check if we have only one date (single date with role keyword)
                    # CRITICAL: For MODIFY_BOOKING, only assign role if explicit range cue exists
                    # Single dates with role keywords should NOT get roles assigned unless there's explicit range syntax
                    if dates_count == 1:
                        # For single dates, only assign role if explicit range cue is present
                        if _has_explicit_range_cue(osentence):
                            logger.info(
                                f"[date_role] MODIFY_BOOKING: Detected END_DATE role keyword '{keyword}' for single date with explicit range cue (date_index={date_index})",
                                extra={'sentence': osentence, 'date_entity': date_entity.get('text', ''), 'keyword': keyword}
                            )
                            return "END_DATE"
                        else:
                            logger.debug(
                                f"[date_role] MODIFY_BOOKING: Ignoring END_DATE role keyword '{keyword}' for single date - no explicit range cue",
                                extra={'sentence': osentence, 'date_entity': date_entity.get('text', ''), 'keyword': keyword, 'dates_count': dates_count}
                            )
                            return None
    
    # Check for START_DATE range signals (from, starting, beginning, since)
    logger.info(
        f"[date_role] Checking START_DATE signals: {start_signals}, in '{sentence_before_date}'",
        extra={'intent': intent_name, 'date_index': date_index}
    )
    for signal in start_signals:
        # Use word boundary matching to avoid false positives
        pattern = rf"\b{re.escape(signal)}\b"
        match = re.search(pattern, sentence_before_date)
        logger.debug(
            f"[date_role] Checking signal '{signal}' with pattern '{pattern}': match={bool(match)}",
            extra={'intent': intent_name,
                   'date_index': date_index, 'signal': signal}
        )
        if match:
            # For MODIFY_BOOKING: only assign if we have range syntax (two dates + range syntax)
            if is_modify_booking:
                if has_range_syntax and dates_count >= 2:
                    logger.info(
                        f"[date_role] MODIFY_BOOKING: Detected START_DATE signal '{signal}' with range syntax for date_index={date_index}",
                        extra={'sentence': osentence, 'date_entity': date_entity.get('text', ''), 'signal': signal}
                    )
                    return "START_DATE"
                else:
                    logger.debug(
                        f"[date_role] MODIFY_BOOKING: Ignoring START_DATE signal '{signal}' - no range syntax or insufficient dates",
                        extra={'intent': intent_name, 'date_index': date_index, 'has_range_syntax': has_range_syntax, 'dates_count': dates_count}
                    )
                    # Don't return - continue to check other signals
            else:
                # For CREATE_RESERVATION: use signal as-is
                logger.info(
                    f"[date_role] ✓ Detected START_DATE signal '{signal}' for date_index={date_index}",
                    extra={'sentence': osentence, 'date_entity': date_entity.get('text', ''), 'signal': signal}
                )
                return "START_DATE"

    # Check for END_DATE range signals (to, until, till, through, ending)
    logger.info(
        f"[date_role] Checking END_DATE signals: {end_signals}, in '{sentence_before_date}'",
        extra={'intent': intent_name, 'date_index': date_index}
    )
    for signal in end_signals:
        pattern = rf"\b{re.escape(signal)}\b"
        match = re.search(pattern, sentence_before_date)
        logger.debug(
            f"[date_role] Checking signal '{signal}' with pattern '{pattern}': match={bool(match)}",
            extra={'intent': intent_name,
                   'date_index': date_index, 'signal': signal}
        )
        if match:
            # For MODIFY_BOOKING: only assign if we have range syntax (two dates + range syntax)
            # CRITICAL: For single dates, "to" is ambiguous (e.g., "change X to Y" is not a date range)
            # Only assign END_DATE role if we have explicit range syntax ("from X to Y" or "between X and Y")
            if is_modify_booking:
                # For single dates (dates_count == 1), NEVER assign roles based on standalone signals like "to"
                # Standalone "to" in "change X to Y" does NOT indicate a date range
                if dates_count == 1:
                    logger.debug(
                        f"[date_role] MODIFY_BOOKING: Ignoring END_DATE signal '{signal}' - single date detected (ambiguous, not a range)",
                        extra={'intent': intent_name, 'date_index': date_index, 'signal': signal, 'dates_count': dates_count, 'osentence': osentence}
                    )
                    # Don't return - continue to check defaults, which will return None for single dates
                elif has_range_syntax and dates_count >= 2:
                    logger.info(
                        f"[date_role] MODIFY_BOOKING: Detected END_DATE signal '{signal}' with range syntax for date_index={date_index}",
                        extra={'sentence': osentence, 'date_entity': date_entity.get('text', ''), 'signal': signal}
                    )
                    return "END_DATE"
                else:
                    logger.debug(
                        f"[date_role] MODIFY_BOOKING: Ignoring END_DATE signal '{signal}' - no range syntax or insufficient dates",
                        extra={'intent': intent_name, 'date_index': date_index, 'has_range_syntax': has_range_syntax, 'dates_count': dates_count}
                    )
                    # Don't return - continue to check defaults
            else:
                # For CREATE_RESERVATION: use signal as-is
                logger.info(
                    f"[date_role] ✓ Detected END_DATE signal '{signal}' for date_index={date_index}",
                    extra={'sentence': osentence, 'date_entity': date_entity.get('text', ''), 'signal': signal}
                )
                return "END_DATE"

    # Default behavior: depends on intent
    if is_modify_booking:
        # For MODIFY_BOOKING: Never infer roles from intent alone (no default assignment)
        # Only assign if we have range syntax with two dates
        if has_range_syntax and dates_count >= 2:
            # Range syntax with two dates: assign by position
            if date_index == 0:
                logger.info(
                    f"[date_role] MODIFY_BOOKING: Default START_DATE for date_index=0 (range syntax with two dates)",
                    extra={'intent': intent_name, 'date_index': date_index}
                )
                return "START_DATE"
            elif date_index == 1:
                logger.info(
                    f"[date_role] MODIFY_BOOKING: Default END_DATE for date_index=1 (range syntax with two dates)",
                    extra={'intent': intent_name, 'date_index': date_index}
                )
                return "END_DATE"
        
        # No range syntax or insufficient dates: return None (no role assignment)
        logger.info(
            f"[date_role] MODIFY_BOOKING: No role assigned - no explicit role keyword, no range syntax, or insufficient dates",
            extra={'intent': intent_name, 'date_index': date_index, 'has_range_syntax': has_range_syntax, 'dates_count': dates_count}
        )
        return None
    else:
        # For CREATE_RESERVATION: Default by position (first = START_DATE, second = END_DATE)
        logger.info(
            f"[date_role] No signals found, using default: date_index={date_index}",
            extra={'intent': intent_name, 'date_index': date_index}
        )
        if date_index == 0:
            logger.info(
                f"[date_role] Default: returning START_DATE for date_index=0",
                extra={'intent': intent_name, 'date_index': date_index}
            )
            return "START_DATE"
        elif date_index == 1:
            logger.info(
                f"[date_role] Default: returning END_DATE for date_index=1",
                extra={'intent': intent_name, 'date_index': date_index}
            )
            return "END_DATE"

    logger.warning(
        f"[date_role] No role determined: date_index={date_index} (not 0 or 1)",
        extra={'intent': intent_name, 'date_index': date_index}
    )
    return None


def _has_explicit_range_cue(osentence: str) -> bool:
    """
    Check if sentence has explicit range cues that would justify date role assignment.
    
    Explicit cues include:
    - Range syntax: "from...to", "between...and"
    - Role keywords: "start date", "end date", "check-in date", "check-out date"
    - End signals: "until", "till", "through"
    
    Args:
        osentence: Original sentence to check
        
    Returns:
        True if explicit range cue is present, False otherwise
    """
    osentence_lower = osentence.lower()
    
    # Check for range syntax (must be complete patterns, not standalone words)
    # "from X to Y" or "between X and Y" - these indicate date ranges
    range_patterns = [
        r'\bfrom\s+.*?\s+to\s+',  # "from date1 to date2"
        r'\bbetween\s+.*?\s+and\s+'  # "between date1 and date2"
    ]
    for pattern in range_patterns:
        if re.search(pattern, osentence_lower, re.IGNORECASE):
            return True
    
    # Check for role keywords (explicit date role mentions)
    role_keywords = [
        "start date", "start_date", "check-in date", "check-in", "checkin date", "arrival date",
        "end date", "end_date", "check-out date", "check-out", "checkout date", "check out date", "departure date"
    ]
    for keyword in role_keywords:
        pattern = rf"\b{re.escape(keyword)}\b"
        if re.search(pattern, osentence_lower, re.IGNORECASE):
            return True
    
    # Check for end signals (these are range cues, but NOT standalone "to")
    # "to" alone is ambiguous (can be "change X to Y" which doesn't indicate a range)
    # Only check unambiguous end signals that clearly indicate ranges
    end_signals = ["until", "till", "through"]
    for signal in end_signals:
        pattern = rf"\b{re.escape(signal)}\b"
        if re.search(pattern, osentence_lower, re.IGNORECASE):
            return True
    
    return False


def _normalize_date_roles_for_modify_booking(
    date_roles: List[Optional[str]],
    date_refs: List[str],
    intent_name: Optional[str],
    osentence: str
) -> List[Optional[str]]:
    """
    Final normalization guard for MODIFY_BOOKING single dates.
    
    If intent == MODIFY_BOOKING AND len(date_refs) == 1 AND NOT has_explicit_range_cue,
    force date_roles = [] to prevent role leakage.
    
    This guard runs AFTER all role inference paths to ensure no downstream logic
    can reintroduce roles for single dates without explicit range cues.
    
    Args:
        date_roles: Current date_roles list (may contain START_DATE/END_DATE)
        date_refs: List of date references
        intent_name: Intent name
        osentence: Original sentence
        
    Returns:
        Normalized date_roles list (empty if MODIFY_BOOKING single date without explicit cue)
    """
    if intent_name != "MODIFY_BOOKING":
        return date_roles
    
    if len(date_refs) != 1:
        return date_roles
    
    if _has_explicit_range_cue(osentence):
        return date_roles
    
    # Guard: MODIFY_BOOKING single date without explicit range cue → force empty roles
    original_roles = date_roles.copy()
    date_roles_normalized = []
    
    logger.debug(
        f"[date_role] MODIFY_BOOKING normalization guard: Stripping roles for single date without explicit cue. "
        f"Original: {original_roles}, Normalized: {date_roles_normalized}",
        extra={'intent': intent_name, 'date_refs': date_refs, 'osentence': osentence, 'original_roles': original_roles}
    )
    
    return date_roles_normalized


def _resolve_date_semantics(
    entities: Dict[str, Any],
    structure: Dict[str, Any],
    intent_name: Optional[str] = None,
    memory_state: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Resolve date semantics with hardened, production-safe subset.

    Supports:
    - Relative days: today, tomorrow, day after tomorrow, tonight → single_day
    - Week-based: this week, next week → range
    - Weekends: this weekend, next weekend → range (Sat-Sun)
    - Specific weekdays: this Monday, next Monday, coming Friday → single_day
    - Month-relative: this month, next month → range (full month)
    - Calendar dates: 15th Dec, Dec 15, 12 July, 12/07 → single_day
      (locale ambiguous like 07/12 → flagged for clarification)
    - Simple ranges: between Monday and Wednesday → range
    - Misspellings: normalized first

    Does NOT resolve (requires clarification):
    - Plural weekdays: next Mondays → clarification
    - Vague references: sometime soon → clarification
    - Context-dependent: Thursday just gone → clarification

    Fine-grained modifiers resolve to ranges only:
    - early/mid/end of next week → range
    - early/mid/end of next month → range

    Args:
        entities: Raw extraction output
        structure: Structure interpretation result
        intent_name: Intent name (for date_role detection)

    Returns:
        Dict with "mode", "refs", "date_roles" (list of roles), and optional "needs_clarification" flag
    """
    dates = entities.get("dates", [])
    dates_absolute = entities.get("dates_absolute", [])

    # Collect date modifiers (semantic metadata; additive only)
    osentence = str(entities.get("osentence", "")).lower()
    modifier_values = entities.get("date_modifiers_vocab", [])
    modifier_values = [
        m.strip().lower()
        for m in modifier_values
        if isinstance(m, str) and m.strip()
    ]
    date_modifiers: List[str] = []
    if osentence and modifier_values:
        for mod in modifier_values:
            if isinstance(mod, str):
                if re.search(rf"\b{re.escape(mod.lower())}\b", osentence):
                    date_modifiers.append(mod.lower())

    # Normalize date texts (handle misspellings)
    normalized_dates = [_normalize_date_text(d.get("text", "")) for d in dates]
    normalized_absolute = [_normalize_date_text(
        da.get("text", "")) for da in dates_absolute]

    # Rule 1: Check for vague/ambiguous patterns that require clarification
    all_date_texts = normalized_dates + normalized_absolute
    for date_text in all_date_texts:
        if _is_vague_date_reference(date_text):
            # Will be flagged in _check_ambiguity
            pass
        if _is_plural_weekday(date_text):
            # Will be flagged in _check_ambiguity
            pass
        if _is_context_dependent(date_text):
            # Will be flagged in _check_ambiguity
            pass

    # Shorthand range completion for reservation temporal shape
    shorthand_refs = _maybe_complete_shorthand_date_range(
        entities.get("osentence", ""),
        normalized_absolute,
        intent_name
    )
    if shorthand_refs:
        # Detect date_roles for shorthand ranges
        date_roles = []
        for idx in range(len(shorthand_refs)):
            role = _detect_date_role(entities, idx, intent_name)
            date_roles.append(role)
        # Note: Shorthand ranges are always multi-date, so normalization guard doesn't apply
        return {
            "mode": DateMode.RANGE.value,
            "refs": shorthand_refs,
            "modifiers": date_modifiers,
            "date_roles": date_roles
        }

    # Rule 2: Absolute dates take precedence
    if dates_absolute:
        if len(dates_absolute) == 1:
            date_text = normalized_absolute[0]
            # Check for locale ambiguity (e.g., 07/12 could be July 12 or Dec 7)
            if _is_locale_ambiguous(date_text):
                # Will be flagged in _check_ambiguity
                pass
            print(
                "DEBUG[semantic]: RETURN ABSOLUTE_SINGLE "
                f"date_refs={[normalized_absolute[0]]} "
                f"date_modifiers={date_modifiers}"
            )
            # EARLY GUARD: MODIFY_BOOKING single date → skip role detection, force date_roles = []
            # This prevents role leakage (e.g., "to" token causing END_DATE assignment)
            # Rule-removal fix: single dates in MODIFY_BOOKING must be treated as generic "date", not "end_date" or "start_date"
            date_refs_computed = [normalized_absolute[0]]
            if intent_name == "MODIFY_BOOKING" and len(date_refs_computed) == 1:
                date_roles = []
                logger.info(
                    f"[date_role] MODIFY_BOOKING early guard: Single date detected → skipping role detection, date_roles=[]",
                    extra={'intent': intent_name, 'date_text': date_text, 'date_refs': date_refs_computed}
                )
            else:
                # For CREATE_* intents or multi-date cases: call _detect_date_role as usual
                date_role = _detect_date_role(entities, 0, intent_name)
                date_roles = [date_role] if date_role else []
            
            # Final normalization guard: MODIFY_BOOKING single date without explicit cue → force empty roles
            osentence = entities.get("osentence", "")
            date_roles = _normalize_date_roles_for_modify_booking(
                date_roles,
                [normalized_absolute[0]],
                intent_name,
                osentence
            )
            
            return {
                "mode": DateMode.SINGLE.value,
                "refs": [normalized_absolute[0]],  # Use normalized text
                "modifiers": date_modifiers,
                "date_roles": date_roles
            }
        elif len(dates_absolute) >= 2:
            # Multiple absolute dates → check for range marker
            # Detect date_roles for both dates
            date_roles = []
            for idx in range(min(2, len(normalized_absolute))):
                role = _detect_date_role(entities, idx, intent_name)
                date_roles.append(role)
            if structure.get("date_type") == DateMode.RANGE.value or structure.get("date_type") == DateMode.RANGE or "between" in str(structure).lower() or "from" in str(structure).lower():
                return {
                    "mode": DateMode.RANGE.value,
                    "refs": normalized_absolute[:2],  # Use normalized text
                    "modifiers": date_modifiers,
                    "date_roles": date_roles
                }
            else:
                # Ambiguous - will be flagged
                return {
                    "mode": DateMode.RANGE.value,  # Default to range, but flag ambiguity
                    "refs": normalized_absolute[:2],  # Use normalized text
                    "modifiers": date_modifiers,
                    "date_roles": date_roles
                }

    # Rule 3: Relative dates
    if dates:
        if len(dates) == 1:
            date_text = normalized_dates[0]

            # EARLY GUARD: MODIFY_BOOKING single date → skip role detection, force date_role = None
            # This prevents role leakage (e.g., "to" token causing END_DATE assignment)
            # Rule-removal fix: single dates in MODIFY_BOOKING must be treated as generic "date", not "end_date" or "start_date"
            date_refs_computed = [normalized_dates[0]]
            if intent_name == "MODIFY_BOOKING" and len(date_refs_computed) == 1:
                date_role = None
                logger.info(
                    f"[date_role] MODIFY_BOOKING early guard: Single date detected → skipping role detection, date_role=None",
                    extra={'intent': intent_name, 'date_text': date_text, 'date_refs': date_refs_computed}
                )
            else:
                # For CREATE_* intents or multi-date cases: call _detect_date_role as usual
                date_role = _detect_date_role(entities, 0, intent_name)

            # Check for fine-grained modifiers (early/mid/end) → always range
            if _has_fine_grained_modifier(date_text):
                single_date_roles = [date_role] if date_role else []
                osentence = entities.get("osentence", "")
                single_date_roles = _normalize_date_roles_for_modify_booking(
                    single_date_roles,
                    [normalized_dates[0]],
                    intent_name,
                    osentence
                )
                return {
                    "mode": DateMode.RANGE.value,
                    "refs": [normalized_dates[0]],  # Use normalized text
                    "modifiers": date_modifiers,
                    "date_roles": single_date_roles
                }

            # Specific weekday → single_day
            # Check this FIRST (before week-based) so "next week Wednesday" is caught correctly
            if _is_specific_weekday(date_text):
                single_date_roles = [date_role] if date_role else []
                osentence = entities.get("osentence", "")
                single_date_roles = _normalize_date_roles_for_modify_booking(
                    single_date_roles,
                    [normalized_dates[0]],
                    intent_name,
                    osentence
                )
                return {
                    "mode": DateMode.SINGLE.value,
                    "refs": [normalized_dates[0]],  # Use normalized text
                    "modifiers": date_modifiers,
                    "date_roles": single_date_roles
                }

            # Week-based → range (only if has concrete anchor)
            # Guard: Vague phrases like "next week" without weekday/date require clarification
            # Check week-based AFTER specific weekday to avoid false matches
            if _is_week_based(date_text):
                if _has_concrete_date_anchor(date_text, entities):
                    single_date_roles = [date_role] if date_role else []
                    osentence = entities.get("osentence", "")
                    single_date_roles = _normalize_date_roles_for_modify_booking(
                        single_date_roles,
                        [normalized_dates[0]],
                        intent_name,
                        osentence
                    )
                    return {
                        "mode": DateMode.RANGE.value,
                        "refs": [normalized_dates[0]],  # Use normalized text
                        "modifiers": date_modifiers,
                        "date_roles": single_date_roles
                    }
                else:
                    # No concrete anchor → don't resolve, will trigger clarification
                    # Return FLEXIBLE to indicate no resolution
                    return {
                        "mode": DateMode.FLEXIBLE.value,
                        "refs": [],
                        "modifiers": date_modifiers,
                        "date_roles": []
                    }

            # Simple relative days → single_day
            # Check this AFTER week-based to avoid false matches
            if _is_simple_relative_day(date_text):
                single_date_roles = [date_role] if date_role else []
                osentence = entities.get("osentence", "")
                single_date_roles = _normalize_date_roles_for_modify_booking(
                    single_date_roles,
                    [normalized_dates[0]],
                    intent_name,
                    osentence
                )
                return {
                    "mode": DateMode.SINGLE.value,
                    "refs": [normalized_dates[0]],  # Use normalized text
                    "modifiers": date_modifiers,
                    "date_roles": single_date_roles
                }

            # Weekend → range
            if _is_weekend_reference(date_text):
                single_date_roles = [date_role] if date_role else []
                osentence = entities.get("osentence", "")
                single_date_roles = _normalize_date_roles_for_modify_booking(
                    single_date_roles,
                    [normalized_dates[0]],
                    intent_name,
                    osentence
                )
                return {
                    "mode": DateMode.RANGE.value,
                    "refs": [normalized_dates[0]],  # Use normalized text
                    "modifiers": date_modifiers,
                    "date_roles": single_date_roles
                }

            # Month-relative → range (full month) (only if has concrete anchor)
            # Guard: Vague phrases like "next month" without weekday/date require clarification
            if _is_month_relative(date_text):
                if _has_concrete_date_anchor(date_text, entities):
                    single_date_roles = [date_role] if date_role else []
                    osentence = entities.get("osentence", "")
                    single_date_roles = _normalize_date_roles_for_modify_booking(
                        single_date_roles,
                        [normalized_dates[0]],
                        intent_name,
                        osentence
                    )
                    return {
                        "mode": DateMode.RANGE.value,
                        "refs": [normalized_dates[0]],  # Use normalized text
                        "modifiers": date_modifiers,
                        "date_roles": single_date_roles
                    }
                else:
                    # No concrete anchor → don't resolve, will trigger clarification
                    # Return FLEXIBLE to indicate no resolution
                    return {
                        "mode": DateMode.FLEXIBLE.value,
                        "refs": [],
                        "modifiers": date_modifiers,
                        "date_roles": []
                    }

            # Default: single_day
            single_date_roles = [date_role] if date_role else []
            osentence = entities.get("osentence", "")
            single_date_roles = _normalize_date_roles_for_modify_booking(
                single_date_roles,
                [normalized_dates[0]],
                intent_name,
                osentence
            )
            return {
                "mode": DateMode.SINGLE.value,
                "refs": [normalized_dates[0]],  # Use normalized text
                "modifiers": date_modifiers,
                "date_roles": single_date_roles
            }
        elif len(dates) >= 2:
            # Multiple relative dates → check for range marker
            # Detect date_roles for both dates
            date_roles = []
            for idx in range(min(2, len(normalized_dates))):
                role = _detect_date_role(entities, idx, intent_name)
                date_roles.append(role)
            
            # Check if this is a weekday-only range without anchors
            if _is_weekday_only_range(normalized_dates[:2], DateMode.RANGE.value, entities, memory_state):
                # Block resolution for weekday-only ranges without anchors
                return {
                    "mode": DateMode.FLEXIBLE.value,
                    "refs": [],
                    "modifiers": date_modifiers,
                    "date_roles": []
                }
            
            if structure.get("date_type") == DateMode.RANGE.value or structure.get("date_type") == DateMode.RANGE or "between" in str(structure).lower() or "from" in str(structure).lower():
                return {
                    "mode": DateMode.RANGE.value,
                    "refs": normalized_dates[:2],  # Use normalized text
                    "modifiers": date_modifiers,
                    "date_roles": date_roles
                }
            else:
                # Ambiguous - will be flagged
                return {
                    "mode": DateMode.RANGE.value,  # Default to range, but flag ambiguity
                    "refs": normalized_dates[:2],  # Use normalized text
                    "modifiers": date_modifiers,
                    "date_roles": date_roles
                }

    # Rule 4: Mixed absolute and relative
    if dates_absolute and dates:
        # Absolute takes precedence
        date_role = _detect_date_role(entities, 0, intent_name)
        single_date_roles = [date_role] if date_role else []
        osentence = entities.get("osentence", "")
        single_date_roles = _normalize_date_roles_for_modify_booking(
            single_date_roles,
            [normalized_absolute[0]],
            intent_name,
            osentence
        )
        return {
            "mode": DateMode.SINGLE.value,
            "refs": [normalized_absolute[0]],  # Use normalized text
            "modifiers": date_modifiers,
            "date_roles": single_date_roles
        }

    # Rule 5: No dates
    return {
        "mode": DateMode.FLEXIBLE.value,
        "refs": [],
        "modifiers": date_modifiers,
        "date_roles": []
    }


def _check_ambiguity(
    entities: Dict[str, Any],
    structure: Dict[str, Any],
    time_resolution: Dict[str, Any],
    date_resolution: Dict[str, Any]
) -> Optional[Clarification]:
    """
    Check for conflicts and ambiguity.

    Returns Clarification object if clarification is needed, None otherwise.

    Args:
        entities: Raw extraction output
        structure: Structure interpretation result
        time_resolution: Resolved time semantics
        date_resolution: Resolved date semantics

    Returns:
        Clarification object or None
    """
    debug_print(
        "DEBUG[semantic]: enter _check_ambiguity "
        f"osentence={entities.get('osentence')} "
        f"psentence={entities.get('psentence')} "
        f"date_refs={date_resolution.get('refs', [])} "
        f"date_modifiers={date_resolution.get('modifiers', [])}"
    )

    dates = entities.get("dates", [])
    dates_absolute = entities.get("dates_absolute", [])
    all_dates = dates + dates_absolute

    # Guard: conflicting modifier + relative date (e.g., "next tomorrow")
    osentence = str(entities.get("osentence", "")).lower()
    vocab = _load_vocabularies()
    modifiers = vocab.get("date_modifiers", []) if isinstance(
        vocab.get("date_modifiers", []), list) else []
    entity_types = _load_entity_types()
    relative_defs = entity_types.get("date", {}).get("relative", [])
    relative_values = {rd.get("value", "").lower()
                       for rd in relative_defs if isinstance(rd, dict) and rd.get("value")}
    if osentence and modifiers and relative_values:
        found_modifier = None
        for mod in modifiers:
            if mod and mod.lower() in osentence:
                found_modifier = mod.lower()
                break
        if found_modifier:
            for date_entity in all_dates:
                date_text = _normalize_date_text(date_entity.get("text", ""))
                if date_text in relative_values:
                    return Clarification(
                        reason=ClarificationReason.CONFLICTING_SIGNALS,
                        data={
                            "modifier": found_modifier,
                            "date": date_text
                        }
                    )

    # Check for weekday-only range without anchors (must check before other ambiguity checks)
    # This check must override other reasons to ensure consistent normalization
    # Check entities directly since date_resolution may have been set to FLEXIBLE for weekday-only ranges
    dates = entities.get("dates", [])
    if len(dates) >= 2:
        # Extract date texts from entities (before normalization/modification)
        date_texts = [d.get("text", "") for d in dates[:2]]
        # Check if this is a weekday-only range without anchors
        # Use DateMode.RANGE.value since we're checking if it should be a range
        # Note: Luma is stateless (memory_state is always None), but we can still check for anchors in entities
        if _is_weekday_only_range(date_texts, DateMode.RANGE.value, entities, None):
            # Normalize missing slots: both start_date and end_date must be marked as missing
            # This overrides any partial issues to ensure complete clarification output
            return Clarification(
                reason=ClarificationReason.MISSING_DATE_RANGE,
                data={
                    "missing_slots": ["start_date", "end_date"]
                }
            )

    # Check for vague date references
    for date_entity in all_dates:
        date_text = _normalize_date_text(date_entity.get("text", ""))
        if _is_vague_date_reference(date_text):
            return Clarification(
                reason=ClarificationReason.VAGUE_DATE_REFERENCE,
                data={
                    "date_text": date_entity.get("text")
                }
            )
        if _is_plural_weekday(date_text):
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_PLURAL_WEEKDAY,
                data={
                    "date_text": date_entity.get("text")
                }
            )
        if _is_context_dependent(date_text):
            return Clarification(
                reason=ClarificationReason.CONTEXT_DEPENDENT_DATE,
                data={
                    "date_text": date_entity.get("text")
                }
            )
        # Bare weekday (no modifier) is context-dependent, not conflicting
        if _is_bare_weekday(date_text) and not ALLOW_BARE_WEEKDAY_BINDING:
            return Clarification(
                reason=ClarificationReason.CONTEXT_DEPENDENT_DATE,
                data={
                    "weekday": date_text
                }
            )

    # Check for locale-ambiguous dates
    for date_entity in dates_absolute:
        date_text = _normalize_date_text(date_entity.get("text", ""))
        if _is_locale_ambiguous(date_text):
            return Clarification(
                reason=ClarificationReason.LOCALE_AMBIGUOUS_DATE,
                data={
                    "date_text": date_entity.get("text")
                }
            )

    # Check structure-level ambiguity flag
    if structure.get("needs_clarification", False):
        return Clarification(
            reason=ClarificationReason.CONFLICTING_SIGNALS,
            data={"structure": structure}
        )

    # Check for conflicting dates
    if dates_absolute and dates:
        # Absolute takes precedence, not ambiguous
        pass
    elif len(dates_absolute) > 1:
        # Multiple absolute dates
        if date_resolution["mode"] == DateMode.RANGE.value and not structure.get("needs_clarification"):
            # Valid range, no ambiguity
            pass
        else:
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_DATE_MULTIPLE,
                data={
                    "date_count": len(dates_absolute),
                    "dates": [d.get("text") for d in dates_absolute]
                }
            )
    elif len(dates) > 1:
        # Multiple relative dates
        if date_resolution["mode"] == DateMode.RANGE.value and not structure.get("needs_clarification"):
            # Valid range, no ambiguity
            pass
        else:
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_DATE_MULTIPLE,
                data={
                    "date_count": len(dates),
                    "dates": [d.get("text") for d in dates]
                }
            )

    # Check for multiple times without range
    times = entities.get("times", [])
    time_windows = entities.get("time_windows", [])
    services = entities.get("business_categories") or entities.get(
        "service_families", [])
    total_dates = len(dates) + len(dates_absolute)

    # BUG 3 FIX: Check for bare hours (without am/pm or time window)
    # This check must happen BEFORE the "exact time exists" rule
    # Also check for fuzzy hours without time window - these need clarification too
    for time_entity in times:
        time_text = time_entity.get("text", "")
        # Skip fuzzy hours (they're handled as ranges in time resolution)
        if _is_fuzzy_hour(time_text):
            # If fuzzy hour has no time window, it's ambiguous (can't determine am/pm)
            if not time_windows:
                return Clarification(
                    reason=ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW,
                    data={
                        "time": time_text
                    }
                )
        # Check for bare hours (just digits, no am/pm)
        elif _is_bare_hour(time_text) and not time_windows:
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW,
                data={
                    "time": time_text
                }
            )

    # Rule: Do NOT trigger clarification when:
    # - There is exactly one service
    # - Exactly one date
    # - Exactly one exact time (that is NOT a bare hour - already checked above)
    # Even if a time window is also present
    if len(services) == 1 and total_dates == 1 and len(times) == 1:
        # Exact time exists and is not bare (bare hours already handled above), so no clarification needed
        pass
    elif len(times) > 1:
        if time_resolution["mode"] != TimeMode.RANGE.value:
            # Use first time for template rendering
            first_time = times[0].get("text", "") if times else ""
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW,
                data={
                    "time": first_time
                }
            )

    # No clarification needed
    return None


def _check_unresolved_weekday_patterns(
    intent_result: Dict[str, Any],
    date_resolution: Dict[str, Any],
    entities: Dict[str, Any]
) -> Optional[Clarification]:
    """
    Guard: Detect weekday-like patterns that weren't normalized.

    If ALL are true:
    - Intent = CREATE_BOOKING
    - Date refs is empty AFTER normalization
    - Date entities exist but contain weekday-like patterns that weren't recognized

    Then return clarification to prevent silent failure.

    Do NOT attempt to guess the date.
    """
    intent = intent_result.get("intent")

    # Check if intent has temporal shape from IntentRegistry (sole policy source)
    registry = get_intent_registry()
    intent_meta = registry.get(intent) if intent else None
    has_temporal_shape = intent_meta and intent_meta.temporal_shape is not None

    if not has_temporal_shape:
        return None

    # Check if date_refs is empty
    date_refs = date_resolution.get("refs", [])
    if date_refs:
        return None  # Date was resolved, no issue

    # Check if date_mode is flexible (meaning no dates were found)
    date_mode = date_resolution.get("mode", DateMode.FLEXIBLE.value)
    if date_mode != DateMode.FLEXIBLE.value:
        return None  # Date mode was set, so something was found

    # Get date entities
    dates = entities.get("dates", [])
    dates_absolute = entities.get("dates_absolute", [])
    all_dates = dates + dates_absolute

    # If no date entities at all, nothing to check
    if not all_dates:
        return None

    # Check for weekday-like patterns in date entities that weren't normalized
    # Pattern: "this <word>" or "next <word>" where word looks like a weekday but isn't recognized
    weekday_pattern = re.compile(
        r"\b(this|next)\s+([a-z]{4,10})\b",
        re.IGNORECASE
    )

    # Load known weekdays from vocabularies (canonical + all variants)
    vocab = _load_vocabularies()
    weekdays_dict = vocab.get("weekdays", {})
    known_weekdays = list(weekdays_dict.keys()) if isinstance(
        weekdays_dict, dict) else []
    # Also include all variants
    for _canonical, variants in weekdays_dict.items():
        if isinstance(variants, list):
            known_weekdays.extend(variants)

    for date_entity in all_dates:
        date_text = date_entity.get("text", "").lower()
        match = weekday_pattern.search(date_text)
        if match:
            potential_weekday = match.group(2).lower()
            # If it's NOT a known weekday (meaning normalization failed or typo not in config)
            if potential_weekday not in known_weekdays:
                # Check if it's weekday-like (ends with 'day' or contains weekday fragments)
                is_weekday_like = (
                    potential_weekday.endswith("day") or
                    any(fragment in potential_weekday for fragment in [
                        "mon", "tue", "wed", "thu", "fri", "sat", "sun"])
                )
                if is_weekday_like:
                    return Clarification(
                        reason=ClarificationReason.CONTEXT_DEPENDENT_DATE,
                        data={
                            "date_text": date_entity.get("text"),
                            "reason": "unresolved_weekday_typo"
                        }
                    )

    return None
