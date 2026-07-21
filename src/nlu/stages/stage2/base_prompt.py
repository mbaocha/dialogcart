"""
Stage 2 shared prompt components — DATE RULES, TIME RULES, BOOKING ID RULES.

Each Stage 2 group composes its prompt from the blocks it needs.
Extracted from nlu/slm/extractor.py so each group imports only what's relevant.
"""
from typing import Any, Dict, List, Optional

from ...registry.intent_groups import ALL_INTENTS, RAG_INTENTS, format_intent_registry


def date_rules(now: str) -> str:
    """Legacy date rules kept for documentation/tests; prefer temporal_rules()."""
    return f"""── DATE RULES ──────────────────────────────────────────────────────────────
CRITICAL: Only extract dates the user explicitly mentions. Never default to today/current date.
  "book haircut at 10am"  → dates=[], date_time_pairs=[]  (time only — date NOT mentioned)
  "at 3pm"                → dates=[], times=["15:00"]
date_time_pairs: ONLY when BOTH date AND time appear in the same utterance (e.g. "tomorrow at 3pm").
Named-month dates (no year): month > current → current year; month < current → next year.
Current date/time: {now}
Example: "march 3"→2026-03-03, "jan 10"→2026-01-10, "dec 1"→2026-12-01.
Resolve named-month dates to ISO YYYY-MM-DD. For weekday/relative terms, preserve user's exact words
in dates[] — bare "friday" stays "friday", "next monday" stays "next monday", "tomorrow" stays "tomorrow".
EXCEPTION — CORRECTION date replacement: when replacing a date ("saturday instead", "no make it friday"),
resolve the new date to ISO YYYY-MM-DD using current date/time.
For date ranges ("mar 5 through 8", "from may 1 to 10"), output exactly [start_date, end_date] — never enumerate intermediate dates.
Numeric format is DD/MM (day first): "04/03"→day=4,month=3→2026-03-04."""


def time_rules() -> str:
    """Legacy time rules kept for documentation/tests; prefer temporal_rules()."""
    return """── TIME RULES ──────────────────────────────────────────────────────────────
Exact times → mode=exact, start=HH:MM, end=HH:MM (same value), label=null; also in facts.times.
  ("3pm"→15:00, "9am"→09:00, "noon"→12:00, "midnight"→00:00)
"after X" → mode=exact, start=HH:MM, end="23:59".
"from X" / "by X" → mode=exact, start=end=HH:MM.
Named windows (morning/afternoon/evening/night) → mode=fuzzy, label=<name>, times=[].
No time → time_constraint=null, times=[]. ALWAYS extract times even for UNKNOWN intent."""


def temporal_rules(now: str) -> str:
    return f"""── TEMPORAL RULES ──────────────────────────────────────────────────────────
Populate the temporal object only. Do NOT invent dates or times the user did not mention.
Anchor (tenant-local): {now}

Fields:
- expression: full temporal phrase as spoken when useful (e.g. "tomorrow at 9am"), else null
- start_date_expression / end_date_expression: ALWAYS keep the user's exact date words for
  relatives/weekdays/week/weekend ("tomorrow", "next monday", "this weekend"). Also keep for
  named-month if helpful; null only when there were no date words (ISO-only / time-only).
- start_time_expression / end_time_expression: non-clock labels only (morning/afternoon/evening/night).
- start_date / end_date: ALWAYS resolve to ISO YYYY-MM-DD using the anchor above whenever the user
  mentioned a date (relatives, weekdays, weeks, weekends, named months, numeric).
- start_time / end_time: HH:MM 24h when an exact clock time is known; null otherwise
- mode: none | single_day | range | flexible
    single_day = one day; range = closed stay/range; flexible = week/weekend period (not a committed day);
    none = no date material
- confidence: 0.0–1.0 (telemetry only; does not change binding)

Date resolution policy (use local weekday/date from the anchor):
- today / tomorrow / yesterday → ISO for that local calendar day; keep expression; mode=single_day
- bare weekday ("friday"): upcoming occurrence; if today IS that weekday → next week's that day; mode=single_day
- "this <weekday>": same as bare weekday; mode=single_day
- "next <weekday>": that weekday in the NEXT Monday-based calendar week; mode=single_day
- "this weekend" / "weekend": upcoming Sat–Sun (if today Sat → this Sat–Sun); mode=flexible; set start_date+end_date
- "next weekend": Sat–Sun of the following weekend; mode=flexible
- "this week": Mon–Sun of the current week; mode=flexible
- "next week": Mon–Sun of the next week; mode=flexible
- "next month": first day of next calendar month ISO; keep expression; mode=single_day
- "in two weeks": local date + 14 days ISO; keep expression; mode=single_day
- Named-month (no year): month > current → current year; month < current → next year; mode=single_day or range
- Ranges ("mar 5 through 8"): start_date+end_date ISO; mode=range — never enumerate intermediate days
- Numeric dates are DD/MM: "04/03"→2026-03-04
- Time-only ("book haircut at 10am"): all date fields null; mode=none; start_time="10:00"
- CORRECTION ("saturday instead"): resolve ISO + keep expression; mode=single_day
- Bare ordinal day revision ("23rd", "24th", "15th", "show slots for 23rd") when conversation
  context already established a month/year (Last date proposal and/or prior availability turns):
  keep start_date_expression as the user's ordinal words (e.g. "23rd"); resolve start_date to ISO
  using that conversational month/year (not today); mode=single_day.
  Do NOT leave mode=none and do NOT invent a different month.
  Without an established conversational date, leave temporal empty (mode=none) — do not guess.

Ambiguous examples (anchor = Tuesday 2026-07-07 local):
  "tomorrow" → start_date_expression="tomorrow", start_date=2026-07-08, mode=single_day
  "friday" / "this friday" → start_date=2026-07-10, mode=single_day
  "next wednesday" → start_date=2026-07-15 (Wednesday of next calendar week), mode=single_day
  "this weekend" → start_date=2026-07-11, end_date=2026-07-12, mode=flexible
  "next week" → start_date=2026-07-13, end_date=2026-07-19, mode=flexible
  "book facial next week" → same dates, mode=flexible (do NOT treat as a single committed day)
  After prior turn searched July 22 (Last date proposal 2026-07-22):
    "show slots for 23rd" / "23rd" → start_date_expression="23rd", start_date=2026-07-23, mode=single_day
    "24th" → start_date_expression="24th", start_date=2026-07-24, mode=single_day
    "july 23rd" → start_date_expression="july 23rd", start_date=2026-07-23, mode=single_day (unchanged)

Time resolution:
- Exact clock → start_time HH:MM ("3pm"→15:00, "9am"→09:00, "noon"→12:00, "midnight"→00:00).
- "after X" → start_time=HH:MM, end_time="23:59".
- "from X" / "by X" → start_time=end_time=HH:MM.
- Named windows → start_time_expression=morning|afternoon|evening|night; start_time/end_time null.
- No time mentioned → all time fields null.

Examples:
  "book haircut tomorrow at 9am"
    → start_date_expression="tomorrow", start_date=<ISO>, start_time="09:00",
      expression="tomorrow at 9am", mode=single_day
  "reserve room march 5 to 10"
    → start_date=ISO, end_date=ISO, mode=range
  "book massage tomorrow afternoon"
    → start_date_expression="tomorrow", start_date=<ISO>, start_time_expression="afternoon", mode=single_day
  "at 3pm"
    → start_time="15:00" only, mode=none"""


def booking_id_rules(tenant_context: Optional[Dict[str, Any]] = None) -> str:
    from ...config.booking_id import DEFAULT_BOOKING_ID_PATTERN, get_booking_id_settings

    _, _, examples = get_booking_id_settings(tenant_context or {})
    raw = (tenant_context or {}).get("booking_id") or {}
    pattern = raw.get("pattern") or DEFAULT_BOOKING_ID_PATTERN

    lines = [
        "── BOOKING ID RULES ────────────────────────────────────────────────────────",
        "Populate facts.booking_id only for clear booking reference tokens — never dates, times, or services.",
        f"Default ID shape (case-insensitive): {pattern}  (e.g. ABC123 — 2+ letters + 3+ digits).",
        "Do NOT set booking_id from vague phrases like \"my booking\" with no reference token.",
        "DO set booking_id when the user gives a standalone ID, an explicit anchor (#ABC123, \"booking id: …\"),",
        '  or "booking/reservation <ID>" when <ID> matches the format.',
        "When unsure, leave booking_id null — the pipeline regex is authoritative.",
    ]
    if examples:
        lines.append(f"Tenant booking ID examples (hints only): {', '.join(examples)}")
    return "\n".join(lines)


def service_rules(aliases: Dict[str, str]) -> str:
    keys = ", ".join(f'"{k}"' for k in aliases) if aliases else "none provided"
    return f"""── SERVICE RULES ───────────────────────────────────────────────────────────
KNOWN SERVICE ALIASES (for reference only): {keys}

Extract the user's service phrase EXACTLY as they said it into facts.service_term.
Do NOT resolve, correct, or match it against the aliases — code handles that.
- User says "book premiun haircut" → service_term = "premiun haircut"  (typo preserved)
- User says "massage"             → service_term = "massage"
- User says "book tomorrow at 3pm" (no service) → service_term = null
service_term must be null when no service is mentioned.
ALWAYS attempt service extraction even for UNKNOWN intent."""


def search_query_rules() -> str:
    rag = ", ".join(sorted(RAG_INTENTS))
    return f"""── SEARCH QUERY RULES ──────────────────────────────────────────────────────
Populate search_query ONLY for: {rag}.
Strip conversational filler ("tell me about", "what are", "do you have") and return the core noun phrase.
Examples:
  "tell me about deep tissue massage"  → "deep tissue massage"
  "how much does a haircut cost"       → "haircut price"
  "what services do you offer"         → "available services"
For all other intents, search_query must be null."""


def intent_validation_section(candidate_intent: str) -> str:
    """Compact intent list for Stage 2 validation / re-routing."""
    unclear_rule = (
        "If still unclear with no active booking context, keep "
        f"{candidate_intent}."
    )
    if candidate_intent == "UNKNOWN":
        unclear_rule = (
            "If still unclear and there is NO active booking context in "
            "CONVERSATION CONTEXT, keep UNKNOWN."
        )
    return f"""════════════════════════════════════════
INTENT VALIDATION
════════════════════════════════════════
Stage 1 classified this message as: {candidate_intent}

You are the final authority on intent. If after seeing the full slot context
you determine a different intent is correct, set validated_intent accordingly.
{unclear_rule}

Supported intents:
{format_intent_registry()}"""


# ── Shared tool schema components ────────────────────────────────────────────

_NON_TEMPORAL_FACT_FIELDS = {
    "service_id": {
        "type": ["string", "null"],
        "description": "Closest matching key from KNOWN SERVICE ALIASES, or null.",
    },
    "service_term": {
        "type": ["string", "null"],
        "description": (
            "Raw service phrase the user mentioned, as spoken (typos preserved). "
            "Null if no service was mentioned. Do NOT resolve against aliases."
        ),
    },
    "booking_id": {
        "type": ["string", "null"],
        "description": "Booking reference ID when user states one matching the platform format. Null if absent.",
    },
}


def _temporal_schema() -> dict:
    nullable_str = {"type": ["string", "null"]}
    return {
        "type": ["object", "null"],
        "description": "Canonical temporal understanding for this utterance. Null only if no date/time mentioned.",
        "properties": {
            "expression": {
                **nullable_str,
                "description": "Full temporal phrase as spoken, when useful.",
            },
            "start_date_expression": {
                **nullable_str,
                "description": "User's start-date words (tomorrow, friday, next monday).",
            },
            "start_time_expression": {
                **nullable_str,
                "description": "Fuzzy window label (morning/afternoon/evening/night), else null.",
            },
            "end_date_expression": {
                **nullable_str,
                "description": "User's end-date words for ranges, else null.",
            },
            "end_time_expression": {
                **nullable_str,
                "description": "Fuzzy end window if any, else null.",
            },
            "start_date": {
                **nullable_str,
                "description": "ISO YYYY-MM-DD resolved against tenant-local now for any mentioned date.",
            },
            "start_time": {
                **nullable_str,
                "description": "HH:MM 24h exact start time, else null.",
            },
            "end_date": {
                **nullable_str,
                "description": "ISO YYYY-MM-DD range end when resolved, else null.",
            },
            "end_time": {
                **nullable_str,
                "description": "HH:MM 24h exact end time when distinct, else null.",
            },
            "mode": {
                "type": ["string", "null"],
                "description": "none|single_day|range|flexible (week/weekend periods are flexible).",
            },
            "confidence": {
                "type": ["number", "null"],
                "description": "Temporal extraction confidence 0.0–1.0 (telemetry only).",
            },
        },
        "required": [
            "expression",
            "start_date_expression",
            "start_time_expression",
            "end_date_expression",
            "end_time_expression",
            "start_date",
            "start_time",
            "end_date",
            "end_time",
            "mode",
            "confidence",
        ],
    }


def _facts_schema(include: list) -> dict:
    """Build facts sub-schema including only non-temporal field names."""
    props = {k: _NON_TEMPORAL_FACT_FIELDS[k] for k in include if k in _NON_TEMPORAL_FACT_FIELDS}
    return {
        "type": "object",
        "properties": props,
        "required": list(props.keys()),
    }


def build_tool(
    name: str,
    description: str,
    facts_fields: Optional[List[str]] = None,
    include_time_constraint: bool = False,
    include_search_query: bool = False,
    include_off_topic_query: bool = False,
    include_validated_intent: bool = True,
    include_service_candidates: bool = False,
    include_operation: bool = False,
    include_temporal: bool = False,
) -> dict:
    """Build a Stage 2 tool schema for a specific group."""
    facts_fields = list(facts_fields or [])
    # Temporal-first groups must not ask the LLM for legacy date/time bags.
    temporal_legacy = {"dates", "times", "date_time_pairs"}
    if include_temporal:
        facts_fields = [f for f in facts_fields if f not in temporal_legacy]

    props: dict = {}
    required: list = []

    if include_validated_intent:
        props["validated_intent"] = {
            "type": ["string", "null"],
            "enum": ALL_INTENTS + [None],
            "description": (
                "Override Stage 1's intent if you determine a different one is correct. "
                "null to accept Stage 1's classification."
            ),
        }
        required.append("validated_intent")

    props["confidence"] = {
        "type": "number",
        "description": "Confidence score 0.0–1.0.",
    }
    required.append("confidence")

    if include_temporal:
        props["temporal"] = _temporal_schema()
        required.append("temporal")

    if facts_fields:
        props["facts"] = _facts_schema(facts_fields)
        required.append("facts")

    # Legacy time_constraint only when not using Temporal ownership.
    if include_time_constraint and not include_temporal:
        props["time_constraint"] = {
            "type": ["object", "null"],
            "description": "Structured time constraint. null if no time is mentioned.",
            "properties": {
                "mode": {"type": "string", "enum": ["exact", "fuzzy"]},
                "start": {"type": "string", "description": "Start bound HH:MM."},
                "end": {"type": "string", "description": "End bound HH:MM."},
                "label": {
                    "type": ["string", "null"],
                    "description": "Window name for fuzzy, null for exact.",
                },
            },
            "required": ["mode", "start", "end", "label"],
        }
        required.append("time_constraint")

    if include_search_query:
        props["search_query"] = {
            "type": ["string", "null"],
            "description": "Normalised search string for RAG lookup. Null for non-RAG intents.",
        }
        required.append("search_query")

    if include_off_topic_query:
        props["off_topic_query"] = {
            "type": ["string", "null"],
            "description": (
                "Canonical off-topic question for OFF_TOPIC only "
                "(clear standalone question; meaning preserved). "
                "Null for all other intents. Never a business FAQ search_query."
            ),
        }
        required.append("off_topic_query")

    if include_service_candidates:
        props["service_candidates"] = {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Alias keys for the user to choose from. "
                "Set to matching aliases when ambiguous; set to ALL alias keys when no service was mentioned; "
                "empty only when service_id is resolved or user mentioned a service not in the catalog."
            ),
        }
        required.append("service_candidates")

    if include_operation:
        props["operation"] = {
            "type": ["string", "null"],
            "enum": ["browse_next", "browse_previous", None],
            "description": (
                "Set when the user is navigating previously presented availability "
                "(browse_next or browse_previous). Null for new availability searches."
            ),
        }
        required.append("operation")

    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": props,
            "required": required,
        },
    }
