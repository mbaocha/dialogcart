# Luma NLU Contract (Core boundary)

This document describes what Luma must produce for Core. It covers NLU responsibilities at the Luma/Core boundary—not pipeline internals.

For availability-specific interactions, see the [Availability Interaction Contract](../core/orchestration/contracts/AVAILABILITY_INTERACTION_CONTRACT.md).

---

## Per-request output

Each `/resolve` response must include at minimum:

```json
{
  "intent": { "name": "<INTENT_NAME>" }
}
```

Availability browse turns add a top-level `operation` field:

```json
{
  "intent": { "name": "AVAILABILITY", "confidence": 0.95 },
  "operation": "browse_next"
}
```

Availability search turns omit `operation` (or leave it unset). Luma never emits planner actions or `SEARCH_AVAILABILITY`.

Additional fields are optional and partial. Core computes `missing_slots` and planning state from merged facts and session.

---

## Responsibilities

**Luma owns:**

- Intent classification
- Operation classification (where defined, for example availability browse)
- Fact extraction: dates, times, services, booking references
- Entity resolution to tenant catalog identifiers

**Luma does not:**

- Persist session state
- Select execution steps (`SEARCH_AVAILABILITY`, `CONFIRM_APPOINTMENT`, etc.)
- Invalidate availability caches or paginate results

---

## Availability interactions

Availability uses a **single intent** (`AVAILABILITY`) and a generic **`operation`** field—not intent-specific browse fields.

| `operation` | When Luma sets it |
|-------------|-------------------|
| `null` | User is asking for availability or refining search parameters (date, service) |
| `browse_next` | User wants more times from an already-presented set (“show more”, “show more times”, “show additional times”, “next page”, “more availability”) |
| `browse_previous` | User wants an earlier page (“previous page”, “earlier times”, “go back”) |

Luma classifies language; Core decides whether to search, paginate, or reuse cache.

Full utterance mapping and examples: [Availability Interaction Contract](../core/orchestration/contracts/AVAILABILITY_INTERACTION_CONTRACT.md).

---

## Booking slots

Luma must not fabricate durable booking slots absent from the current utterance. Context from Core informs interpretation but does not substitute for what the user said this turn.

Temporal values surface as **facts** and **proposals** (`date_proposal`, `time_proposal`). Core promotes them to durable slots after availability binding or explicit confirmation.
