"""Shared availability browse ``operation`` contract for Stage 2 extractors.

Browse is an interaction subtype under an intent. Core consumes structured
``browse_next`` / ``browse_previous``; Stage 2 must emit it for the whole
browse lifecycle — including after an exhaustion assistant reply.
"""

from __future__ import annotations

from typing import Any, Optional

VALID_OPERATIONS = frozenset({"browse_next", "browse_previous"})


def operation_rules() -> str:
    return """── AVAILABILITY OPERATION ───────────────────────────────────────────────────
Set operation when the user is navigating previously presented availability — not
requesting a new search. Otherwise leave operation null.

browse_next — user wants the next page of times from a prior result:
  "next", "show more", "more"
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

Do NOT set browse_next/browse_previous for date navigation. Phrases such as
"next day", "previous day", "later date", "earlier date", "tomorrow", or an
explicit calendar date are new availability searches → operation = null and
extract temporal as usual.

New availability queries (dates, services, "what times are free") → operation = null."""


def normalize_operation(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    operation = str(raw).strip().lower().replace("-", "_")
    if operation in VALID_OPERATIONS:
        return operation
    return None
