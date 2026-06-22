"""
NLU Pipeline — 3-stage: SLM extraction → decision → calendar binding.

Stage 1 (SLM):      HaikuExtractor → intent + raw facts + time_constraint
Stage 2 (decision): Pass-through — decision layer output contract differs from /resolve
Stage 3 (calendar): ISO-8601 date binding via calendar_binder
"""
import logging
import os
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

    def run(self, text: str, tenant_context: Dict[str, Any], now: str = None, timezone: str = "UTC") -> PipelineResult:
        now = _resolve_now(now)
        slm = self._slm_extract(text, tenant_context, now)
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

    def _slm_extract(self, text: str, tenant_context: Dict[str, Any], now: str) -> Dict[str, Any]:
        return self._extractor.extract(text, tenant_context, now)

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

        return PipelineResult(
            intent={"name": intent, "confidence": confidence},
            facts={**facts, "dates": bound_dates},
            time_constraint=_enrich_time_constraint(tc),
            search_query=search_query,
        )
