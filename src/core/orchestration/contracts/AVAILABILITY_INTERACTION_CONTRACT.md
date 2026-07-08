# Availability Interaction Contract (Luma → Core)

This document defines the contract for how Luma communicates availability-related user interactions to Core.

---

## Principles

| Owner | Responsibility |
|-------|----------------|
| **Luma** | Intent classification, `operation` classification, fact extraction (dates, services, times) |
| **Core** | Session merge, planning, cache invalidation, pagination, execution dispatch |

Rules:

- **Luma owns intent and operation.** Core must not parse raw user text to infer browse direction in steady state.
- **Core owns execution decisions.** Luma never instructs Core to run `SEARCH_AVAILABILITY` or any other execution step.
- **Core decides** on each turn whether to:
  1. **Reuse** cached availability (no presentation change),
  2. **Paginate** cached availability (presentation-only browse), or
  3. **Execute** `SEARCH_AVAILABILITY` (cache miss or invalidation).

`plan.action` remains Core-only. NLU output is input to planning—not a command to execute.

---

## Schema

Availability interactions use a **single intent** with an optional **generic operation** field.

```json
{
  "intent": { "name": "AVAILABILITY" },
  "operation": null,
  "facts": {}
}
```

### Fields

| Field | Type | Owner | Meaning |
|-------|------|-------|---------|
| `intent.name` | string | Luma | Always `AVAILABILITY` for availability-related utterances in this contract |
| `operation` | string \| null | Luma | Interaction subtype at **top level** of Luma response; omitted when not browsing |
| `facts` | object | Luma | Extracted entities for this turn (dates, service references, times, etc.) |

### Supported operations (initial set)

| `operation` | Meaning |
|-------------|---------|
| `null` | Availability query or refinement—not paginating an existing result set |
| `browse_next` | User wants the next page of previously searched availability |
| `browse_previous` | User wants the previous page of previously searched availability |

Future operations (for example `browse_first`, `refresh`) may be added without new intents. Core treats unknown operations as `null` until explicitly supported.

### Where fields live

Luma emits `operation` at the **top level** of the `/resolve` response (sibling to `intent`). Core normalizes it to a transient per-turn value during merge. The field is **per-turn only** and must not be persisted in session.

---

## Utterance mapping

During an active booking, Luma still emits this contract. Core may preserve the durable session intent (for example `CREATE_APPOINTMENT`) while consuming `operation` for presentation. The table below describes **Luma output**, not Core's effective planning intent.

| User request | Intent | Operation | Other facts (examples) |
|--------------|--------|-----------|------------------------|
| show availability | `AVAILABILITY` | `null` | `service_id` from utterance or empty; `dates` from session context if none stated |
| availability for July 8 | `AVAILABILITY` | `null` | `dates: ["2026-07-08"]`; `service_id` if stated or carried via context |
| show more | `AVAILABILITY` | `browse_next` | none required; browse is presentation-only |
| show additional times | `AVAILABILITY` | `browse_next` | none required |
| previous page | `AVAILABILITY` | `browse_previous` | none required |
| earlier times | `AVAILABILITY` | `browse_previous` | none required |
| later times | `AVAILABILITY` | `browse_next` | none required |

Notes:

- **Search vs browse:** Rows with `operation: null` may still cause Core to run `SEARCH_AVAILABILITY` when cache is missing or search parameters changed. Rows with `browse_*` must never cause a new search—Core paginates `last_execution_result` only.
- **Facts without operation:** Date or service facts on a browse turn are ignored for invalidation unless Core classifies them as a booking revision (separate merge/invalidation rules).
- **Mid-booking:** Utterances like “show more times” during `CREATE_APPOINTMENT` are still `AVAILABILITY` + `browse_next` from Luma; Core applies pagination without advancing the booking stage.

---

## Example payloads

**New availability query**

```json
{
  "intent": { "name": "AVAILABILITY" },
  "operation": null,
  "facts": {
    "service_id": "premium haircut",
    "dates": ["2026-07-08"]
  }
}
```

**Browse next page**

```json
{
  "intent": { "name": "AVAILABILITY" },
  "operation": "browse_next",
  "facts": {}
}
```

**Browse previous page**

```json
{
  "intent": { "name": "AVAILABILITY" },
  "operation": "browse_previous",
  "facts": {}
}
```

---

## Core response (informative)

Core does not expose `operation` to the user. On browse turns Core:

- sets `plan.action` to `null`,
- updates `presented_availability` and `availability_presentation`,
- leaves booking slots, proposals, and `availability_fingerprint` unchanged.

When `operation` is `null` and policy selects search, Core runs `SEARCH_AVAILABILITY` and refreshes the cache.

Core consumes structured `operation` only. It does not parse raw user text to infer browse direction.

---

## Related documentation

- Core architectural rules: [`src/core/AGENTS.md`](../../AGENTS.md) — Availability, Availability presentation
- Root ownership boundaries: [`AGENTS.md`](../../../../AGENTS.md) — NLU vs Core
- Luma NLU responsibilities: [`src/luma/NLU_CONTRACT.md`](../../../luma/NLU_CONTRACT.md)
