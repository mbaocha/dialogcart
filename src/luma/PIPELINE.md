# Luma Booking Pipeline — Stage-By-Stage Guide

Luma transforms free-text booking requests (service or reservation) into structured, bookable payloads. This document explains the full processing path and what each stage produces so you can debug or extend the system quickly.

## End-to-End Flow (stateless)
- **Caller → Orchestrator**: `core.orchestration.orchestrator.handle_message` derives the org domain, builds tenant aliases from the catalog, and calls `LumaClient.resolve(user_id, text, domain, timezone, tenant_context)`.
- **Contract check**: Response is validated with `assert_luma_contract`. If `success=false`, an error is returned immediately.
- **Clarification short-circuit**: If Luma sets `needs_clarification=true`, the orchestrator returns a `CLARIFY` outcome with a template key and data.
- **Resolved path**: When the intent is supported, the orchestrator proceeds to resolve catalog IDs and create the booking.

The rest of this doc focuses on what happens inside Luma (`luma` package) before the orchestrator receives the response.

## Pipeline Stages (inside `luma`)
Luma runs six ordered stages. Each stage enriches the payload that is exposed under `stages` in the final response. Any stage may emit clarification metadata if it detects ambiguity or missing information.

### 1) Entity Extraction (`luma/extraction`)
**Purpose:** Find raw entities and normalize them.

**Key behaviors**
- Detects services/room types/extras, dates, times, durations, and ordinals.
- Normalizes text into canonical keys (uses `tenant_context.aliases` when provided).
- Parameterizes the sentence to mark entity spans.
- Domain-scoped: only whitelisted entity types per domain (`DOMAIN_ENTITY_WHITELIST`).
- Pre-processing: orthography + natural-language variant normalization (`pre_normalization`, `normalize_hyphens`, `normalize_natural_language_variants`).
- Tenant alias handling: longest-match-first detection on normalized text, merged into services and filtered against generic matches (`detect_tenant_alias_spans`, `merge_alias_spans_into_services`).
- Uses spaCy pipeline initialized with business categories (`init_nlp_with_business_categories`), then `extract_entities_from_doc` and `build_parameterized_sentence`.
- Example inputs → outputs:
  - `"book haircut tomorrow at 2pm"` → services: `haircut` (canonical), date: `tomorrow`, time: `2pm`, psentence: `"book servicefamilytoken datetoken timetoken"`.
  - Tenant alias `"mens cut"` mapped via alias → canonical `beauty_and_wellness.haircut`; generic overlapping service removed.
- Disambiguation:
  - Longest alias wins to avoid partial overlaps (`mens cut` vs `cut`).
  - Generic service extractions overlapping explicit aliases are dropped.
  - Only whitelisted entity types per domain are emitted (e.g., no product entities in service domain).

**Outputs (example keys)**
- `services` / `service_families` / `rooms` / `extras`
- `dates`, `times`, `durations`
- `parameterized_text`
**Key files**
- `extraction/matcher.py`
- `extraction/entity_loading.py`, `extraction/entity_processing.py`

### 2) Intent Resolution (`luma/grouping/reservation_intent_resolver.py`)
**Purpose:** Decide what the user wants to do.

**Key behaviors**
- Canonical intents (10): `DISCOVERY`, `DETAILS`, `AVAILABILITY`, `QUOTE`, `RECOMMENDATION`, `CREATE_BOOKING`, `BOOKING_INQUIRY`, `MODIFY_BOOKING`, `CANCEL_BOOKING`, `PAYMENT` (+ `UNKNOWN` fallback).
- Deterministic rule ordering (first match wins). Precedence: destructive/sensitive → booking → informational → fallback.
- Signals used: regex lexical cues (cancel/pay/reschedule/etc.), presence of entities (services, dates, times, durations), booking verbs, availability phrasing, discovery phrasing.
- Confidence tiers: HIGH (0.95), MEDIUM (0.85), LOW (0.75) depending on rule strength.
- Example mappings:
  - `"cancel my haircut at 3"` → `CANCEL_BOOKING` (cancel patterns outrank booking context).
  - `"pay deposit for massage"` → `PAYMENT`.
  - `"can i get a haircut tomorrow at 2"` → `CREATE_BOOKING` (services + time).
  - `"what times are available for massage tomorrow"` → `AVAILABILITY`.
  - `"what do you offer?"` → `DISCOVERY`.
- Disambiguation:
  - Destructive actions (pay/cancel/modify) checked first to avoid misclassifying as booking.
  - Availability phrasing can override booking verbs if present.
  - Discovery requires services without time constraints (or explicit discovery phrasing); otherwise falls back to booking or availability.

**Outputs**
- `intent.intent` and `intent.confidence`
**Key logic (in order)**
- Payment language → `PAYMENT`
- Cancel → `CANCEL_BOOKING`
- Reschedule/change → `MODIFY_BOOKING`
- Services + time/duration → `CREATE_BOOKING` (unless explicitly availability phrasing); fallback: booking verbs + time signals.
- Booking inquiry (existing booking questions) → `BOOKING_INQUIRY`
- Availability phrasing → `AVAILABILITY` (with/without entities)
- Details (attributes) → `DETAILS`
- Quote (price) → `QUOTE`
- Discovery (services present, no time constraints, or discovery phrasing) → `DISCOVERY`
- Recommendation phrasing → `RECOMMENDATION`
- Else → `UNKNOWN`

### 3) Structural Interpretation (`luma/structure/interpreter.py`)
**Purpose:** Understand the shape of the request.

**Key behaviors**
- Determines booking count (single vs. multi).
- Determines service scope (single/multi), date scope, and time scope.
- Classifies time type (exact, range, window) and whether date/time are required.
- Pure rule functions in `structure/rules.py`; uses the parameterized sentence to reason about token order (servicefamilytoken, timetoken, timewindowtoken).
- Booking count: multiple verbs or separators (`and/then/next/...`) → multi.
- Service scope: conjunctions between service tokens with no verb → shared; otherwise separate.
- Time type: explicit range markers → range; else times → exact; else windows → window; else none.
- Time scope: if time tokens appear after all services → shared; interleaved or leading times → per_service.
- Duration flag and a coarse clarification flag (`check_needs_clarification`).
- Example interpretations:
  - `"book servicefamilytoken and servicefamilytoken tomorrow at 2pm"` → booking_count=1, service_scope=shared, time_scope=shared, time_type=exact.
  - `"book servicefamilytoken at 2 and servicefamilytoken at 4"` → booking_count=1, service_scope=separate, time_scope=per_service, time_type=exact.
  - `"between 2 and 4 book servicefamilytoken"` → time_type=range.
- Disambiguation:
  - Multiple times without explicit range marker + shared scope flags clarification.
  - Conflicting scopes (separate services but shared time) trigger clarification in grouping.

**Outputs**
- `structure.booking_count`
- `structure.service_scope`, `structure.date_scope`, `structure.time_scope`
- `structure.time_type`
**Key files**
- `structure/interpreter.py`
- `structure/rules.py`

### 4) Appointment Grouping (`luma/grouping/appointment_grouper.py`)
**Purpose:** Attach services to their date/time references.

**Key behaviors**
- Groups each service with the relevant date/time/duration references.
- Validates that the request forms at least one coherent appointment.
- Prepares grouped items for downstream semantic resolution.
- Default intent is `BOOK_APPOINTMENT`; status can be `OK` or `NEEDS_CLARIFICATION`.
- Builds booking dict with services, date_ref (prefers absolute), time_ref (per `time_type`), and duration.
- Clarification reasons include multiple dates/times without range markers or conflicting scopes.
- Example groupings:
  - Entities: services `[haircut]`, dates `[tomorrow]`, times `[2pm]` → booking: date_ref=`tomorrow`, time_ref=`2pm`, status=OK.
  - Entities: services `[haircut, massage]`, times `[2pm, 4pm]`, time_type=exact → time_ref=`2pm to 4pm` (first two times), service_scope separate → still status=OK.
  - Entities: multiple dates without range → status=NEEDS_CLARIFICATION, reason notes multiple dates.
- Disambiguation:
  - If structure.needs_clarification is set, grouping passes through with status `NEEDS_CLARIFICATION`.
  - Range markers missing while multiple times/dates exist → clarification reason recorded.

**Outputs**
- `grouping.appointments` (service + temporal pairing)
**Key file**
- `grouping/appointment_grouper.py`

### 5) Semantic Resolution (`luma/resolution/semantic_resolver.py`)
**Purpose:** Convert grouped data into booking semantics and detect ambiguity.

**Key behaviors**
- Determines `date_mode` (e.g., exact vs. relative) and `time_mode` (exact vs. range/window).
- Detects ambiguity (bare weekday, hour-only time, fuzzy hour like “around 6ish”, missing service, etc.).
- Emits `needs_clarification`, `clarification.reason`, and `clarification.data` that map to templates.
- Loads vocab/entity types once (cached) to normalize date/time variants and detect unresolved patterns.
- Time semantics precedence: exact > range > window > none; windows discarded if an exact time co-exists. Fuzzy hours need a window or trigger ambiguity.
- Date semantics precedence: absolute > relative; guards conflicting modifier+relative combos.
- Tenant alias support: builds `variants_by_family` from tenant aliases, tracks explicit alias matches, and flags `SERVICE_VARIANT` ambiguity when multiple aliases map to the same family without an explicit match.
- Ambiguity checks: multiple times without range, bare weekdays, fuzzy hours without windows, missing services, conflicting signals, unresolved weekday-like text.
- Example resolutions:
  - Services `[haircut]`, date `tomorrow`, time `2pm` → date_mode=relative, time_mode=exact, refs=`["2pm"]`, needs_clarification=False.
  - Time window `morning` + exact time `9am` → time_mode=exact (window dropped), refs=`["9am"]`.
  - Fuzzy hour `"around 6ish"` with no window → needs_clarification=True (ambiguity).
  - Aliases: tenant aliases map to canonical; if multiple aliases for same family and none explicitly used → `SERVICE_VARIANT` clarification with options.
- Disambiguation:
  - Conflicting modifier+relative date (e.g., “next tomorrow”) → clarification.
  - Multiple times without range → ambiguity; window+exact resolves to exact.
  - Bare weekdays or unresolved weekday-like text → clarification.

**Outputs**
- `semantic.resolved_booking` (date_mode, date_refs, time_mode, time_refs, durations)
- `needs_clarification` + `clarification` (reason, data) when applicable
**Key file**
- `resolution/semantic_resolver.py`

### 6) Calendar Binding (`luma/calendar/calendar_binder.py`)
**Purpose:** Produce bookable, timezone-aware ranges.

**Key behaviors**
- Resolves relative dates to absolute ISO-8601 using the provided `timezone` and current time.
- Normalizes times to 24h format; builds date/time/datetime ranges.
- Validates calendar constraints (e.g., avoids past dates or inconsistent ranges).
- Intent-guarded: binds only for `AVAILABILITY`, `CREATE_BOOKING`, `MODIFY_BOOKING`, `BOOKING_INQUIRY`.
- Binding rules: relative offsets, absolute dates (future-biased if no year), weekday phrases (`this/next <weekday>`), time windows expanded to bounds, duration adds end time.
- Validation: rejects end-before-start, duration with multi-day range, midnight-spanning ranges, invalid formats; maps to `ClarificationReason.CONFLICTING_SIGNALS` when needed.
- Example bindings (timezone-aware):
  - date_refs=`["tomorrow"]`, time_refs=`["2pm"]`, now=`2025-01-14T10:00Z` → date_range `2025-01-15`, time_range `14:00-14:00`, datetime_range `2025-01-15T14:00Z`.
  - date_refs=`["this friday"]` (on Tuesday) → binds to upcoming Friday; `next friday` → following week.
  - time_mode=window `["morning"]` with bounds config → expands to start/end times.
  - duration 60m with datetime_range start `14:00` → end `15:00`.
- Disambiguation:
  - If intent not in binding set → returns unbound ranges (no clarification).
  - Validation failures (end before start, duration + multi-day) set `needs_clarification` with `CONFLICTING_SIGNALS` reason/data.

**Outputs**
- `calendar.calendar_booking.date_range` (`start`, `end`)
- `calendar.calendar_booking.time_range` (`start_time`, `end_time`)
- `calendar.calendar_booking.datetime_range` (`start`, `end`)
**Key file**
- `calendar/calendar_binder.py`

## Response Shape (illustrative)
```json
{
  "success": true,
  "needs_clarification": false,
  "intent": {"name": "CREATE_BOOKING", "confidence": "HIGH"},
  "booking": { "services": [...], "date_mode": "relative", ... },
  "stages": {
    "extraction": {...},
    "intent": {...},
    "structure": {...},
    "grouping": {...},
    "semantic": {"resolved_booking": {...}},
    "calendar": {"calendar_booking": {...}}
  },
  "clarification": {
    "reason": null,
    "data": null
  }
}
```

If `needs_clarification=true`, the orchestrator uses the `reason` to pick a template (see `luma/clarification/reasons.py` and `renderer.py`) and returns a `CLARIFY` outcome to the caller.

## Running the pipeline locally
- Quick sample: `python -m luma.test --examples`
- Interactive REPL: `python luma/cli/interactive.py`
- REST endpoint: `python luma/api.py` then POST to `http://localhost:9001/book`

Use the same domain/timezone and tenant aliases you expect in production for realistic results.


