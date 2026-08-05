"""
NLU Pipeline — two-stage extraction + normalisation + calendar binding.

Stage 1 (intent):   Stage1IntentExtractor — lightweight LLM call, intent + confidence only
                    (proposal / prior; Stage 2 is semantic authority)
Stage 2 (slots):    Stage2 dispatcher → per-group slot extractor (LLM, focused prompt)
                    Shared Intent Validation Contract → validated_intent; then extract.
                    UNKNOWN routes to create; validated_intent may re-route intent.
Stage 3 (slots):    When validated_intent maps to a different group, re-run that extractor.
Normalisation:      Rule-based post-processing (date, time, booking_id, alias)
Calendar binding:   ISO-8601 date resolution via calendar_binder

Backward-compat shim: `_slm_extract` delegates to the two-stage path so existing
tests and callers that patch `_slm_extract` continue to work unchanged.
"""
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .catalog import infer_service_term_from_utterance, resolve_service
from .stages.stage1.extractor import Stage1IntentExtractor
from .stages.stage2.dispatcher import extract_slots as _stage2_extract

logger = logging.getLogger(__name__)

_WEEKDAYS = {
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
}

_WEEKDAY_TO_DOW = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_MONTHS = {
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
}

_DATE_TIME_TOKENS = _WEEKDAYS | _MONTHS | {
    "today", "tomorrow", "yesterday", "next", "this", "last", "weekend",
    "morning", "afternoon", "evening", "night", "noon", "midnight",
    "am", "pm",
}

_RELATIVE_DATE_PHRASES = (
    "today", "tomorrow", "yesterday", "next week", "this weekend", "next weekend",
    "this friday", "next friday", "this monday", "next monday",
    "this tuesday", "next tuesday", "this wednesday", "next wednesday",
    "this thursday", "next thursday", "this saturday", "next saturday",
    "this sunday", "next sunday",
)

_MONTH_PATTERN = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)"
)
# Month-first ("July 24", "July 24th") or day-first ("24 July", "24th July").
_DATE_SINGLE_RE = re.compile(
    rf"""
    \b
    (?:
        {_MONTH_PATTERN}\s+\d{{1,2}}(?:st|nd|rd|th)?
      |
        \d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH_PATTERN}
    )
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_NUMERIC_DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")
_BOOKING_VERB_RE = re.compile(r"\b(?:book|reserve|schedule)\b", re.IGNORECASE)
_RESERVATION_NOUN_RE = re.compile(
    r"\b(?:room|suite|accommodation|stay|deluxe|delux)\b", re.IGNORECASE
)


def _text_mentions_date(text: str) -> bool:
    """True when the utterance explicitly mentions a calendar date (not bare clock time)."""
    from .extraction.entity_loading import normalize_utterance_aliases

    # Share global vocabulary typos/aliases (tomorow→tomorrow) with Stage2 config.
    lowered = normalize_utterance_aliases(text).lower()
    for phrase in _RELATIVE_DATE_PHRASES:
        if phrase in lowered:
            return True
    words = lowered.split()
    if any(w.strip(".,!?") in _WEEKDAYS for w in words):
        return True
    if _DATE_SINGLE_RE.search(lowered):
        return True
    if _NUMERIC_DATE_RE.search(lowered):
        return True
    if re.search(r"\b(?:next|this|last)\s+\w+\b", lowered):
        return True
    return False


_FUZZY_TIME_TOKENS = {"morning", "afternoon", "evening", "night"}
_TIME_PERIOD_TOKENS = _FUZZY_TIME_TOKENS | {"noon", "midnight"}

# Explicit clock-time patterns: "3pm", "3:30 am", "15:00", "9am", "1.30pm"
# Dotted minutes without am/pm are NOT matched here (avoids "price is 1.30");
# bare dotted replies like "1.30" are covered by _BARE_CLOCK_UTTERANCE_RE.
_EXPLICIT_CLOCK_RE = re.compile(
    r"\b\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)\b|\b\d{2}:\d{2}\b",
    re.IGNORECASE,
)
# "at 9", "by 12", "around 3:30", "until 5", "at 1.30"
_PREPOSITIONAL_HOUR_RE = re.compile(
    r"\b(?:at|by|around|before|after|until|till|from)\s+\d{1,2}(?:[:.]\d{2})?\b",
    re.IGNORECASE,
)
_OCLOCK_RE = re.compile(r"\b\d{1,2}\s*o'?clock\b", re.IGNORECASE)
# Bare hour / HH:MM / H.MM as the whole utterance (slot-fill: "9", "9:30", "1.30").
# Optional am/pm covers "1.30pm" / "3pm" as the entire reply.
_BARE_CLOCK_UTTERANCE_RE = re.compile(
    r"^\s*\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?\s*$",
    re.IGNORECASE,
)


def _text_mentions_time(text: str) -> bool:
    """True when the utterance explicitly mentions a clock or time-of-day."""
    lowered = (text or "").lower()
    if _EXPLICIT_CLOCK_RE.search(lowered):
        return True
    if _PREPOSITIONAL_HOUR_RE.search(lowered):
        return True
    if _OCLOCK_RE.search(lowered):
        return True
    if _BARE_CLOCK_UTTERANCE_RE.match(lowered):
        return True
    words = re.sub(r"[^\w\s]", " ", lowered).split()
    if any(w in _TIME_PERIOD_TOKENS for w in words):
        return True
    return False


def _normalize_fuzzy_time(text: str, slm: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure fuzzy time-window tokens produce fuzzy Temporal (and legacy projection).

    Haiku sometimes emits a concrete time like "20:00" when the user says "evening".
    That concrete time gets promoted to slots.time and satisfies the time slot too
    early, causing READY when NEEDS_CLARIFICATION is expected (scenario 21).

    When an explicit clock time is also present ("evening at 7pm"), leave the SLM
    output unchanged — the clock time takes precedence.
    """
    from .temporal.pipeline_sync import apply_temporal, get_temporal

    text_lower = text.lower()

    found_token = None
    for token in _FUZZY_TIME_TOKENS:
        if re.search(rf"\b{token}s?\b", text_lower):
            found_token = token
            break

    if not found_token:
        return slm

    # Explicit clock time present → let it win; don't override
    if _EXPLICIT_CLOCK_RE.search(text_lower):
        return slm

    temporal = get_temporal(slm)
    # Already correct: fuzzy with right label and no spurious clock times
    if (
        (temporal.start_time_expression or "").lower() == found_token
        and not temporal.start_time
        and not temporal.end_time
    ):
        return slm

    temporal.start_time = None
    temporal.end_time = None
    temporal.start_time_expression = found_token
    temporal.end_time_expression = None
    logger.debug(
        "_normalize_fuzzy_time: text=%r token=%r (temporal-primary)",
        text,
        found_token,
    )
    return apply_temporal(slm, temporal)


def _tokenize_utterance(text: str) -> List[str]:
    """Lowercase tokenization for utterance / service_term grounding checks."""
    return re.sub(r"[^\w\s]", " ", (text or "").lower()).split()


# Filler tokens stripped before checking whether a service_term is grounded in text.
_SERVICE_GROUNDING_NOISE = _DATE_TIME_TOKENS | frozenset({
    "a", "an", "the", "to", "for", "me", "my", "i", "want", "need", "please",
    "switch", "change", "instead", "actually", "book", "schedule", "reserve",
})


def _ground_service_term_in_text(
    text: str, service_term: Optional[str]
) -> Optional[str]:
    """Keep only service tokens explicitly present in the current utterance.

    Returns None when no service token from *service_term* appears in *text*.
    Partial hallucinations (e.g. service_term='premium haircut' on utterance
    'premium') collapse to the grounded subset ('premium').
    """
    if not service_term or not str(service_term).strip():
        return None

    text_tokens = _tokenize_utterance(text)
    if not text_tokens:
        return None

    text_joined = " ".join(text_tokens)
    term_tokens = [
        t
        for t in _tokenize_utterance(str(service_term))
        if t not in _SERVICE_GROUNDING_NOISE
    ]
    if not term_tokens:
        return None

    term_norm = " ".join(term_tokens)
    if term_norm and term_norm in text_joined:
        return term_norm

    grounded = [t for t in term_tokens if t in text_tokens]
    if not grounded:
        return None
    if len(grounded) == len(term_tokens):
        return term_norm
    return " ".join(grounded)


def _text_mentions_service(
    text: str,
    service_term: Optional[str] = None,
    facts_service_id: Optional[str] = None,
) -> bool:
    """True when the current utterance explicitly mentions a service."""
    if _ground_service_term_in_text(text, service_term):
        return True
    if not facts_service_id:
        return False
    text_tokens = set(_tokenize_utterance(text))
    for token in _tokenize_utterance(str(facts_service_id)):
        if token not in _SERVICE_GROUNDING_NOISE and token in text_tokens:
            return True
    return False


def _strip_unmentioned_service(text: str, slm: Dict[str, Any]) -> Dict[str, Any]:
    """Remove service slots Haiku inferred from conversation context alone.

    Mirrors _strip_unmentioned_dates: booking services must appear in the
    current utterance. Core owns session stickiness via merge + resolved_service_id.
    """
    facts = slm.get("facts") or {}
    service_term = slm.get("service_term")
    facts_service_id = facts.get("service_id")

    if not service_term and not facts_service_id:
        return slm

    if _text_mentions_service(text, service_term, facts_service_id):
        grounded = _ground_service_term_in_text(text, service_term)
        if service_term and grounded != service_term:
            logger.debug(
                "Grounding service_term in utterance text=%r service_term=%r → %r",
                text,
                service_term,
                grounded,
            )
            return {**slm, "service_term": grounded}
        return slm

    logger.debug(
        "Stripping unmentioned service from SLM output for text=%r "
        "service_term=%r facts.service_id=%r",
        text,
        service_term,
        facts_service_id,
    )
    updated = {**slm, "service_term": None, "service_candidates": []}
    if facts_service_id is not None:
        updated["facts"] = {**facts, "service_id": None}
    return updated


def _strip_unmentioned_dates(text: str, slm: Dict[str, Any]) -> Dict[str, Any]:
    """Remove dates Haiku invented when the user only mentioned a clock time."""
    from .temporal.pipeline_sync import (
        apply_temporal,
        clear_temporal_dates,
        get_temporal,
        temporal_has_date_material,
    )

    if _text_mentions_date(text):
        return slm
    temporal = get_temporal(slm)
    facts = slm.get("facts", {})
    if not temporal_has_date_material(temporal) and not facts.get("date_time_pairs"):
        return slm
    # Time-only utterance: never keep invented dates (even if date_time_pairs set).
    logger.debug(
        "Stripping unmentioned dates from SLM output for text=%r temporal=%r",
        text,
        temporal.to_dict(),
    )
    return apply_temporal(slm, clear_temporal_dates(temporal))


def _strip_unmentioned_times(text: str, slm: Dict[str, Any]) -> Dict[str, Any]:
    """Remove times Haiku inferred from conversation context alone.

    Mirrors _strip_unmentioned_dates: clock / period language must appear in the
    current utterance. Core owns session time stickiness and availability rematch.
    """
    from .temporal.pipeline_sync import (
        apply_temporal,
        clear_temporal_times,
        get_temporal,
        temporal_has_time_material,
    )

    if _text_mentions_time(text):
        return slm
    temporal = get_temporal(slm)
    facts = slm.get("facts", {}) or {}
    if (
        not temporal_has_time_material(temporal)
        and not facts.get("times")
        and not slm.get("time_constraint")
    ):
        return slm
    logger.debug(
        "Stripping unmentioned times from SLM output for text=%r temporal=%r",
        text,
        temporal.to_dict(),
    )
    return apply_temporal(slm, clear_temporal_times(temporal))


def _apply_booking_mode_intent(
    text: str, slm: Dict[str, Any], tenant_context: Dict[str, Any]
) -> Dict[str, Any]:
    """Upgrade reservation intents when booking_mode=reservation and verb is present."""
    if tenant_context.get("booking_mode") != "reservation":
        return slm
    intent = slm.get("intent", "UNKNOWN")
    if intent not in ("UNKNOWN", "CREATE_APPOINTMENT"):
        return slm
    if not _BOOKING_VERB_RE.search(text):
        return slm
    facts = slm.get("facts", {})
    has_service = facts.get("service_id") is not None
    if has_service or _RESERVATION_NOUN_RE.search(text):
        logger.debug(
            "Promoting intent %r → CREATE_RESERVATION for reservation booking_mode text=%r",
            intent,
            text,
        )
        return {**slm, "intent": "CREATE_RESERVATION"}
    return slm



_BOOKING_ID_EXPLICIT_RE = re.compile(
    r'(?:'
    r'#\s*\w+'
    r'|(?:booking|reservation)\s*(?:id|ref|reference|number|code)[\s:#]'
    r'|(?:id|ref|reference|number|code)\s*[:#]\s*\w+'
    r')',
    re.IGNORECASE,
)

_BOOKING_ID_LABELLED_RE = re.compile(
    r'(?:booking|reservation)\s+([\w-]+)',
    re.IGNORECASE,
)


def _booking_id_allowed_in_context(text: str, booking_id: str) -> bool:
    """True when the utterance clearly presents *booking_id* as a reference token."""
    bid = str(booking_id).strip()
    if not bid:
        return False
    stripped = text.strip()
    if stripped.lower() == bid.lower():
        return True
    if _BOOKING_ID_EXPLICIT_RE.search(text):
        return True
    for match in _BOOKING_ID_LABELLED_RE.finditer(text):
        if match.group(1).strip().lower() == bid.lower():
            return True
    return False


def _normalize_booking_id(
    text: str,
    slm: Dict[str, Any],
    tenant_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate and normalize booking_id using tenant-aware regex + phrase gates.

    Haiku may propose a booking_id; the pipeline is authoritative:
      1. Regex-scan raw text for ID-shaped tokens (global default, tenant override).
      2. Prefer the scanned token when present; otherwise keep Haiku's value if valid.
      3. Emit booking_id only when the phrase context allows it:
         - standalone ID reply, explicit anchor (#, "booking id:"), or
         - "booking/reservation <ID>" when <ID> matches the format.
    """
    from .config.booking_id import (
        get_booking_id_settings,
        is_valid_booking_id,
        scan_booking_id_from_text,
    )

    facts = slm.get("facts", {}) or {}
    validate_re, scan_re, _ = get_booking_id_settings(tenant_context)

    haiku_id = facts.get("booking_id")
    scanned_id = scan_booking_id_from_text(text, validate_re, scan_re)

    candidate: Optional[str] = None
    if scanned_id:
        candidate = scanned_id
    elif haiku_id and is_valid_booking_id(str(haiku_id), validate_re):
        candidate = str(haiku_id).strip()

    if not candidate:
        if haiku_id:
            logger.debug(
                "_normalize_booking_id: dropping invalid booking_id=%r text=%r",
                haiku_id,
                text,
            )
            return {**slm, "facts": {**facts, "booking_id": None}}
        return slm

    if not _booking_id_allowed_in_context(text, candidate):
        logger.debug(
            "_normalize_booking_id: dropping booking_id=%r (format ok, phrase not allowed) text=%r",
            candidate,
            text,
        )
        return {**slm, "facts": {**facts, "booking_id": None}}

    if candidate != haiku_id:
        logger.debug(
            "_normalize_booking_id: setting booking_id=%r (scanned=%r haiku=%r) text=%r",
            candidate,
            scanned_id,
            haiku_id,
            text,
        )
    return {**slm, "facts": {**facts, "booking_id": candidate}}


def _resolve_now(request_now: str = None) -> str:
    """Resolve reference datetime: request field → env var → wall clock."""
    if request_now:
        return request_now
    env = os.environ.get("LUMA_TEST_NOW")
    if env:
        return env
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class PipelineResult:
    intent: Dict[str, Any] = field(default_factory=lambda: {
                                   "name": "UNKNOWN", "confidence": 0.0})
    facts: Dict[str, Any] = field(default_factory=dict)
    time_constraint: Optional[Dict[str, Any]] = None
    search_query: Optional[str] = None
    date_constraint: Optional[Dict[str, Any]] = None
    service_candidates: List[str] = field(default_factory=list)
    operation: Optional[str] = None
    # Top-level Stage2 dialogue acts: schema field names explicitly declined.
    declined_entities: List[str] = field(default_factory=list)
    # Canonical OFF_TOPIC question (Stage2); never used as business FAQ search_query.
    off_topic_query: Optional[str] = None
    # OFF_TOPIC world-knowledge evidence from Stage2 (Core digression; null for other intents).
    answerable: Optional[bool] = None
    answer: Optional[str] = None
    # Additive Stage2 canonical temporal (not consumed by binder/planning yet).
    temporal: Optional[Dict[str, Any]] = None
    # Utterance understanding outcome for this turn (not planner status).
    understanding: str = "UNDERSTOOD"

    @property
    def turn(self) -> Dict[str, str]:
        return {"understanding": self.understanding}


class _SemanticResultAdapter:
    """Wraps a resolved_booking dict so calendar_binder.bind_calendar() can read it."""

    def __init__(self, resolved_booking: Dict[str, Any]):
        self.resolved_booking = resolved_booking
        self.needs_clarification = False
        self.clarification = None


def _normalize_dates(dates: List[str]) -> List[str]:
    """Prepend 'this' to bare weekday names so the binder treats them as nearest-future."""
    result = []
    for d in dates:
        stripped = str(d).strip()
        if stripped.lower() in _WEEKDAYS:
            result.append(f"this {stripped.lower()}")
        else:
            result.append(stripped)
    return result


def _fix_iso_weekday_mismatch(
    text: str,
    slm: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fix Haiku ISO dates that do not match a weekday name in the utterance.

    Common on CORRECTION turns ("saturday instead") when Haiku pre-resolves to
    the wrong ISO day. Uses last_date_proposal from conversation_context as an
    anchor when present; otherwise resets to bare weekday for calendar binding.
    """
    from .temporal.pipeline_sync import apply_temporal, get_temporal

    temporal = get_temporal(slm)
    raw = temporal.start_date
    if not raw or not re.match(r"^\d{4}-\d{2}-\d{2}", str(raw)):
        return slm
    iso = str(raw).split("T")[0].split(" ")[0]

    text_lower = (text or "").lower()
    mentioned = next((name for name in _WEEKDAY_TO_DOW if name in text_lower), None)
    if not mentioned:
        return slm

    expected_dow = _WEEKDAY_TO_DOW[mentioned]
    try:
        actual_dow = datetime.strptime(iso, "%Y-%m-%d").weekday()
    except ValueError:
        return slm
    if actual_dow == expected_dow:
        return slm

    anchor_date = None
    if isinstance(conversation_context, dict):
        last_dp = conversation_context.get("last_date_proposal")
        if isinstance(last_dp, dict):
            anchor_date = last_dp.get("start")

    if anchor_date:
        try:
            base = datetime.strptime(
                str(anchor_date).split("T")[0].split(" ")[0], "%Y-%m-%d"
            )
        except ValueError:
            base = datetime.strptime(iso, "%Y-%m-%d")
        days_ahead = (expected_dow - base.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        fixed = (base + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        logger.info(
            "[DATE_WEEKDAY_FIX] text=%r mentioned=%s bad_iso=%s anchor=%s fixed=%s",
            text,
            mentioned,
            iso,
            anchor_date,
            fixed,
        )
        temporal.start_date = fixed
        temporal.start_date_expression = None
        return apply_temporal(slm, temporal)

    logger.info(
        "[DATE_WEEKDAY_FIX] text=%r mentioned=%s bad_iso=%s → bare weekday for binder",
        text,
        mentioned,
        iso,
    )
    temporal.start_date = None
    temporal.start_date_expression = mentioned
    return apply_temporal(slm, temporal)


def _infer_date_mode(dates: List[str]) -> str:
    """Map the date list to the DateMode string the calendar binder expects."""
    if not dates:
        return "flexible"
    if len(dates) >= 2:
        return "range"
    phrase = str(dates[0]).lower().strip()
    if ("week" in phrase and "weekend" not in phrase) or "weekend" in phrase:
        return "flexible"
    return "single_day"


def _enrich_time_constraint(tc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Fill start/end window bounds from FUZZY_TIME_WINDOWS for fuzzy time constraints."""
    if tc is None or tc.get("mode") != "fuzzy":
        return tc
    label = (tc.get("label") or "").lower()
    if not label:
        return tc
    try:
        from .config.temporal import FUZZY_TIME_WINDOWS
        if label in FUZZY_TIME_WINDOWS:
            start, end = FUZZY_TIME_WINDOWS[label]
            return {**tc, "start": start, "end": end}
    except Exception:
        logger.debug(
            "Could not enrich fuzzy time_constraint for label=%r", label)
    return tc


def _active_booking_intent_from_context(
    conversation_context: Optional[Dict[str, Any]],
) -> Optional[str]:
    from .stages.shared.in_flow_act import active_booking_intent_from_context

    return active_booking_intent_from_context(conversation_context)


def _has_slot_material(facts: Dict[str, Any]) -> bool:
    if not isinstance(facts, dict):
        return False
    dates = facts.get("dates")
    times = facts.get("times")
    pairs = facts.get("date_time_pairs")
    tc = facts.get("time_constraint")
    if isinstance(dates, list) and dates:
        return True
    if isinstance(times, list) and times:
        return True
    if isinstance(pairs, list) and pairs:
        return True
    if isinstance(tc, dict) and tc.get("mode") not in (None, "none"):
        return True
    return False


def _slm_has_slot_material(slm: Dict[str, Any]) -> bool:
    """Slot-fill detection prefers Temporal, falls back to legacy facts."""
    from .temporal.pipeline_sync import (
        get_temporal,
        temporal_has_date_material,
        temporal_has_time_material,
    )

    temporal = get_temporal(slm)
    if temporal_has_date_material(temporal) or temporal_has_time_material(temporal):
        return True
    facts = slm.get("facts", {})
    if _has_slot_material(facts if isinstance(facts, dict) else {}):
        return True
    tc = slm.get("time_constraint")
    return isinstance(tc, dict) and tc.get("mode") not in (None, "none")


def _resolve_slot_fill_intent(
    slm: Dict[str, Any],
    text: str,
    conversation_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministic fallback: promote UNKNOWN → active booking intent in-flow.

    Primary resolution lives in Stage 2 create validation; this runs only when
    Stage 2 still emitted UNKNOWN despite active booking conversation context.
    """
    from .stages.shared.in_flow_act import promote_in_flow_booking_intent

    slm_intent = slm.get("intent", "UNKNOWN")
    promoted = promote_in_flow_booking_intent(
        slm_intent, text, conversation_context
    )
    if promoted == slm_intent:
        return slm

    logger.info(
        "[SLOT_FILL_INTENT] slm_intent=UNKNOWN promoted_to=%s (pipeline fallback)",
        promoted,
    )
    return {**slm, "intent": promoted}


def _resolve_calendar_binding_intent(
    slm_intent: str,
    facts: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]] = None,
    temporal: Optional[Dict[str, Any]] = None,
) -> str:
    """Pick intent passed to calendar binder (may differ from SLM intent on slot-fill turns.

    When Haiku returns UNKNOWN or CORRECTION but dates are present and
    conversation_context carries an active booking intent, bind using the
    session intent so named-month / range dates resolve to ISO.
    """
    from .calendar.calendar_binder import BINDING_INTENTS
    from .temporal.pipeline_sync import (
        parse_temporal_dict,
        temporal_has_date_material,
    )

    has_dates = False
    if isinstance(temporal, dict):
        has_dates = temporal_has_date_material(parse_temporal_dict(temporal))
    if not has_dates:
        dates = facts.get("dates") if isinstance(facts, dict) else []
        has_dates = isinstance(dates, list) and len(dates) > 0
    if slm_intent in BINDING_INTENTS or not has_dates:
        return slm_intent
    if not isinstance(conversation_context, dict):
        return slm_intent

    booking_intent = _active_booking_intent_from_context(conversation_context)
    if slm_intent in ("UNKNOWN", "CORRECTION") and booking_intent in BINDING_INTENTS:
        logger.info(
            "[CALENDAR_BIND_INTENT] slm_intent=%s binding_as=%s (session slot-fill/correction)",
            slm_intent,
            booking_intent,
        )
        return booking_intent
    return slm_intent




class NLUPipeline:
    """Two-stage NLU pipeline: Stage 1 (intent) → Stage 2 (slots) → normalise → calendar."""

    def __init__(self):
        self._stage1 = Stage1IntentExtractor()

    def run(
        self,
        text: str,
        tenant_context: Dict[str, Any],
        now: str = None,
        timezone: str = "UTC",
        conversation_context: Optional[Dict[str, Any]] = None,
        entity_schema: Optional[Dict[str, Any]] = None,
    ) -> PipelineResult:
        from .stages.stage2.entity_schema import (
            compile_business_entities,
            effective_tenant_context,
        )

        now = _resolve_now(now)
        from .temporal.resolver import anchor_now, format_prompt_now

        local_now = anchor_now(now, timezone)
        prompt_now = format_prompt_now(local_now, timezone)

        # Single compile/validate per request. Downstream receives CompiledBusinessEntities only.
        compiled = None
        if entity_schema is not None:
            compiled = compile_business_entities(entity_schema)
        # New dict only — never mutate the caller's tenant_context.
        effective_tc = effective_tenant_context(tenant_context, compiled)

        slm = self._slm_extract(
            text,
            effective_tc,
            prompt_now,
            conversation_context=conversation_context,
            compiled_entities=compiled,
        )
        slm = _strip_unmentioned_dates(text, slm)
        slm = _strip_unmentioned_times(text, slm)
        slm = _strip_unmentioned_service(text, slm)
        slm = _normalize_fuzzy_time(text, slm)
        slm = _normalize_booking_id(text, slm, effective_tc)
        slm = _apply_booking_mode_intent(text, slm, effective_tc)
        slm = self._correct_bare_weekday_dates(text, slm)
        slm = _fix_iso_weekday_mismatch(text, slm, conversation_context)
        slm = self._resolve_service_ambiguity(
            slm,
            effective_tc,
            conversation_context,
            compiled=compiled,
            text=text,
        )
        slm = _resolve_slot_fill_intent(slm, text, conversation_context)
        from .temporal.bare_ordinal import inject_bare_ordinal_expression

        slm = inject_bare_ordinal_expression(text, slm)
        from .stages.shared.turn_understanding import derive_turn_understanding

        understanding = derive_turn_understanding(slm, conversation_context)
        result = self._bind_calendar(
            slm,
            effective_tc,
            now,
            timezone,
            conversation_context,
            local_now=local_now,
        )
        result.understanding = understanding
        logger.info(
            "[NLU_FINAL] text=%r intent=%r understanding=%r operation=%r resolved_dates=%r "
            "date_time_pairs=%r times=%r service_id=%r service_candidates=%r "
            "time_constraint=%r",
            text,
            result.intent.get("name"),
            result.understanding,
            result.operation,
            result.facts.get("dates"),
            result.facts.get("date_time_pairs"),
            result.facts.get("times"),
            result.facts.get("service_id"),
            result.service_candidates,
            result.time_constraint,
        )
        return result

    def _correct_bare_weekday_dates(self, text: str, slm: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise Haiku's Temporal output for bare weekday / weekend inputs.

        Two cases:
        - Single bare weekday ("wednesday"): Haiku may resolve to wrong ISO week.
          Reset to expression so the binder picks the nearest future occurrence.
        - Weekend phrase ("this weekend", …): reset to flexible expression so the
          binder's week/weekend path runs instead of a saturday/sunday range.
        """
        from .temporal.pipeline_sync import apply_temporal, get_temporal

        words = text.lower().strip().split()
        temporal = get_temporal(slm)

        if len(words) == 1 and words[0] in _WEEKDAYS:
            temporal.start_date = None
            temporal.end_date = None
            temporal.start_date_expression = words[0]
            temporal.end_date_expression = None
            return apply_temporal(slm, temporal)

        _WEEKEND_PHRASES = {"this weekend", "next weekend", "the weekend", "weekend"}
        if " ".join(words) in _WEEKEND_PHRASES:
            temporal.start_date = None
            temporal.end_date = None
            temporal.start_date_expression = " ".join(words)
            temporal.end_date_expression = None
            return apply_temporal(slm, temporal)

        return slm

    def _resolve_service_ambiguity(
        self, slm: Dict[str, Any], tenant_context: Dict[str, Any],
        conversation_context: Optional[Dict[str, Any]] = None,
        compiled: Optional[Any] = None,
        text: Optional[str] = None,
    ) -> Dict[str, Any]:
        if compiled is not None:
            return self._resolve_schema_entities(
                slm, conversation_context, compiled, text=text
            )

        aliases = tenant_context.get("aliases", {})
        if not aliases:
            return slm

        service_term = slm.get("service_term")
        facts = slm.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}

        # AVAILABILITY Stage 2 extracts service into facts.service_id and leaves
        # service_term null. Honor that current-turn evidence before reusing a
        # locked session resolved_service_id (date-only follow-ups keep null).
        if not service_term:
            extracted = facts.get("service_id")
            if isinstance(extracted, str) and extracted.strip():
                service_term = extracted.strip()

        # Explicit catalog mention in the current utterance overrides sticky
        # prior service when Stage 2 omitted service_term.
        if not service_term and text:
            recovered = infer_service_term_from_utterance(text, aliases)
            if recovered:
                service_term = recovered

        ctx = conversation_context if isinstance(conversation_context, dict) else {}
        awaiting_service_id = "service_id" in ctx.get("missing_slots", [])

        prior_text: Optional[str] = None
        candidate_keys: Optional[List[str]] = None
        resolved_service_id: Optional[str] = None
        if ctx:
            cands = ctx.get("service_candidates")
            if isinstance(cands, list) and cands:
                candidate_keys = cands
            if awaiting_service_id:
                turns = ctx.get("turns") or []
                joined = " ".join(t.get("user", "") for t in turns).strip()
                prior_text = joined or None
            raw_resolved = ctx.get("resolved_service_id")
            if isinstance(raw_resolved, str) and raw_resolved:
                resolved_service_id = raw_resolved

        resolved = resolve_service(
            service_term=service_term,
            aliases=aliases,
            prior_text=prior_text,
            awaiting_service_id=awaiting_service_id,
            candidate_keys=candidate_keys,
            resolved_service_id=resolved_service_id,
        )
        logger.debug(
            "_resolve_service_ambiguity: service_term=%r awaiting=%r prior=%r candidates=%r → service_id=%r out_candidates=%r",
            service_term, awaiting_service_id, prior_text, candidate_keys,
            resolved["service_id"], resolved["service_candidates"],
        )
        return {
            **slm,
            "facts": {**facts, "service_id": resolved["service_id"]},
            "service_candidates": resolved["service_candidates"],
        }

    def _resolve_schema_entities(
        self,
        slm: Dict[str, Any],
        conversation_context: Optional[Dict[str, Any]],
        compiled: Any,
        text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve each catalog entity against only that entity's catalog."""
        from .stages.stage2.entity_schema import resolved_id_key

        facts = dict(slm.get("facts") or {})
        ctx = conversation_context if isinstance(conversation_context, dict) else {}
        awaiting_service_id = "service_id" in ctx.get("missing_slots", [])

        prior_text: Optional[str] = None
        bookable_candidates: Optional[List[str]] = None
        resolved_service_id: Optional[str] = None
        if ctx:
            cands = ctx.get("service_candidates")
            if isinstance(cands, list) and cands:
                bookable_candidates = cands
            if awaiting_service_id:
                turns = ctx.get("turns") or []
                joined = " ".join(t.get("user", "") for t in turns).strip()
                prior_text = joined or None
            raw_resolved = ctx.get("resolved_service_id")
            if isinstance(raw_resolved, str) and raw_resolved:
                resolved_service_id = raw_resolved

        service_candidates: List[str] = []
        bookable = compiled.bookable_item_field

        for entity in compiled.fields:
            if entity.type != "catalog":
                continue

            phrase = facts.get(entity.name)
            if isinstance(phrase, str):
                phrase = phrase.strip() or None
            else:
                phrase = None

            id_key = resolved_id_key(entity)
            is_bookable = bookable is not None and entity.name == bookable.name

            # resolve_service treats alias keys as the resolved service_id values
            # Core already consumes (display phrases). Per-field catalogs stay isolated.
            aliases = dict(entity.catalog)
            if not aliases:
                facts.setdefault(id_key, None)
                continue

            if is_bookable and not phrase and text:
                phrase = infer_service_term_from_utterance(text, aliases)

            candidate_keys = bookable_candidates if is_bookable else None
            resolved = resolve_service(
                service_term=phrase,
                aliases=aliases,
                prior_text=prior_text if is_bookable else None,
                awaiting_service_id=awaiting_service_id if is_bookable else False,
                candidate_keys=candidate_keys,
                resolved_service_id=resolved_service_id if is_bookable else None,
            )
            facts[id_key] = resolved["service_id"]
            if is_bookable:
                service_candidates = list(resolved["service_candidates"] or [])

            logger.debug(
                "_resolve_schema_entities: field=%r phrase=%r → %s=%r candidates=%r",
                entity.name,
                phrase,
                id_key,
                resolved["service_id"],
                resolved["service_candidates"] if is_bookable else [],
            )

        # Ensure platform keys exist even when schema has no bookable catalog field.
        facts.setdefault("service_id", None)
        facts.setdefault("booking_id", facts.get("booking_id"))

        out = {
            **slm,
            "facts": facts,
            "service_candidates": service_candidates,
        }
        # Keep legacy service_term aligned with bookable phrase for grounding helpers.
        if bookable is not None:
            out["service_term"] = facts.get(bookable.name)
        return out

    def _slm_extract(
        self,
        text: str,
        tenant_context: Dict[str, Any],
        now: str,
        conversation_context: Optional[Dict[str, Any]] = None,
        compiled_entities: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Two-stage extraction: Stage 1 classifies intent, Stage 2 extracts slots.

        ``compiled_entities`` is ``CompiledBusinessEntities | None`` from
        ``NLUPipeline.run`` (already validated; CREATE must not recompile).
        """
        stage1 = self._stage1.classify(text, now, conversation_context)
        intent = stage1.get("intent", "UNKNOWN")
        confidence = stage1.get("confidence", 0.0)
        logger.info(
            "[NLU_STAGE1] text=%r intent=%r confidence=%.2f ctx_last_intent=%r",
            text,
            intent,
            confidence,
            (conversation_context or {}).get("last_intent"),
        )

        result = _stage2_extract(
            intent=intent,
            text=text,
            now=now,
            tenant_context=tenant_context,
            conversation_context=conversation_context,
            compiled_entities=compiled_entities,
        )
        _facts = result.get("facts") or {}
        logger.info(
            "[NLU_STAGE2] text=%r intent=%r dates=%r date_time_pairs=%r times=%r service_term=%r",
            text,
            result.get("intent"),
            _facts.get("dates"),
            _facts.get("date_time_pairs"),
            _facts.get("times"),
            result.get("service_term"),
        )
        # Carry through Stage 1 confidence when Stage 2 didn't change the intent
        if result.get("intent") == intent and result.get("confidence", 0.0) == 0.0:
            result = {**result, "confidence": confidence}
        # Ensure service_candidates and service_term are always present
        if "service_candidates" not in result:
            result = {**result, "service_candidates": []}
        if "service_term" not in result:
            result = {**result, "service_term": None}
        return result

    def _bind_calendar(
        self,
        decision: Dict[str, Any],
        tenant_context: Dict[str, Any],
        now: str,
        timezone: str = "UTC",
        conversation_context: Optional[Dict[str, Any]] = None,
        local_now: Optional[datetime] = None,
    ) -> PipelineResult:
        from .calendar.calendar_binder import bind_calendar
        from .temporal.pipeline_sync import (
            apply_temporal,
            get_temporal,
            infer_date_mode_from_temporal,
        )
        from .temporal.project_legacy import project_legacy_from_temporal
        from .temporal.resolver import (
            anchor_now,
            apply_bare_ordinal_revision,
            resolve_temporal,
        )

        intent = decision.get("intent", "UNKNOWN")
        confidence = decision.get("confidence", 0.0)
        facts = decision.get("facts", {})
        search_query = decision.get("search_query")
        off_topic_query = decision.get("off_topic_query")
        answerable = decision.get("answerable")
        answer = decision.get("answer")
        service_candidates = decision.get("service_candidates") or []
        operation = decision.get("operation")
        raw_declined = decision.get("declined_entities")
        declined_entities: List[str] = []
        if isinstance(raw_declined, list):
            seen = set()
            for item in raw_declined:
                if not isinstance(item, str) or not item.strip():
                    continue
                name = item.strip()
                if name in seen:
                    continue
                seen.add(name)
                declined_entities.append(name)

        if local_now is None:
            local_now = anchor_now(now, timezone)

        temporal = resolve_temporal(get_temporal(decision), local_now, timezone)
        temporal = apply_bare_ordinal_revision(
            temporal,
            conversation_context=conversation_context,
            now=local_now,
        )
        # Keep legacy projection in sync (Core compat); ISO preferred in dates[].
        decision = apply_temporal(decision, temporal)
        facts = decision.get("facts", {})
        tc = decision.get("time_constraint")

        binding_intent = _resolve_calendar_binding_intent(
            intent, facts, conversation_context, temporal=temporal.to_dict()
        )

        date_mode = infer_date_mode_from_temporal(temporal)
        legacy_dates = project_legacy_from_temporal(temporal)["dates"]
        # date_refs are ISO (or unresolved expression only if open vocab left unbound)
        normalized_dates = list(legacy_dates)

        tc_mode = (tc or {}).get("mode", "none")
        if tc_mode == "exact":
            time_mode = "exact"
        elif tc_mode in ("fuzzy", "window"):
            time_mode = "window"
        else:
            time_mode = "none"

        resolved_booking = {
            "date_refs": normalized_dates,
            "date_mode": date_mode,
            "date_modifiers": [],
            "time_refs": facts.get("times", []),
            "time_mode": time_mode,
            "time_constraint": tc,
            "services": [],
            "booking_mode": tenant_context.get("booking_mode", "service"),
            "duration": None,
        }

        semantic = _SemanticResultAdapter(resolved_booking)

        try:
            binder_result, _ = bind_calendar(
                semantic_result=semantic,
                now=local_now,
                timezone=timezone,
                intent=binding_intent,
                temporal=temporal.to_dict(),
            )
            date_range = binder_result.calendar_booking.get("date_range")
            if date_range:
                start = date_range.get("start_date")
                end = date_range.get("end_date")
                if start and end and start == end:
                    temporal.start_date, temporal.end_date = start, None
                elif start and end:
                    temporal.start_date, temporal.end_date = start, end
                elif start:
                    temporal.start_date, temporal.end_date = start, None
                # Preserve expressions for audit; projection prefers ISO.
        except Exception:
            logger.exception(
                "Calendar binding failed for intent=%r (binding_intent=%r)",
                intent,
                binding_intent,
            )

        synced = apply_temporal(decision, temporal)
        facts = synced.get("facts", {})
        tc = synced.get("time_constraint")

        date_constraint = {"mode": date_mode} if date_mode == "flexible" else None

        return PipelineResult(
            intent={"name": intent, "confidence": confidence},
            facts=facts,
            time_constraint=_enrich_time_constraint(tc),
            search_query=search_query,
            date_constraint=date_constraint,
            service_candidates=service_candidates,
            operation=operation if isinstance(operation, str) else None,
            declined_entities=declined_entities,
            off_topic_query=off_topic_query if isinstance(off_topic_query, str) else None,
            answerable=answerable if isinstance(answerable, bool) else None,
            answer=answer if isinstance(answer, str) and answer.strip() else None,
            temporal=temporal.to_dict(),
        )
