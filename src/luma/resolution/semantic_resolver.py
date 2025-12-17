"""
Semantic Resolver

Resolves semantic meaning from extracted entities and grouped intents.
Decides what the user means without binding to actual calendar dates.

This layer answers: "What does the user mean?"
NOT: "What actual dates does this correspond to?"
"""
# Provenance marker to verify loaded module version
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
import re
from typing import Dict, Any, Optional, Tuple, Set, List
from pathlib import Path
from dataclasses import dataclass
print("DEBUG: semantic_resolver loaded from", __file__)


def _get_global_config_path() -> Path:
    """Get path to global normalization config JSON."""
    # Try multiple possible locations
    # From semantic_resolver.py: parent.parent = luma/, so luma/store/normalization/
    possible_paths = [
        Path(__file__).parent.parent / "store" /
        "normalization" / "global.v2.json",
        Path(__file__).parent.parent / "store" /
        "normalization" / "global.v1.json",
    ]
    for path in possible_paths:
        if path.exists():
            return path
    # Fallback - assume standard location
    return Path(__file__).parent.parent / "store" / "normalization" / "global.v2.json"


# Lazy-loaded config (loaded on first use)
_VOCAB_CACHE: Dict[str, Any] = {}
# (synonym_map, typo_map, all_canonicals)
_VOCAB_MAPS_CACHE: Tuple[Dict[str, str], Dict[str, str], Set[str]] = None
_ENTITY_TYPES_CACHE: Dict[str, Any] = {}


def _load_vocabularies() -> Dict[str, Any]:
    """Load and cache vocabularies from JSON."""
    if not _VOCAB_CACHE:
        config_path = _get_global_config_path()
        _VOCAB_CACHE.update(load_global_vocabularies(config_path))
    return _VOCAB_CACHE


def _load_entity_types() -> Dict[str, Any]:
    """Load and cache entity_types from JSON."""
    if not _ENTITY_TYPES_CACHE:
        config_path = _get_global_config_path()
        _ENTITY_TYPES_CACHE.update(load_global_entity_types(config_path))
    return _ENTITY_TYPES_CACHE


def _load_vocabulary_maps() -> Tuple[Dict[str, str], Dict[str, str], Set[str]]:
    """Load and cache vocabulary maps (synonyms and typos) from JSON."""
    global _VOCAB_MAPS_CACHE
    if _VOCAB_MAPS_CACHE is None:
        config_path = _get_global_config_path()
        vocabularies = load_vocabularies(config_path)
        _VOCAB_MAPS_CACHE = compile_vocabulary_maps(vocabularies)
    return _VOCAB_MAPS_CACHE


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


def resolve_semantics(
    intent_result: Dict[str, Any],
    entities: Dict[str, Any]
) -> SemanticResolutionResult:
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

    # Resolve time semantics
    time_resolution = _resolve_time_semantics(entities, structure)

    # Resolve date semantics
    date_resolution = _resolve_date_semantics(entities, structure)

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
            resolved_booking = {
                "services": booking.get("services", []),
                "date_mode": date_resolution["mode"],
                "date_refs": date_resolution["refs"],
                "date_modifiers": date_modifiers,
                "time_mode": time_resolution["mode"],
                "time_refs": time_resolution["refs"],
                "duration": booking.get("duration"),
            }
            return SemanticResolutionResult(
                resolved_booking=resolved_booking,
                needs_clarification=True,
                clarification=clarification,
            )

    # Extract duration
    duration = booking.get("duration")

    # Check for conflicts and ambiguity
    clarification = _check_ambiguity(
        entities, structure, time_resolution, date_resolution
    )

    # Guard: Check for weekday-like patterns that weren't normalized
    # This prevents silent failure when normalization doesn't succeed
    if clarification is None:
        clarification = _check_unresolved_weekday_patterns(
            intent_result, date_resolution, entities
        )

    resolved_booking = {
        "services": services,
        "date_mode": date_resolution["mode"],
        "date_refs": date_resolution["refs"],
        "date_modifiers": date_resolution.get("modifiers", []),
        "time_mode": time_resolution["mode"],
        "time_refs": time_resolution["refs"],
        "duration": duration
    }

    return SemanticResolutionResult(
        resolved_booking=resolved_booking,
        needs_clarification=clarification is not None,
        clarification=clarification
    )


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


def _resolve_time_semantics(
    entities: Dict[str, Any],
    structure: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Resolve time semantics following precedence rules.

    Precedence: exact > range > window > none

    Args:
        entities: Raw extraction output
        structure: Structure interpretation result

    Returns:
        Dict with "mode" and "refs" keys
    """
    times = entities.get("times", [])
    time_windows = entities.get("time_windows", [])
    time_type = structure.get("time_type", "none")

    # Rule 1: Window + exact time - exact time wins, window is discarded
    # Example: "tomorrow morning at 9am" → resolves to 9am, not morning window
    # Exact time always overrides time windows for appointment booking
    if time_windows and times:
        # Discard windows, use only exact time
        return {
            "mode": "exact",
            "refs": [times[0].get("text")]  # Only exact time, no windows
        }

    # Rule 1.5: Fuzzy hours (6ish, around 6) → treat as range ONLY if time window exists
    # If no time window, flag as ambiguous (consistent with bare hour policy)
    if times:
        for time_entity in times:
            time_text = time_entity.get("text", "")
            if _is_fuzzy_hour(time_text):
                # Extract hour from fuzzy pattern
                hour_match = re.search(r'(\d+)', time_text)
                if hour_match and time_windows:
                    # Time window provides context, treat as range
                    hour = int(hour_match.group(1))
                    return {
                        "mode": "range",
                        "refs": [f"{hour}:00", f"{hour+1}:00"]
                    }
                # If no time window, will be flagged as ambiguous in _check_ambiguity

    # Rule 2: Range (between X and Y) - check before single exact time
    if time_type == "range" and times:
        return {
            "mode": "range",
            "refs": [t.get("text") for t in times[:2]]
        }

    # Rule 3: Exact time wins (if present, single or multiple)
    if times:
        if len(times) == 1:
            return {
                "mode": "exact",
                "refs": [times[0].get("text")]
            }
        elif len(times) >= 2:
            # Multiple times without range marker → ambiguity
            return {
                "mode": "exact",  # Default to exact, but flag ambiguity later
                "refs": [t.get("text") for t in times]
            }

    # Rule 4: Window (coarse time ranges) - only if no exact time
    if time_windows:
        return {
            "mode": "window",
            "refs": [tw.get("text") for tw in time_windows]
        }

    # Rule 5: None
    return {
        "mode": "none",
        "refs": []
    }


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


def _resolve_date_semantics(
    entities: Dict[str, Any],
    structure: Dict[str, Any]
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

    Returns:
        Dict with "mode", "refs", and optional "needs_clarification" flag
    """
    print(
        "DEBUG[semantic]: enter _resolve_date_semantics "
        f"keys={list(entities.keys())} "
        f"osentence={entities.get('osentence')} "
        f"psentence={entities.get('psentence')}"
    )
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
    print(
        "DEBUG[semantic]: modifier_values raw =",
        modifier_values,
        type(modifier_values)
    )
    date_modifiers: List[str] = []
    if osentence and modifier_values:
        for mod in modifier_values:
            if isinstance(mod, str):
                if re.search(rf"\b{re.escape(mod.lower())}\b", osentence):
                    date_modifiers.append(mod.lower())
    print(
        "DEBUG[semantic]: after modifiers "
        f"sentence={osentence} "
        f"date_modifiers={date_modifiers}"
    )

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
            return {
                "mode": "single_day",
                "refs": [normalized_absolute[0]],  # Use normalized text
                "modifiers": date_modifiers
            }
        elif len(dates_absolute) >= 2:
            # Multiple absolute dates → check for range marker
            if structure.get("date_type") == "range" or "between" in str(structure).lower() or "from" in str(structure).lower():
                print(
                    "DEBUG[semantic]: RETURN ABSOLUTE_RANGE "
                    f"date_refs={normalized_absolute[:2]} "
                    f"date_modifiers={date_modifiers}"
                )
                return {
                    "mode": "range",
                    "refs": normalized_absolute[:2],  # Use normalized text
                    "modifiers": date_modifiers
                }
            else:
                # Ambiguous - will be flagged
                print(
                    "DEBUG[semantic]: RETURN ABSOLUTE_RANGE_AMBIG "
                    f"date_refs={normalized_absolute[:2]} "
                    f"date_modifiers={date_modifiers}"
                )
                return {
                    "mode": "range",  # Default to range, but flag ambiguity
                    "refs": normalized_absolute[:2],  # Use normalized text
                    "modifiers": date_modifiers
                }

    # Rule 3: Relative dates
    if dates:
        if len(dates) == 1:
            date_text = normalized_dates[0]

            # Check for fine-grained modifiers (early/mid/end) → always range
            if _has_fine_grained_modifier(date_text):
                print(
                    "DEBUG[semantic]: RETURN RELATIVE_FINE_GRAINED "
                    f"date_refs={[normalized_dates[0]]} "
                    f"date_modifiers={date_modifiers}"
                )
                return {
                    "mode": "range",
                    "refs": [normalized_dates[0]],  # Use normalized text
                    "modifiers": date_modifiers
                }

            # Simple relative days → single_day
            if _is_simple_relative_day(date_text):
                print(
                    "DEBUG[semantic]: RETURN RELATIVE_SIMPLE "
                    f"date_refs={[normalized_dates[0]]} "
                    f"date_modifiers={date_modifiers}"
                )
                return {
                    "mode": "single_day",
                    "refs": [normalized_dates[0]],  # Use normalized text
                    "modifiers": date_modifiers
                }

            # Week-based → range
            if _is_week_based(date_text):
                print(
                    "DEBUG[semantic]: RETURN RELATIVE_WEEK_BASED "
                    f"date_refs={[normalized_dates[0]]} "
                    f"date_modifiers={date_modifiers}"
                )
                return {
                    "mode": "range",
                    "refs": [normalized_dates[0]],  # Use normalized text
                    "modifiers": date_modifiers
                }

            # Weekend → range
            if _is_weekend_reference(date_text):
                print(
                    "DEBUG[semantic]: RETURN RELATIVE_WEEKEND "
                    f"date_refs={[normalized_dates[0]]} "
                    f"date_modifiers={date_modifiers}"
                )
                return {
                    "mode": "range",
                    "refs": [normalized_dates[0]],  # Use normalized text
                    "modifiers": date_modifiers
                }

            # Specific weekday → single_day
            if _is_specific_weekday(date_text):
                print(
                    "DEBUG[semantic]: RETURN RELATIVE_WEEKDAY "
                    f"date_refs={[normalized_dates[0]]} "
                    f"date_modifiers={date_modifiers}"
                )
                return {
                    "mode": "single_day",
                    "refs": [normalized_dates[0]],  # Use normalized text
                    "modifiers": date_modifiers
                }

            # Month-relative → range (full month)
            if _is_month_relative(date_text):
                print(
                    "DEBUG[semantic]: RETURN RELATIVE_MONTH "
                    f"date_refs={[normalized_dates[0]]} "
                    f"date_modifiers={date_modifiers}"
                )
                return {
                    "mode": "range",
                    "refs": [normalized_dates[0]],  # Use normalized text
                    "modifiers": date_modifiers
                }

            # Default: single_day
            print(
                "DEBUG[semantic]: RETURN RELATIVE_DEFAULT "
                f"date_refs={[normalized_dates[0]]} "
                f"date_modifiers={date_modifiers}"
            )
            return {
                "mode": "single_day",
                "refs": [normalized_dates[0]],  # Use normalized text
                "modifiers": date_modifiers
            }
        elif len(dates) >= 2:
            # Multiple relative dates → check for range marker
            if structure.get("date_type") == "range" or "between" in str(structure).lower() or "from" in str(structure).lower():
                print(
                    "DEBUG[semantic]: RETURN RELATIVE_MULTI_RANGE "
                    f"date_refs={normalized_dates[:2]} "
                    f"date_modifiers={date_modifiers}"
                )
                return {
                    "mode": "range",
                    "refs": normalized_dates[:2],  # Use normalized text
                    "modifiers": date_modifiers
                }
            else:
                # Ambiguous - will be flagged
                print(
                    "DEBUG[semantic]: RETURN RELATIVE_MULTI_AMBIG "
                    f"date_refs={normalized_dates[:2]} "
                    f"date_modifiers={date_modifiers}"
                )
                return {
                    "mode": "range",  # Default to range, but flag ambiguity
                    "refs": normalized_dates[:2],  # Use normalized text
                    "modifiers": date_modifiers
                }

    # Rule 4: Mixed absolute and relative
    if dates_absolute and dates:
        # Absolute takes precedence
        print(
            "DEBUG[semantic]: RETURN MIXED_ABSOLUTE_RELATIVE "
            f"date_refs={[normalized_absolute[0]]} "
            f"date_modifiers={date_modifiers}"
        )
        return {
            "mode": "single_day",
            "refs": [normalized_absolute[0]],  # Use normalized text
            "modifiers": date_modifiers
        }

    # Rule 5: No dates
    print(
        "DEBUG[semantic]: RETURN NO_DATES "
        f"date_refs=[] "
        f"date_modifiers={date_modifiers}"
    )
    return {
        "mode": "flexible",
        "refs": [],
        "modifiers": date_modifiers
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

    # Check for vague date references
    for date_entity in all_dates:
        date_text = _normalize_date_text(date_entity.get("text", ""))
        if _is_vague_date_reference(date_text):
            return Clarification(
                reason=ClarificationReason.VAGUE_DATE_REFERENCE,
                data={"date_text": date_entity.get("text")}
            )
        if _is_plural_weekday(date_text):
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_PLURAL_WEEKDAY,
                data={"date_text": date_entity.get("text")}
            )
        if _is_context_dependent(date_text):
            return Clarification(
                reason=ClarificationReason.CONTEXT_DEPENDENT_DATE,
                data={"date_text": date_entity.get("text")}
            )
        # Bare weekday (no modifier) is context-dependent, not conflicting
        if _is_bare_weekday(date_text):
            return Clarification(
                reason=ClarificationReason.CONTEXT_DEPENDENT_DATE,
                data={"weekday": date_text}
            )

    # Check for locale-ambiguous dates
    for date_entity in dates_absolute:
        date_text = _normalize_date_text(date_entity.get("text", ""))
        if _is_locale_ambiguous(date_text):
            return Clarification(
                reason=ClarificationReason.LOCALE_AMBIGUOUS_DATE,
                data={"date_text": date_entity.get("text")}
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
        if date_resolution["mode"] == "range" and not structure.get("needs_clarification"):
            # Valid range, no ambiguity
            pass
        else:
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_DATE_MULTIPLE,
                data={"date_count": len(dates_absolute), "dates": [
                    d.get("text") for d in dates_absolute]}
            )
    elif len(dates) > 1:
        # Multiple relative dates
        if date_resolution["mode"] == "range" and not structure.get("needs_clarification"):
            # Valid range, no ambiguity
            pass
        else:
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_DATE_MULTIPLE,
                data={"date_count": len(dates), "dates": [
                    d.get("text") for d in dates]}
            )

    # Check for multiple times without range
    times = entities.get("times", [])
    time_windows = entities.get("time_windows", [])
    services = entities.get("service_families", [])
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
                    data={"time": time_text}
                )
        # Check for bare hours (just digits, no am/pm)
        elif _is_bare_hour(time_text) and not time_windows:
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW,
                data={"time": time_text}
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
        if time_resolution["mode"] != "range":
            # Use first time for template rendering
            first_time = times[0].get("text", "") if times else ""
            return Clarification(
                reason=ClarificationReason.AMBIGUOUS_TIME_NO_WINDOW,
                data={"time": first_time}
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
    if intent != "CREATE_BOOKING":
        return None

    # Check if date_refs is empty
    date_refs = date_resolution.get("refs", [])
    if date_refs:
        return None  # Date was resolved, no issue

    # Check if date_mode is flexible (meaning no dates were found)
    date_mode = date_resolution.get("mode", "flexible")
    if date_mode != "flexible":
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
                        data={"date_text": date_entity.get(
                            "text"), "reason": "unresolved_weekday_typo"}
                    )

    return None
