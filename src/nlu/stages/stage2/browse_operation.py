"""Shared availability browse ``operation`` contract for Stage 2 extractors.

Browse is an interaction subtype under an intent. Core consumes structured
``browse_next`` / ``browse_previous``; Stage 2 must emit it for the whole
browse lifecycle — including after an exhaustion assistant reply.

Deterministic utterance normalisation at the NLU boundary recovers browse
when the model omits ``operation``, and strips browse when the utterance is
temporal search language. Cold ambiguous words are not classified as
pagination without presented-availability context.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

from ...temporal.stage2_output import empty_temporal_dict

VALID_OPERATIONS = frozenset({"browse_next", "browse_previous"})

_BROWSE_NEXT_PHRASES = frozenset(
    {
        "next",
        "more",
        "show more",
        "show more times",
        "show me more",
        "show me more times",
        "show me additional times",
        "show additional times",
        "show me the next times",
    }
)
_BROWSE_PREVIOUS_PHRASES = frozenset(
    {
        "previous",
        "show previous",
        "back",
        "previous times",
        "go back",
    }
)

_BROWSE_NEXT_RE = re.compile(
    r"^(?:are there|do you have)\s+(?:any\s+)?more\s+times"
    r"(?:\s+for\s+.+)?$",
    re.IGNORECASE,
)

_FILLER_PREFIX_RE = re.compile(
    r"^(?:please|thanks|thank you|can you|could you|would you)\s+",
    re.IGNORECASE,
)
_FILLER_SUFFIX_RE = re.compile(
    r"\s+(?:please|thanks|thank you)$",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)

_WEEKDAYS = frozenset(
    {
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    }
)
_RELATIVE_DATE_PHRASES = (
    "today",
    "tomorrow",
    "yesterday",
    "next week",
    "this weekend",
    "next weekend",
    "next day",
    "previous day",
    "later date",
    "earlier date",
    "another day",
    "following day",
)
_MONTH_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")
_NEXT_THIS_LAST_RE = re.compile(
    r"\b(?:next|this|last)\s+(?:day|week|weekend|month|year)\b",
    re.IGNORECASE,
)
_CLOCK_OPTION_RE = re.compile(
    r"\b(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)
_AVAILABILITY_PRESENTATION_MARKERS = (
    "available times",
    "available appointment",
    "appointment times",
    "show more",
    "additional times",
    "no more times",
    "nothing more",
    "no more availability",
    "`next`",
    "`previous`",
)


def operation_rules() -> str:
    return """── AVAILABILITY OPERATION ───────────────────────────────────────────────────
Set operation when the user is navigating previously presented availability — not
requesting a new search. Otherwise leave operation null.

browse_next — user wants the next page of times from a prior result:
  "next", "show more", "more", "show more times", "show me additional times",
  "are there more times?"
  → operation = "browse_next"
  → temporal must be null (no date/time extraction)

browse_previous — user wants the previous page of times from a prior result:
  "previous", "show previous", "back"
  → operation = "browse_previous"
  → temporal must be null

EXHAUSTION CONTINUITY (critical):
When the prior assistant message said there are no more times / nothing more to
show / browse is exhausted, and the user repeats the same browse-next language:
  → STILL set operation = "browse_next"
  → Do NOT leave operation null
  → Do NOT treat the utterance as unrecognized gibberish
  → Do NOT invent a new date/service search
Core owns exhaustion messaging; NLU must keep emitting the browse signal.

When operation is browse_next or browse_previous, set validated_intent to
AVAILABILITY (browse is an AVAILABILITY subtype, not a CREATE slot fill).

Repeating the date of the current presentation does not make browse language a
new search: "are there more times for July 20?" remains browse_next when the
presented availability is for July 20.

Do NOT set browse_next/browse_previous for revised date navigation. Phrases such as
"next day", "previous day", "later date", "earlier date", "tomorrow",
"next Tuesday", "next week", "the next available Tuesday", "go back to Tuesday",
or an explicit calendar date are new availability searches → operation = null
and extract temporal as usual.

New availability queries (dates, services, "what times are free") → operation = null."""


def normalize_operation(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    operation = str(raw).strip().lower().replace("-", "_")
    if operation in VALID_OPERATIONS:
        return operation
    return None


def normalize_browse_utterance(text: str) -> str:
    """Lowercase, strip punctuation/fillers, collapse whitespace."""
    lowered = " ".join((text or "").lower().split())
    stripped = _PUNCT_RE.sub(" ", lowered)
    normalized = " ".join(stripped.split())
    changed = True
    while changed and normalized:
        updated = _FILLER_PREFIX_RE.sub("", normalized)
        updated = _FILLER_SUFFIX_RE.sub("", updated)
        updated = " ".join(updated.split())
        changed = updated != normalized
        normalized = updated
    return normalized


def match_browse_direction(text: str) -> Optional[str]:
    """Return browse_next / browse_previous when the whole utterance is a browse phrase."""
    normalized = normalize_browse_utterance(text)
    if not normalized:
        return None
    if normalized in _BROWSE_NEXT_PHRASES:
        return "browse_next"
    if normalized in _BROWSE_PREVIOUS_PHRASES:
        return "browse_previous"
    if _BROWSE_NEXT_RE.fullmatch(normalized):
        return "browse_next"
    return None


def utterance_has_date_language(text: str) -> bool:
    """True when the utterance expresses a date/search criterion, not page movement."""
    lowered = " ".join((text or "").lower().split())
    if not lowered:
        return False
    if any(phrase in lowered for phrase in _RELATIVE_DATE_PHRASES):
        return True
    if any(re.search(rf"\b{day}\b", lowered) for day in _WEEKDAYS):
        return True
    if _MONTH_RE.search(lowered) or _NUMERIC_DATE_RE.search(lowered):
        return True
    if _NEXT_THIS_LAST_RE.search(lowered):
        return True
    return False


def _iter_assistant_texts(ctx: Dict[str, Any]) -> Iterable[str]:
    messages = ctx.get("messages")
    if isinstance(messages, list):
        for item in messages:
            if not isinstance(item, dict):
                continue
            if str(item.get("role") or "").lower() != "assistant":
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                yield text
    turns = ctx.get("turns")
    if isinstance(turns, list):
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            text = turn.get("assistant")
            if isinstance(text, str) and text.strip():
                yield text


def _assistant_presented_availability(text: str) -> bool:
    lowered = text.lower()
    if any(marker in lowered for marker in _AVAILABILITY_PRESENTATION_MARKERS):
        return True
    # A list of at least two clock-formatted choices is presentation evidence;
    # a lone clock may instead be a confirmation or ordinary prose reference.
    return len(_CLOCK_OPTION_RE.findall(text)) >= 2


def conversation_presented_availability(
    conversation_context: Optional[Dict[str, Any]],
) -> bool:
    """True when Core-supplied context shows availability was already presented.

    Structured ``presented_options`` is preferred. Assistant history is retained
    as compatibility evidence for contexts produced before that contract.
    Intent history alone is not presentation evidence.
    """
    if not isinstance(conversation_context, dict) or not conversation_context:
        return False
    presented_options = conversation_context.get("presented_options")
    if isinstance(presented_options, dict):
        reference = presented_options.get("reference")
        options = presented_options.get("options")
        if isinstance(reference, str) and reference and isinstance(options, list) and options:
            return True
    for assistant in _iter_assistant_texts(conversation_context):
        if _assistant_presented_availability(assistant):
            return True
    return False


def _active_presented_dates(
    conversation_context: Optional[Dict[str, Any]],
) -> frozenset[str]:
    """Return ISO dates represented by Core's structured current presentation."""
    if not isinstance(conversation_context, dict):
        return frozenset()
    dates = set()
    presented = conversation_context.get("presented_options")
    options = presented.get("options") if isinstance(presented, dict) else None
    if isinstance(options, list):
        for option in options:
            if not isinstance(option, dict):
                continue
            for key in ("starts_at", "start", "datetime"):
                value = option.get(key)
                if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value):
                    dates.add(value[:10])
                    break
    proposal = conversation_context.get("last_date_proposal")
    if isinstance(proposal, dict):
        value = proposal.get("start") or proposal.get("start_date")
        if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value):
            dates.add(value[:10])
    return frozenset(dates)


def _utterance_dates(slm: Dict[str, Any]) -> frozenset[str]:
    """Read model-resolved current-turn dates without parsing language in Core."""
    dates = set()
    facts = slm.get("facts")
    if isinstance(facts, dict):
        for value in facts.get("dates") or []:
            if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value):
                dates.add(value[:10])
    temporal = slm.get("temporal")
    if isinstance(temporal, dict):
        for key in ("start_date", "end_date"):
            value = temporal.get(key)
            if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value):
                dates.add(value[:10])
    return frozenset(dates)


def _date_qualifier_matches_active_presentation(
    text: str,
    slm: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]],
) -> bool:
    """Allow a browse phrase to repeat, but not revise, the presented date."""
    active_dates = _active_presented_dates(conversation_context)
    resolved_dates = _utterance_dates(slm)
    if resolved_dates:
        return bool(active_dates) and resolved_dates.issubset(active_dates)

    # A model may omit temporal evidence for a browse utterance. Compare the
    # narrow month-name/day qualifier with the trusted structured date.
    match = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
        r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})\b",
        text,
        re.IGNORECASE,
    )
    if not match or not active_dates:
        return False
    month_names = (
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec",
    )
    month = month_names.index(match.group(1)[:3].lower()) + 1
    day = int(match.group(2))
    return any(date[5:10] == f"{month:02d}-{day:02d}" for date in active_dates)


def _clear_temporal_fields(slm: Dict[str, Any]) -> Dict[str, Any]:
    facts = slm.get("facts")
    facts = dict(facts) if isinstance(facts, dict) else {}
    facts["dates"] = []
    facts["times"] = []
    facts["date_time_pairs"] = []
    slm["facts"] = facts
    slm["time_constraint"] = None
    slm["temporal"] = empty_temporal_dict(float(slm.get("confidence") or 0.0))
    return slm


def _set_intent_availability(slm: Dict[str, Any]) -> Dict[str, Any]:
    intent = slm.get("intent")
    if isinstance(intent, dict):
        slm["intent"] = {**intent, "name": "AVAILABILITY"}
    else:
        slm["intent"] = "AVAILABILITY"
    return slm


def apply_deterministic_browse_operation(
    text: str,
    slm: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalise browse ``operation`` from the utterance and conversation context.

    Classification condition:
    - If the utterance contains date/search language → never browse (strip
      ``operation``; leave temporal evidence untouched).
    - Else if the whole utterance is a browse phrase AND availability was
      presented → ``operation`` + intent AVAILABILITY; clear invented temporal.
    - Else recover a known whole-utterance browse phrase deterministically.
    - Finally, retain any valid browse operation (including a model-emitted
      operation for an unknown phrase) only when availability presentation is
      evidenced by context; otherwise suppress it.
    """
    if not isinstance(slm, dict):
        return slm
    out = dict(slm)
    presented = conversation_presented_availability(conversation_context)
    direction = match_browse_direction(text)
    if utterance_has_date_language(text) and not (
        direction
        and presented
        and _date_qualifier_matches_active_presentation(
            text, out, conversation_context
        )
    ):
        out.pop("operation", None)
        return out

    if direction:
        out["operation"] = direction

    operation = normalize_operation(out.get("operation"))
    if operation is not None and not presented:
        out.pop("operation", None)
        return out
    if operation is not None:
        out["operation"] = operation
        out = _set_intent_availability(out)
        return _clear_temporal_fields(out)
    return out
