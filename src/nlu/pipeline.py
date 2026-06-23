"""
NLU Pipeline — 3-stage: SLM extraction → decision → calendar binding.

Stage 1 (SLM):      HaikuExtractor → intent + raw facts + time_constraint
Stage 2 (decision): Pass-through — decision layer output contract differs from /resolve
Stage 3 (calendar): ISO-8601 date binding via calendar_binder
"""
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .slm.extractor import HaikuExtractor

logger = logging.getLogger(__name__)

_WEEKDAYS = {
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
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
_DATE_SINGLE_RE = re.compile(
    rf"\b{_MONTH_PATTERN}\s+\d{{1,2}}(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")
_BOOKING_VERB_RE = re.compile(r"\b(?:book|reserve|schedule)\b", re.IGNORECASE)
_RESERVATION_NOUN_RE = re.compile(
    r"\b(?:room|suite|accommodation|stay|deluxe|delux)\b", re.IGNORECASE
)


def _text_mentions_date(text: str) -> bool:
    """True when the utterance explicitly mentions a calendar date (not bare clock time)."""
    lowered = text.lower()
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

# Explicit clock-time patterns: "3pm", "3:30 am", "15:00", "9am"
_EXPLICIT_CLOCK_RE = re.compile(
    r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\b\d{2}:\d{2}\b",
    re.IGNORECASE,
)


def _normalize_fuzzy_time(text: str, slm: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure fuzzy time-window tokens (morning/afternoon/evening/night) produce a
    fuzzy time_constraint and NOT a concrete clock time in facts.times.

    Haiku sometimes emits a concrete time like "20:00" when the user says "evening".
    That concrete time gets promoted to slots.time and satisfies the time slot too
    early, causing READY when NEEDS_CLARIFICATION is expected (scenario 21).

    When an explicit clock time is also present ("evening at 7pm"), leave the SLM
    output unchanged — the clock time takes precedence.
    """
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

    facts = slm.get("facts") or {}
    times = list(facts.get("times") or [])
    tc = slm.get("time_constraint") or {}

    # Already correct: fuzzy with right label and no spurious clock times
    if (
        isinstance(tc, dict)
        and tc.get("mode") == "fuzzy"
        and tc.get("label") == found_token
        and not times
    ):
        return slm

    try:
        from .config.temporal import FUZZY_TIME_WINDOWS
        start, end = FUZZY_TIME_WINDOWS[found_token]
    except Exception:
        return slm

    new_tc = {"mode": "fuzzy", "label": found_token, "start": start, "end": end}
    logger.debug(
        "_normalize_fuzzy_time: text=%r token=%r cleared_times=%r tc=%r→%r",
        text,
        found_token,
        times,
        tc,
        new_tc,
    )
    return {
        **slm,
        "time_constraint": new_tc,
        "facts": {**facts, "times": []},
    }


_CANCEL_VERB_RE = re.compile(r"\bcancel\b", re.IGNORECASE)
_CANCEL_NEGATION_RE = re.compile(
    r"\b(?:don'?t|do not|not|won'?t|can'?t|cannot|please\s+don'?t|please\s+do\s+not)\s+cancel\b",
    re.IGNORECASE,
)


def _normalize_cancel_intent(text: str, slm: Dict[str, Any]) -> Dict[str, Any]:
    """Force CANCEL_BOOKING when text contains an unambiguous cancel verb.

    Haiku sometimes misclassifies "nevermind cancel" or "actually cancel it" as UNKNOWN
    or leaves context from a prior CREATE_APPOINTMENT turn.  A regex override is more
    reliable for these short pivot utterances.

    Negation guard: "don't cancel", "please do not cancel", etc. pass through unchanged.
    """
    if not _CANCEL_VERB_RE.search(text):
        return slm
    if _CANCEL_NEGATION_RE.search(text):
        return slm
    current_intent = slm.get("intent", "UNKNOWN")
    if current_intent == "CANCEL_BOOKING":
        return slm
    logger.debug(
        "_normalize_cancel_intent: text=%r intent=%r→CANCEL_BOOKING",
        text, current_intent,
    )
    return {**slm, "intent": "CANCEL_BOOKING"}


def _strip_unmentioned_dates(text: str, slm: Dict[str, Any]) -> Dict[str, Any]:
    """Remove dates Haiku invented when the user only mentioned a clock time."""
    if _text_mentions_date(text):
        return slm
    facts = slm.get("facts", {})
    if not facts.get("dates") and not facts.get("date_time_pairs"):
        return slm
    logger.debug(
        "Stripping unmentioned dates from SLM output for text=%r dates=%r pairs=%r",
        text,
        facts.get("dates"),
        facts.get("date_time_pairs"),
    )
    return {
        **slm,
        "facts": {
            **facts,
            "dates": [],
            "date_time_pairs": [],
        },
    }


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


def _resolve_alias_ambiguity(
    text: str, service_id: Optional[str], aliases: Dict[str, str]
) -> Optional[str]:
    """Return None when service_id is ambiguous across multiple alias keys.

    Algorithm:
    1. Build alias vocabulary — the set of all words that appear in any alias key,
       minus date/time tokens (month names, weekdays, time words) that could
       collide with date phrases in user input.
    2. Filter user tokens to only those in the filtered alias vocabulary.
    3. If an alias key appears verbatim as a contiguous token sequence, it is an
       exact match — return service_id unchanged.
    4. Score each alias key by how many relevant user tokens it contains.
       Tie → null; single winner → return service_id unchanged.
    """
    if not aliases or service_id is None:
        return service_id

    alias_vocab: set = set()
    for key in aliases:
        alias_vocab.update(key.lower().split())
    alias_vocab -= _DATE_TIME_TOKENS

    user_tokens = [w.strip(".,!?") for w in text.lower().split()]
    relevant = [t for t in user_tokens if t in alias_vocab]

    if not relevant:
        return service_id

    # Exact-key match: alias key appears as a contiguous token sequence — unambiguous.
    def _key_in_tokens(key: str) -> bool:
        key_words = key.lower().split()
        n = len(key_words)
        return any(user_tokens[i:i+n] == key_words for i in range(len(user_tokens) - n + 1))

    exact_matches = [k for k in aliases if _key_in_tokens(k)]
    if len(exact_matches) == 1:
        return exact_matches[0]

    def _score(alias_key: str) -> int:
        return len(set(relevant) & set(alias_key.lower().split()))

    scores = {key: _score(key) for key in aliases}
    max_score = max(scores.values())

    if max_score == 0:
        return service_id

    top_keys = [k for k, s in scores.items() if s == max_score]
    if len(top_keys) >= 2:
        logger.debug(
            "Alias ambiguity: relevant=%r top_keys=%r → nulling service_id",
            relevant, top_keys,
        )
        return None

    return top_keys[0]


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


class NLUPipeline:
    """3-stage NLU pipeline."""

    def __init__(self):
        self._extractor = HaikuExtractor()

    def run(
        self,
        text: str,
        tenant_context: Dict[str, Any],
        now: str = None,
        timezone: str = "UTC",
        conversation_context: Optional[Dict[str, Any]] = None,
    ) -> PipelineResult:
        now = _resolve_now(now)
        slm = self._slm_extract(text, tenant_context, now, conversation_context=conversation_context)
        slm = _strip_unmentioned_dates(text, slm)
        slm = _normalize_fuzzy_time(text, slm)
        slm = _normalize_cancel_intent(text, slm)
        slm = _normalize_booking_id(text, slm, tenant_context)
        slm = _apply_booking_mode_intent(text, slm, tenant_context)
        slm = self._correct_bare_weekday_dates(text, slm)
        slm = self._resolve_service_ambiguity(text, slm, tenant_context)
        return self._bind_calendar(slm, tenant_context, now, timezone)

    def _correct_bare_weekday_dates(self, text: str, slm: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise Haiku's date output for bare weekday / weekend inputs.

        Two cases:
        - Single bare weekday ("wednesday"): Haiku may resolve to wrong ISO week.
          Reset to raw name so _normalize_dates prepends "this" and the binder
          picks the nearest future occurrence.
        - Weekend phrase ("this weekend", "next weekend", …): Haiku often expands
          to ["saturday", "sunday"], making date_mode="range" and bypassing the
          binder's flexible weekend path. Reset to the original phrase so
          date_mode becomes "flexible" and the binder handles it correctly.
        """
        words = text.lower().strip().split()

        if len(words) == 1 and words[0] in _WEEKDAYS:
            facts = slm.get("facts", {})
            return {**slm, "facts": {**facts, "dates": [words[0]]}}

        _WEEKEND_PHRASES = {"this weekend", "next weekend", "the weekend", "weekend"}
        if " ".join(words) in _WEEKEND_PHRASES:
            facts = slm.get("facts", {})
            return {**slm, "facts": {**facts, "dates": [" ".join(words)]}}

        return slm

    def _resolve_service_ambiguity(
        self, text: str, slm: Dict[str, Any], tenant_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        aliases = tenant_context.get("aliases", {})
        if not aliases:
            return slm
        facts = slm.get("facts", {})
        service_id = facts.get("service_id")
        resolved = _resolve_alias_ambiguity(text, service_id, aliases)
        if resolved != service_id:
            return {**slm, "facts": {**facts, "service_id": resolved}}
        return slm

    def _slm_extract(
        self,
        text: str,
        tenant_context: Dict[str, Any],
        now: str,
        conversation_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._extractor.extract(text, tenant_context, now, conversation_context=conversation_context)

    def _bind_calendar(
        self, decision: Dict[str, Any], tenant_context: Dict[str, Any], now: str, timezone: str = "UTC"
    ) -> PipelineResult:
        from .calendar.calendar_binder import bind_calendar

        intent = decision.get("intent", "UNKNOWN")
        confidence = decision.get("confidence", 0.0)
        facts = decision.get("facts", {})
        tc = decision.get("time_constraint")
        search_query = decision.get("search_query")

        raw_dates = facts.get("dates", [])
        normalized_dates = _normalize_dates(raw_dates)
        date_mode = _infer_date_mode(normalized_dates)

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
        # fallback: Haiku's raw dates if binder skips
        bound_dates = list(raw_dates)
        binder_resolved = False

        try:
            now_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
            binder_result, _ = bind_calendar(
                semantic_result=semantic,
                now=now_dt,
                timezone=timezone,
                intent=intent,
            )
            date_range = binder_result.calendar_booking.get("date_range")
            if date_range:
                binder_resolved = True
                start = date_range.get("start_date")
                end = date_range.get("end_date")
                if start and end and start == end:
                    bound_dates = [start]
                elif start and end:
                    bound_dates = [start, end]
                elif start:
                    bound_dates = [start]
        except Exception:
            logger.exception("Calendar binding failed for intent=%r", intent)

        # Compact enumerated date sequences (e.g. Haiku outputs every date in a range).
        # When the binder skipped, collapse 3+ dates to [first, last].
        if not binder_resolved and len(bound_dates) > 2 and date_mode == "range":
            bound_dates = [bound_dates[0], bound_dates[-1]]

        # Fix 2: propagate the binder-resolved ISO date back into date_time_pairs so
        # facts_to_slots sees the canonical date rather than the raw phrase ("tomorrow",
        # "friday") which would overwrite the ISO date already set from facts.dates[0].
        updated_dt_pairs = facts.get("date_time_pairs", [])
        if binder_resolved and bound_dates and isinstance(updated_dt_pairs, list) and updated_dt_pairs:
            iso_date = bound_dates[0]
            updated_dt_pairs = [
                {**pair, "date": iso_date} if isinstance(pair, dict) and "date" in pair else pair
                for pair in updated_dt_pairs
            ]

        # Fix 4: signal flexible/ambiguous date ranges so Core can withhold date slot
        # satisfaction for combined first-turn utterances like "book facial next week".
        date_constraint = {"mode": date_mode} if date_mode == "flexible" else None

        return PipelineResult(
            intent={"name": intent, "confidence": confidence},
            facts={**facts, "dates": bound_dates, "date_time_pairs": updated_dt_pairs},
            time_constraint=_enrich_time_constraint(tc),
            search_query=search_query,
            date_constraint=date_constraint,
        )
