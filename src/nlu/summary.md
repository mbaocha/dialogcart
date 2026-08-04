# Luma (`src/nlu`) — Architectural Contract

Permanent architecture notes for the production NLU service. Describes **what Luma is designed to do today**, based on production code—not a redesign proposal.

Primary sources: `nlu/api.py`, `nlu/registry/intent_groups.py`, `nlu/pipeline.py`, `nlu/stages/`, root `AGENTS.md`, `core/adapters/nlu/`, `core/planning/nlu_invocation.py`, `core/planning/pipeline/requests.py`.

---

## 1. Architectural responsibility

### Problem Luma solves

Luma turns a **single user utterance** (plus optional Core-supplied context and tenant catalog) into **structured understanding for that turn**:

1. Classify what the user is trying to do (intent taxonomy).
2. Extract entities evidenced in the utterance (dates, times, services, booking references).
3. Resolve service phrases against the tenant alias catalog.
4. Optionally emit a structured **interaction subtype** (`operation`) when intent alone is insufficient (availability browse).

It is a **semantic understanding** service. It is not a planner, session store, or execution client (`core/adapters/nlu/__init__.py`, root `AGENTS.md`).

### Decisions that belong exclusively to Luma

| Decision | Where |
|----------|--------|
| Intent label for this utterance | Stage 1 proposes; **Stage 2 `validated_intent` is authoritative** |
| Which Stage 2 extractor group runs | `registry/intent_groups.py` → `stages/stage2/dispatcher.py` (Stage 3 re-runs on group change) |
| Fact extraction from **current** text | Stage 2 group extractors (after validation) |
| Service alias / ambiguity resolution | `pipeline.py` + `catalog.py` |
| Calendar binding of named dates to ISO | `calendar/calendar_binder.py` (after extraction) |
| Availability browse subtype (`browse_next` / `browse_previous`) | Stage 2 availability group → `operation` |
| Whether correction language vs bare slot-fill vs modify / FAQ digression | Stage 2 Intent Validation Contract (`intent_validation_section`) |
| Whether `CORRECTION` is a workflow-state change vs informational disagreement | Stage 2 Intent Validation Contract (`CORRECTION` vs informational clarification) |

### Decisions intentionally deferred to Core

| Decision | Owner |
|----------|--------|
| Durable session / booking state | Core session |
| Effective planning intent across turns | `intent_resolution`, `stage01_intent` |
| Missing slots / completeness | Core policy (`intent_policy.yaml`) |
| Whether to search, paginate, confirm, or commit | Core planner + execution |
| Confirmation gate (yes / no / another request) | Core `confirmation_gate` |
| Merging this turn’s facts into session | Core `merge` |
| Invalidation when slots change | Core session / revision stages |
| What to say to the user | Core rendering |

Root constitution: NLU must **not fabricate booking slots** absent from the current utterance; Core carries durable state (`AGENTS.md`).

### What Luma is expected to infer

- Intent of the **current** message, using conversation context when supplied.
- Entities **mentioned this turn** (dates, times, service terms, booking IDs).
- Follow-up references for RAG (`search_query` refinement) when context has prior topic.
- Slot-fill continuation vs correction vs new booking verb (classifier rules).
- Browse vs new availability search (`operation` vs null).

### What Luma is intentionally not responsible for

- Persisting or owning session state.
- Computing `missing_slots`, `status`, `stage`, or `action`.
- Instructing Core to execute availability search or booking commit.
- Reconstructing full durable workflow state (slots already collected, confirmation pending, fingerprints).
- Emitting planner or execution step names.

Contract explicitly forbids planning fields in the response (`api.py`): `status`, `missing_slots`, `issues`, `clarification`, `booking`.

---

## 2. Output contract

HTTP: `POST /resolve` (`nlu/api.py`). Core validates only `intent.name` as required (`luma_contracts.assert_luma_contract`); other fields are optional/partial.

| Field | Meaning | Owner | Kind | Primary Core consumer |
|-------|---------|-------|------|------------------------|
| `intent.name` | Taxonomy label for this turn’s understanding | Luma | Mixed (see §3–4) | `resolve_effective_intent`, `stage01_intent`, confirmation gate (raw) |
| `intent.confidence` | Classifier confidence | Luma | Metadata | Logging / diagnostics; not planning authority |
| `facts.dates` | Date mentions (raw or ISO after bind) | Luma | Extracted facts | `temporal_proposal` → date proposals |
| `facts.times` | Clock times (HH:MM) | Luma | Extracted facts | `temporal_proposal` → time proposals |
| `facts.date_time_pairs` | Same-utterance date+time pairs | Luma | Extracted facts | Temporal proposal / binding |
| `facts.service_id` | Resolved catalog service (or null) | Luma | Extracted facts + entity resolution | `facts_to_slots`, merge, revision |
| `facts.booking_id` | Booking reference token | Luma | Extracted facts | `facts_to_slots`, modify/cancel flows |
| `time_constraint` | Exact / fuzzy / window time structure | Luma | Extracted facts | `temporal_proposal`, revision detection, missing-slot satisfaction |
| `date_constraint` | Date mode structure when present | Luma | Extracted facts | `temporal_proposal`, flexible-utterance rules |
| `search_query` | RAG noun phrase for informational intents | Luma | Conversational / retrieval hint | RAG handler delegation |
| `service_candidates` | Ambiguous service options | Luma | Clarification support | Clarification / decision plan |
| `operation` | Interaction subtype under an intent (today: browse) | Luma | Conversational operation | `availability/browse.py`, pagination workflows |

**Not returned (by design):** `slots` as Core planning slots, `missing_slots`, `status`, execution actions. Core promotes a subset of facts into slots (`luma_facts_adapter.facts_to_slots` promotes only `service_id` / `booking_id`; dates/times stay as proposals until Core binds them).

---

## 3. Intent taxonomy

Canonical list: `nlu/registry/intent_groups.py` → `ALL_INTENTS`.

| Intent | Group | Conceptual category | Why |
|--------|-------|---------------------|-----|
| `CREATE_APPOINTMENT` | booking | **Business workflow** | Starts/continues timed-service booking; durable in Core policy |
| `CREATE_RESERVATION` | booking | **Business workflow** | Date-range lodging/space booking |
| `MODIFY_BOOKING` | booking | **Business workflow** | Change an **existing** booking (typically needs `booking_id`) |
| `CANCEL_BOOKING` | booking | **Business workflow** | Cancel an existing booking |
| `BOOKING_INQUIRY` | booking_query | Business / informational query | Ask about an existing booking |
| `AVAILABILITY` | booking_query | **Business query + operation carrier** | “What’s free?”; browse uses `operation`, not a second intent |
| `PAYMENT` | booking_query | Business workflow signal | Payment desire (capability handoff in Core) |
| `PAYMENT_STATUS` | booking_query | Business query | Payment status question |
| `DISCOVERY` | informational | Informational / RAG | Catalog discovery |
| `DETAILS` | informational | Informational / RAG | Service details |
| `QUOTE` | informational | Informational / RAG | Pricing |
| `RECOMMENDATION` | informational | Informational / RAG | Recommendation |
| `GENERAL_INQUIRY` | informational | Informational / RAG | Catch-all FAQ |
| `CONFIRM_ACTION` | dialog | **Dialog act** | Affirm proposed action (yes/confirm) |
| `REJECT_ACTION` | dialog | **Dialog act** | Reject proposed action |
| `CORRECTION` | dialog | **Conversational operation** (as intent) | In-flow slot replace; not a durable workflow |
| `UNKNOWN` | fallback | Fallback / indeterminate | No clear verb or classification |

Registry comments already separate groups: booking (verb-required workflows), booking_query, informational (`search_query: true`), dialog, fallback.

---

## 4. Architectural consistency of the intent field

The `intent` field is **not a single conceptual layer**. Production mixes:

| Layer | Examples | Same field? |
|-------|----------|-------------|
| Durable business workflows | `CREATE_*`, `MODIFY_BOOKING`, `CANCEL_BOOKING` | Yes |
| Business / informational queries | `AVAILABILITY`, FAQ intents, `BOOKING_INQUIRY` | Yes |
| Dialog acts (confirm/reject) | `CONFIRM_ACTION`, `REJECT_ACTION` | Yes |
| Conversational refinement | `CORRECTION` | Yes |
| Interaction subtype | Browse via `operation` under `AVAILABILITY` | **Separate field** |

### Focused comparison

| Intent | Same layer as CREATE? | Notes |
|--------|----------------------|--------|
| `CREATE_APPOINTMENT` | Baseline business workflow | Durable; owns create lifecycle in Core |
| `MODIFY_BOOKING` | Same layer (different workflow) | Existing-booking change; Core treats as intent switch vs create |
| `CANCEL_BOOKING` | Same layer | Existing-booking cancel |
| `AVAILABILITY` | Adjacent query intent | May refine an active booking; Core remaps onto session intent when non-durable; browse is `operation` |
| `CONFIRM_ACTION` | Dialog act | Core confirmation gate reads **raw** intent |
| `REJECT_ACTION` | Dialog act | Same |
| `CORRECTION` | Conversational operation | Non-durable, non-core; Core remaps planning intent to session; keeps signal as `turn_operation` |

**Conclusion:** Business workflows and conversational operations are **mixed into `intent`**, except for availability browse, which already uses the cleaner **intent + `operation`** split.

---

## 5. Session awareness

### Is Luma session-aware?

**Partially, by design.** Luma is **stateless per request** (`AGENTS.md`, `api.py`: omit `conversation_context` for stateless behaviour). It does **not** load or write Core session. When Core passes `conversation_context`, Luma uses it only to interpret the **current** utterance.

### Does it know the active business workflow?

**Hinted, not owned.** Core may pass:

- `last_intent` — prior turn’s intent (or synthesized from durable `session.intent_name`)
- `active_booking_intent` — durable booking intent after FAQ detour
- `turns` (recent user/assistant/intent snippets)
- `last_search_query`, `last_date_proposal`
- `missing_slots`, `service_candidates`, `resolved_service_id` (Core extensions in `conversation_memory.py`)

Luma uses these for slot-fill continuation, correction gating, calendar bind intent, service disambiguation—not as authoritative workflow state.

### What Core reconstructs

Core alone owns: durable slots, confirmation state, availability trust/fingerprints, missing-slot computation, effective planning intent, merge/invalidation, execution eligibility.

---

## 6. How Core consumes Luma output

Entry: `invoke_nlu_for_planning` → `assert_luma_contract` → planning pipeline (`stage01_intent` → working turn → slots → confirmation → decision plan → …).

| Output | First consumed | Trusted? | Transformed? | Notes |
|--------|----------------|----------|--------------|-------|
| `intent.name` | `resolve_effective_intent` / `reconcile_intent` | Partially | Often remapped | Raw kept; planning intent may become session durable intent |
| `operation` | Availability browse / workflow routing | Yes (structured) | Normalized to direction | Preferred over text heuristics when present |
| `facts.service_id` / `booking_id` | `facts_to_slots`, merge | Yes when present | Promoted to slots | Null means “not this turn” |
| `facts.dates` / `times` / pairs | `temporal_proposal` | As proposals | Bound later | Not immediately durable time/date slots |
| `time_constraint` | Temporal + revision | Yes for exact/fuzzy | → time_proposal | Also used in revision/actionability checks |
| `date_constraint` | Temporal / flexible rules | Yes when present | → date_proposal | Optional field |
| `search_query` | Handler / RAG path | Yes for informational | Passed through | Non-durable FAQ path |
| `service_candidates` | Clarification / plan | Yes when present | May persist on session | Ambiguity UI |
| `intent.confidence` | Logging | Low | Usually ignored for decisions | Not a planning gate |

**Raw vs effective intent:** confirmation gate and `derive_turn_operation` key off **raw** Luma intent; planning and policy use **effective/planning** intent after Core reconciliation.

---

## 7. Architectural mismatches (reinterpretation / compensation)

| Case | Why it exists | Intentional? | Boundary vs smell |
|------|---------------|--------------|-------------------|
| Remap `CORRECTION` / `AVAILABILITY` onto session durable intent | Dialog/query labels are not durable workflows; booking must continue | Intentional | **Boundary** — Core owns durable intent; partially a **smell** that correction is an intent not an `operation` |
| `derive_turn_operation` re-derives `CORRECTION` from raw intent | Preserve refinement signal after remap | Intentional | Clean Core-side dual model; NLU still packs operation into intent |
| Non-core intent preservation in `intent_resolution` | Side intents must not wipe booking session | Intentional | Boundary |
| `CONFIRM_*` / `REJECT_*` as intents + Core gate | Dialog acts need a label; Core owns gate semantics | Intentional | Boundary; consistent with dialog group |
| `facts_to_slots` only promotes service/booking_id | Dates/times are proposals until availability/trust | Intentional | Boundary (Phase 2 temporal model) |
| Calendar bind may use session intent while SLM says `CORRECTION`/`UNKNOWN` | Binding needs booking intent; label may be dialog/fallback | Intentional | Boundary / NLU-internal compensation |
| Browse phrase fallbacks in Core if `operation` missing | Legacy / resilience | Partially intentional | Mild smell vs “structured operation only” goal |
| Cold `CORRECTION` with no session stays non-durable | Correction requires active flow | Intentional edge | Boundary |
| Treating in-flow “change it” as `MODIFY_BOOKING` would reset create session | Taxonomy collision | Intentional that NLU emit `CORRECTION` instead | Smell that both share the intent field |

---

## Deliverable answers

### 1. What is Luma’s architectural responsibility?

Per-turn **language understanding**: classify intent, extract utterance-evidenced facts, resolve catalog entities, optionally emit structured interaction subtypes. Not session, planning, or execution.

### 2. What is the intended contract between Luma and Core?

Luma returns a **fact-oriented understanding delta** for one message. Core supplies optional interpretation context, validates `intent.name`, merges facts into session, decides planning/execution. Luma must not return planner fields (`missing_slots`, `status`, …).

### 3. What conceptual categories does Luma currently emit?

- Business workflows (`CREATE_*`, `MODIFY_*`, `CANCEL_*`, …)
- Business / informational queries (`AVAILABILITY`, FAQ intents, …)
- Dialog acts (`CONFIRM_ACTION`, `REJECT_ACTION`)
- Conversational operations (`CORRECTION` as intent; browse as `operation`)
- Extracted facts (`facts`, `time_constraint`, `date_constraint`)
- Retrieval hints (`search_query`)
- Clarification support (`service_candidates`)

### 4. Are business workflows and conversational operations mixed?

**Yes**, primarily inside `intent`. The exception is availability browse (`AVAILABILITY` + `operation`), which separates workflow/query intent from conversational operation.

### 5. Which parts of the contract appear clean?

- Fact-only response shape and forbidden planner fields
- Tenant-scoped entity resolution
- Explicit non-ownership of session/planning/execution
- AVAILABILITY + `operation` pattern
- Core temporal proposal path (dates/times not prematurely slotted)
- Conversation context as optional interpretation aid only

### 6. Which parts appear inconsistent?

- `CORRECTION` (and dialog refinements) living in the same field as durable workflows
- Core must remap several “intents” that are really turn operations or dialog acts
- Dual representation: NLU `intent=CORRECTION` vs Core `turn_operation=CORRECTION` + remapped planning intent
- `operation` exists but is only populated for browse, not for correction/slot-replace

### 7. Public contract (documentation form)

**Request**

```text
POST /resolve
{
  text: string,                          # required — current user utterance
  tenant_context: {
    aliases: { phrase → service_id },
    booking_mode: "service" | "reservation",
    booking_id?: { pattern, scan_pattern?, examples? }
  },
  conversation_context?: {               # optional; omit = cold/stateless
    last_intent?,
    active_booking_intent?,
    last_search_query?,
    last_date_proposal?,
    turns?: [{ user, assistant?, intent, search_query? }],
    # Core may also attach: missing_slots, service_candidates, resolved_service_id
  },
  test_now?: ISO datetime,
  timezone?: string                      # default UTC
}
```

**Response**

```text
{
  intent: { name: IntentName, confidence: number },  # required name
  facts: {
    dates: string[],
    times: string[],
    date_time_pairs: { date, time }[],
    service_id: string | null,
    booking_id: string | null
  },
  time_constraint?: object | null,       # exact | fuzzy | window
  date_constraint?: object | null,
  search_query?: string | null,          # informational / RAG intents
  service_candidates?: string[],
  operation?: "browse_next" | "browse_previous" | null
}
```

**IntentName** — see §3 / `registry/intent_groups.py`.

**Guarantees**

- Understanding is for the **current utterance**; context may disambiguate but must not invent unmentioned booking slots.
- Response does **not** include Core planning fields (`status`, `missing_slots`, `action`, …).
- `operation` is a structured subtype under an intent (currently availability browse only).
- Entity IDs are tenant-resolved when unambiguous; ambiguity may yield `service_candidates`.

**Non-guarantees**

- Emitted `intent.name` is not always the durable planning intent Core will use.
- Partial/empty `facts` are valid.
- Luma does not decide whether Core searches, confirms, or commits.

---

## Related references

- Root ownership: [`AGENTS.md`](../../AGENTS.md)
- Core constitution: [`core/AGENTS.md`](../core/AGENTS.md)
- Intent registry: [`registry/intent_groups.py`](registry/intent_groups.py)
- HTTP contract: [`api.py`](api.py)
- Core NLU invoke: [`core/planning/nlu_invocation.py`](../core/planning/nlu_invocation.py)
- Core request and turn operations: [`core/planning/pipeline/requests.py`](../core/planning/pipeline/requests.py)
)
