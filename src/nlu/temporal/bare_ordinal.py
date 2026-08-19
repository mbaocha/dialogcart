"""
Bare ordinal day revisions — NLU language recovery before TemporalResolver.

Stage2 sometimes drops bare ordinals (``23rd``, ``show slots for 15th``).
This module recovers them into canonical Temporal expressions. The resolver
then binds ISO against conversational ``last_date_proposal`` without reading
raw English.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

from .models import Temporal
from .pipeline_sync import apply_temporal, get_temporal

logger = logging.getLogger(__name__)

_BARE_ORDINAL_RE = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})(?P<suffix>st|nd|rd|th)\b",
    re.IGNORECASE,
)

_MONTH_TOKEN_RE = re.compile(
    r"\b(?:"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r")\b",
    re.IGNORECASE,
)


def _normalize_phrase(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    phrase = " ".join(str(value).strip().lower().split())
    return phrase or None


def is_bare_ordinal_expression(expression: Optional[str]) -> bool:
    """True when expression is only an ordinal day (e.g. ``23rd``), no month."""
    phrase = _normalize_phrase(expression)
    if not phrase:
        return False
    if _MONTH_TOKEN_RE.search(phrase):
        return False
    match = _BARE_ORDINAL_RE.fullmatch(phrase)
    if not match:
        return False
    day = int(match.group("day"))
    return 1 <= day <= 31


def parse_bare_ordinal_expression(
    expression: Optional[str],
) -> Optional[Tuple[int, str]]:
    """
    Parse a canonical bare-ordinal expression (full phrase only).

    Returns ``(day, normalized_expression)`` or None.
    """
    phrase = _normalize_phrase(expression)
    if not phrase or not is_bare_ordinal_expression(phrase):
        return None
    match = _BARE_ORDINAL_RE.fullmatch(phrase)
    if not match:
        return None
    return int(match.group("day")), match.group(0).lower()


def extract_bare_ordinal_from_utterance(
    text: Optional[str],
) -> Optional[Tuple[int, str]]:
    """
    Extract a single bare ordinal day from raw user text.

    Returns ``(day, matched_expression)`` or None when:
    - no ordinal present,
    - a month name is also present (named-month path owns that),
    - more than one ordinal appears (ambiguous).
    """
    if not text or not str(text).strip():
        return None
    raw = str(text)
    if _MONTH_TOKEN_RE.search(raw):
        return None
    matches = list(_BARE_ORDINAL_RE.finditer(raw))
    if len(matches) != 1:
        return None
    match = matches[0]
    day = int(match.group("day"))
    if day < 1 or day > 31:
        return None
    return day, match.group(0).lower()


def _temporal_has_non_ordinal_date_hint(temporal: Temporal) -> bool:
    if temporal.start_date or temporal.end_date or temporal.end_date_expression:
        return True
    phrase = _normalize_phrase(temporal.start_date_expression)
    if not phrase:
        return False
    if is_bare_ordinal_expression(phrase):
        return False
    return True


def inject_bare_ordinal_expression(
    text: str,
    slm: Dict[str, Any],
) -> Dict[str, Any]:
    """
    NLU preprocess: if Stage2 left no date material, recover bare ordinal
    into ``start_date_expression`` for TemporalResolver to bind.
    """
    temporal = get_temporal(slm)
    if temporal.start_date:
        return slm
    start_phrase = _normalize_phrase(temporal.start_date_expression)
    if start_phrase and is_bare_ordinal_expression(start_phrase):
        return slm
    if _temporal_has_non_ordinal_date_hint(temporal):
        return slm

    extracted = extract_bare_ordinal_from_utterance(text)
    if not extracted:
        return slm

    _day, expr = extracted
    logger.info(
        "[BARE_ORDINAL] inject start_date_expression=%r from text=%r",
        expr,
        text,
    )
    filled = Temporal(
        expression=temporal.expression or expr,
        start_date_expression=expr,
        start_time_expression=temporal.start_time_expression,
        end_date_expression=None,
        end_time_expression=temporal.end_time_expression,
        start_date=None,
        start_time=temporal.start_time,
        end_date=None,
        end_time=temporal.end_time,
        mode="none",
        confidence=temporal.confidence,
        resolution=temporal.resolution,
    )
    return apply_temporal(slm, filled)
