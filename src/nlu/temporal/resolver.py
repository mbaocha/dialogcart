"""
TemporalResolver — deterministic verify / repair / resolve.

Supported vocabulary:

  today | tomorrow | yesterday
  monday..sunday | this <weekday> | next <weekday>
  this week | next week
  this weekend | next weekend
  the weekend | weekend  (treated as this weekend)
  named-month day phrases (unambiguous): ``23rd july``, ``July 23``,
  ``23 July``, ``July 23rd`` (optional year)
  bare ordinal expressions (context-anchored): ``23rd``, ``24th``, ``15th``
  when conversation supplies Last date proposal (expression must already be
  present on Temporal; utterance recovery lives outside this module)

Stage2 owns open relatives (next month, in two weeks) and semantic ISO.
Confidence is ignored for control flow (telemetry only).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple, Union

from .models import TEMPORAL_MODES, Temporal

logger = logging.getLogger(__name__)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_RELATIVE_OFFSETS = {
    "today": 0,
    "tomorrow": 1,
    "yesterday": -1,
}

_MONTH_NAME_TO_NUM = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

_MONTH_ALT = "|".join(sorted(_MONTH_NAME_TO_NUM.keys(), key=len, reverse=True))

# ``23rd july``, ``23 july``, ``july 23``, ``july 23rd``, optional year.
_NAMED_MONTH_RE = re.compile(
    rf"""^
    (?:
        (?P<day1>\d{{1,2}})(?:st|nd|rd|th)?
        \s+(?:of\s+)?
        (?P<month1>{_MONTH_ALT})
      |
        (?P<month2>{_MONTH_ALT})
        \s+(?:the\s+)?
        (?P<day2>\d{{1,2}})(?:st|nd|rd|th)?
    )
    (?:\s*,?\s*(?P<year>\d{{4}}))?
    $""",
    re.IGNORECASE | re.VERBOSE,
)

_CLOSED_VOCABULARY = frozenset(
    {
        "today",
        "tomorrow",
        "yesterday",
        "this week",
        "next week",
        "this weekend",
        "next weekend",
        "the weekend",
        "weekend",
        *(_WEEKDAYS.keys()),
        *(f"this {d}" for d in _WEEKDAYS),
        *(f"next {d}" for d in _WEEKDAYS),
    }
)

CLOSED_RELATIVE_VOCABULARY = _CLOSED_VOCABULARY


def _local_date(now: datetime) -> datetime:
    """Calendar day at local midnight (tz-aware if now is aware)."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def _parse_iso(value: Optional[str]) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip().split("T")[0].split(" ")[0]
    if not _ISO_DATE_RE.match(raw):
        return None
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None
    return raw


def _normalize_phrase(expr: Optional[str]) -> Optional[str]:
    if not expr:
        return None
    phrase = re.sub(r"\s+", " ", str(expr).strip().lower())
    return phrase or None


def is_closed_vocabulary(expression: Optional[str]) -> bool:
    phrase = _normalize_phrase(expression)
    return bool(phrase and phrase in _CLOSED_VOCABULARY)


def is_named_month_expression(expression: Optional[str]) -> bool:
    phrase = _normalize_phrase(expression)
    return bool(phrase and _NAMED_MONTH_RE.match(phrase))


def resolve_named_month_phrase(
    expression: str, now: datetime
) -> Tuple[Optional[str], str]:
    """
    Resolve an unambiguous named-month day phrase to (iso_date, mode).

    Year omitted → tenant-local reference year; if that calendar day has already
    passed relative to ``now``'s local midnight, use the next year.
    Invalid day/month combinations return ``(None, "none")``.
    """
    phrase = _normalize_phrase(expression)
    if not phrase:
        return None, "none"
    match = _NAMED_MONTH_RE.match(phrase)
    if not match:
        return None, "none"

    day_raw = match.group("day1") or match.group("day2")
    month_raw = match.group("month1") or match.group("month2")
    year_raw = match.group("year")
    if not day_raw or not month_raw:
        return None, "none"

    try:
        day = int(day_raw)
        month = _MONTH_NAME_TO_NUM[month_raw.lower()]
    except (ValueError, KeyError):
        return None, "none"

    year = int(year_raw) if year_raw else None
    base = _local_date(now)

    def _candidate(y: int) -> Optional[datetime]:
        try:
            return base.replace(year=y, month=month, day=day)
        except ValueError:
            return None

    if year is None:
        candidate = _candidate(base.year)
        if candidate is None or candidate < base:
            candidate = _candidate(base.year + 1)
        if candidate is None:
            return None, "none"
    else:
        candidate = _candidate(year)
        if candidate is None:
            return None, "none"

    return _iso(candidate), "single_day"


def resolve_closed_phrase(
    expression: str, now: datetime
) -> Tuple[Optional[str], Optional[str], str]:
    """
    Resolve a closed-vocab phrase to (start_iso, end_iso, mode).

    end_iso is None for single-day; set for week/weekend ranges.
    mode is single_day or flexible.
    """
    phrase = _normalize_phrase(expression)
    if not phrase or phrase not in _CLOSED_VOCABULARY:
        return None, None, "none"

    base = _local_date(now)

    if phrase in _RELATIVE_OFFSETS:
        day = base + timedelta(days=_RELATIVE_OFFSETS[phrase])
        return _iso(day), None, "single_day"

    if phrase in ("weekend", "the weekend"):
        phrase = "this weekend"

    if phrase in ("this weekend", "next weekend"):
        today_wd = base.weekday()
        if phrase.startswith("next"):
            days_until_sat = (5 - today_wd) % 7
            if days_until_sat == 0:
                days_until_sat = 7
        else:
            days_until_sat = (5 - today_wd) % 7
        start = base + timedelta(days=days_until_sat)
        end = start + timedelta(days=1)
        return _iso(start), _iso(end), "flexible"

    if phrase in ("this week", "next week"):
        monday = base - timedelta(days=base.weekday())
        if phrase.startswith("next"):
            days_until_monday = (7 - base.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7
            monday = base + timedelta(days=days_until_monday)
        sunday = monday + timedelta(days=6)
        return _iso(monday), _iso(sunday), "flexible"

    # Weekdays: bare | this | next
    match = re.fullmatch(
        r"(?:(this|next)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)",
        phrase,
    )
    if match:
        modifier, day_name = match.group(1), match.group(2)
        target = _WEEKDAYS[day_name]
        if modifier == "next":
            start_of_week = base - timedelta(days=base.weekday())
            start_of_next = start_of_week + timedelta(days=7)
            day = start_of_next + timedelta(days=target)
        else:
            # bare / this → nearest future (if today is that day → next week)
            days_ahead = (target - base.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            day = base + timedelta(days=days_ahead)
        return _iso(day), None, "single_day"

    return None, None, "none"


def _infer_mode(temporal: Temporal) -> str:
    if temporal.mode in TEMPORAL_MODES and temporal.mode != "none":
        return temporal.mode  # type: ignore[return-value]
    start_expr = _normalize_phrase(temporal.start_date_expression) or ""
    end_expr = _normalize_phrase(temporal.end_date_expression) or ""
    phrase = start_expr or end_expr
    if phrase and (
        ("week" in phrase and "weekend" not in phrase) or "weekend" in phrase
    ):
        if is_closed_vocabulary(phrase):
            return "flexible"
    if temporal.end_date or temporal.end_date_expression:
        return "range"
    if temporal.start_date or temporal.start_date_expression:
        return "single_day"
    if temporal.start_time or temporal.start_time_expression:
        return "none"
    return "none"


def _repair_order(start: Optional[str], end: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not start or not end:
        return start, end
    if start <= end:
        return start, end
    # Swap years if end month/day before start (legacy binder behaviour)
    try:
        s = datetime.strptime(start, "%Y-%m-%d")
        e = datetime.strptime(end, "%Y-%m-%d")
        e_fixed = e.replace(year=s.year)
        if s <= e_fixed:
            return start, _iso(e_fixed)
        s_fixed = s.replace(year=e.year)
        if s_fixed <= e:
            return _iso(s_fixed), end
    except ValueError:
        pass
    return end, start


def _apply_named_month_resolution(
    out: Temporal,
    phrase: Optional[str],
    now: datetime,
    *,
    as_end: bool = False,
) -> None:
    if not phrase:
        return
    iso, mode = resolve_named_month_phrase(phrase, now)
    if not iso:
        return
    if as_end:
        if out.end_date and out.end_date != iso:
            logger.warning(
                "[TEMPORAL_RESOLVER] named-month mismatch repair end: "
                "expr=%r stage2=%s oracle=%s",
                phrase,
                out.end_date,
                iso,
            )
        out.end_date = iso
    else:
        if out.start_date and out.start_date != iso:
            logger.warning(
                "[TEMPORAL_RESOLVER] named-month mismatch repair start: "
                "expr=%r stage2=%s oracle=%s",
                phrase,
                out.start_date,
                iso,
            )
        out.start_date = iso
        # Named-month day phrases are single calendar days.
        out.end_date = None
        if mode != "none":
            out.mode = mode


def resolve_temporal(temporal: Temporal, now: datetime, timezone: str = "UTC") -> Temporal:
    """
    Validate / repair Temporal against closed and named-month vocabulary.

    - Invalid ISO → dropped (then filled from supported expression if possible)
    - Closed expression ↔ ISO mismatch → repair to oracle ISO (log warning)
    - Closed / named-month expression without ISO → fill ISO
    - Open expressions (next month, etc.) → leave Stage2 ISO; format-check only
    - Preserves expressions for audit
    - Confidence ignored for decisions
    """
    del timezone  # reserved; ``now`` must already be tenant-local

    out = Temporal(
        expression=temporal.expression,
        start_date_expression=temporal.start_date_expression,
        start_time_expression=temporal.start_time_expression,
        end_date_expression=temporal.end_date_expression,
        end_time_expression=temporal.end_time_expression,
        start_date=_parse_iso(temporal.start_date),
        start_time=temporal.start_time,
        end_date=_parse_iso(temporal.end_date),
        end_time=temporal.end_time,
        mode=temporal.mode if temporal.mode in TEMPORAL_MODES else None,
        confidence=temporal.confidence,
        resolution=temporal.resolution,
    )
    if temporal.start_date and out.start_date is None:
        logger.warning(
            "[TEMPORAL_RESOLVER] invalid start_date dropped: %r", temporal.start_date
        )
    if temporal.end_date and out.end_date is None:
        logger.warning(
            "[TEMPORAL_RESOLVER] invalid end_date dropped: %r", temporal.end_date
        )

    start_phrase = _normalize_phrase(out.start_date_expression)
    end_phrase = _normalize_phrase(out.end_date_expression)

    # Prefer start phrase for closed relative; end-only closed phrases are rare.
    primary = start_phrase
    if primary and is_closed_vocabulary(primary):
        oracle_start, oracle_end, oracle_mode = resolve_closed_phrase(primary, now)
        if oracle_start:
            if out.start_date and out.start_date != oracle_start:
                logger.warning(
                    "[TEMPORAL_RESOLVER] mismatch repair start: expr=%r stage2=%s oracle=%s",
                    primary,
                    out.start_date,
                    oracle_start,
                )
                out.start_date = oracle_start
            elif not out.start_date:
                out.start_date = oracle_start

            if oracle_end:
                if out.end_date and out.end_date != oracle_end:
                    logger.warning(
                        "[TEMPORAL_RESOLVER] mismatch repair end: expr=%r stage2=%s oracle=%s",
                        primary,
                        out.end_date,
                        oracle_end,
                    )
                out.end_date = oracle_end
            elif oracle_mode == "single_day":
                # Single-day closed phrases should not keep a spurious end.
                out.end_date = None

            if oracle_mode != "none":
                out.mode = oracle_mode

    elif end_phrase and is_closed_vocabulary(end_phrase) and not start_phrase:
        oracle_start, oracle_end, oracle_mode = resolve_closed_phrase(end_phrase, now)
        if oracle_end or oracle_start:
            # End-only expression: treat as end bound when range; else start.
            if oracle_end and oracle_start:
                out.start_date = out.start_date or oracle_start
                out.end_date = oracle_end
            else:
                out.end_date = oracle_start
            if oracle_mode != "none":
                out.mode = oracle_mode
    else:
        # Named-month day phrases (deterministic; Stage2 may leave ISO null).
        if start_phrase and is_named_month_expression(start_phrase):
            _apply_named_month_resolution(out, start_phrase, now, as_end=False)
        elif (
            end_phrase
            and is_named_month_expression(end_phrase)
            and not start_phrase
        ):
            _apply_named_month_resolution(out, end_phrase, now, as_end=True)

    out.start_date, out.end_date = _repair_order(out.start_date, out.end_date)

    if not out.mode or out.mode not in TEMPORAL_MODES:
        out.mode = _infer_mode(out)
    elif out.mode == "none" and (
        out.start_date or out.start_date_expression or out.end_date
    ):
        out.mode = _infer_mode(out)

    return out


def resolve_day_with_anchor(
    day: int,
    anchor_iso: str,
    now: datetime,
) -> Optional[str]:
    """
    Combine ``day`` with month/year from ``anchor_iso`` (YYYY-MM-DD).

    Invalid calendar combinations return None. ``now`` is unused today but
    reserved for future rollover policy; keep signature stable.
    """
    del now
    anchor = _parse_iso(anchor_iso)
    if not anchor:
        return None
    try:
        base = datetime.strptime(anchor, "%Y-%m-%d")
        candidate = base.replace(day=day)
    except ValueError:
        return None
    return _iso(candidate)


def _anchor_iso_from_conversation(
    conversation_context: Optional[dict],
) -> Optional[str]:
    if not isinstance(conversation_context, dict):
        return None
    last_dp = conversation_context.get("last_date_proposal")
    if isinstance(last_dp, dict):
        start = last_dp.get("start")
        parsed = _parse_iso(str(start) if start is not None else None)
        if parsed:
            return parsed
    return None


def apply_bare_ordinal_revision(
    temporal: Temporal,
    *,
    conversation_context: Optional[dict],
    now: datetime,
) -> Temporal:
    """
    Bind bare ordinal ``start_date_expression`` to ISO using conversational month/year.

    Consumes only canonical Temporal + structured conversation context. Does not
    read raw utterances. Expression recovery is NLU preprocess
    (``inject_bare_ordinal_expression``).
    """
    from .bare_ordinal import parse_bare_ordinal_expression

    if temporal.start_date:
        return temporal

    parsed = parse_bare_ordinal_expression(temporal.start_date_expression)
    if not parsed:
        return temporal

    day, expr = parsed
    anchor = _anchor_iso_from_conversation(conversation_context)
    if not anchor:
        return temporal

    iso = resolve_day_with_anchor(day, anchor, now)
    if not iso:
        logger.info(
            "[TEMPORAL_RESOLVER] bare ordinal %r invalid for anchor %s",
            expr,
            anchor,
        )
        return temporal

    logger.info(
        "[TEMPORAL_RESOLVER] bare ordinal revision: expr=%r anchor=%s -> %s",
        expr,
        anchor,
        iso,
    )
    return Temporal(
        expression=temporal.expression or expr,
        start_date_expression=expr,
        start_time_expression=temporal.start_time_expression,
        end_date_expression=None,
        end_time_expression=temporal.end_time_expression,
        start_date=iso,
        start_time=temporal.start_time,
        end_date=None,
        end_time=temporal.end_time,
        mode="single_day",
        confidence=temporal.confidence,
        resolution=temporal.resolution,
    )


def anchor_now(now: Union[datetime, str], timezone_name: str = "UTC") -> datetime:
    """Parse request now and convert to tenant timezone for Stage2/Resolver."""
    from ..calendar.calendar_binder import get_timezone, _localize_datetime

    if isinstance(now, str):
        raw = now.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            dt = datetime.utcnow()
    else:
        dt = now

    tz = get_timezone(timezone_name)
    if dt.tzinfo is None:
        return _localize_datetime(dt, tz)
    # Convert to tenant tz
    try:
        return dt.astimezone(tz)
    except Exception:
        return _localize_datetime(dt.replace(tzinfo=None), tz)


def format_prompt_now(now: datetime, timezone_name: str) -> str:
    """Human-readable local now for Stage2 prompts."""
    local = now
    return (
        f"{local.strftime('%Y-%m-%dT%H:%M:%S')} "
        f"(local date {local.strftime('%Y-%m-%d')}, "
        f"weekday {local.strftime('%A')}, timezone {timezone_name})"
    )
