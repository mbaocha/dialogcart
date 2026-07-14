# Core Request Flow — Architecture Guide

> Running example throughout this document: **"book me premium haircut tomorrow by 9am"**

---

## 1. Executive Summary

DialogCart Core is a **layered orchestration system** that turns a user message into a structured booking action. It has a clear separation between concerns: the HTTP boundary handles transport and persistence; the engine coordinates the turn; the planner converts language into a decision; execution runs the selected action; rendering produces the reply text.

### Major layers

```
HTTP Layer          message.py
                        ↓ (session load, transport)
Orchestration       ConversationEngine.process_turn()
                        ↓ (plan → execute → render → return)
Planning            plan_message() → TurnPlanner.plan_turn()
                        ↓ (NLU call, session merge, policy evaluation)
Execution           ActionRunner → dispatcher → clients
                        ↓ (availability search or booking commit)
Rendering           ResponseRenderer
                        ↓ (LLM text generation)
Persistence         message.py → SessionProjector → save_session()
```

### Ownership summary

| Who | What |
|---|---|
| `message.py` | HTTP boundary, session I/O, capability boundary, persistence |
| `ConversationEngine` | Turn lifecycle (plan → execute → render) |
| `TurnPlanner` | NLU, session merge, policy-driven decision |
| `ActionRunner` / dispatcher | Action execution, client dispatch |
| `ResponseRenderer` | LLM text injection |
| `SessionProjector` | Outcome → durable session state |

### One-paragraph flow

A request arrives at `POST /message`. The HTTP layer loads the raw session, passes it unfiltered to `ConversationEngine.process_turn()`, and waits. The engine asks `plan_message()` to call Luma (the NLU service), merge the response with persisted session slots, and produce a typed decision. The engine then evaluates execution eligibility, routes through `WorkflowRouter` → `ActionRunner` → dispatcher → an API client, and post-processes the result through `AvailabilityWorkflow` or `BookingWorkflow`. `ResponseRenderer` generates LLM text. The engine returns a result dict. Back in `message.py`, the capability boundary runs (if active), the handler boundary runs (if delegated), the outcome is projected into a new session state via `SessionProjector`, the session is saved, and a `MessageResponse` is returned.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────┐
│  HTTP Layer                                         │
│  core/orchestration/api/message.py                  │
│  · session load · transport · persist · respond     │
└────────────────────┬────────────────────────────────┘
                     │ _raw_session, text, user_id
                     ▼
┌─────────────────────────────────────────────────────┐
│  Orchestration Engine                               │
│  core/engine/conversation_engine.py                 │
│  · turn lifecycle ownership                         │
│  · coordinates all layers below                     │
└────────────────────┬────────────────────────────────┘
                     │ text, user_id, session_state
                     ▼
┌─────────────────────────────────────────────────────┐
│  Planning                                           │
│  core/orchestration/orchestrator.py  plan_message() │
│  core/planning/planner/turn_planner.py        │
│  · NLU (Luma) · session merge · policy decision     │
└────────────────────┬────────────────────────────────┘
                     │ plan dict
                     ▼
┌─────────────────────────────────────────────────────┐
│  Execution                                          │
│  core/execution/action_runner.py                    │
│  core/orchestration/execution/dispatcher.py         │
│  core/orchestration/execution/clients/              │
│  · availability search · booking commit             │
└────────────────────┬────────────────────────────────┘
                     │ execution_result dict
                     ▼
┌─────────────────────────────────────────────────────┐
│  Domain Workflows                                   │
│  core/workflows/availability/workflow.py            │
│  core/workflows/booking/workflow.py                 │
│  · post-search processing · slot propagation        │
└────────────────────┬────────────────────────────────┘
                     │ processed result
                     ▼
┌─────────────────────────────────────────────────────┐
│  Rendering                                          │
│  core/rendering/response_renderer.py                │
│  · LLM text for availability / outcome / missing    │
└────────────────────┬────────────────────────────────┘
                     │ result dict with text
                     ▼
┌─────────────────────────────────────────────────────┐
│  Persistence  (back in HTTP layer)                  │
│  core/session/session_projector.py                  │
│  core/session/persist.py                            │
│  · project outcome → session · save_session()       │
└─────────────────────────────────────────────────────┘
```

---

## 3. Directory Structure

```
src/
└── core/
    ├── engine/               Orchestration owner
    ├── orchestration/        Transport, planning entry, execution clients
    ├── planning/             NLU merge, policy interpretation, plan building
    ├── execution/            ActionRunner facade
    ├── workflows/            Domain post-processing boundaries
    ├── rendering/            LLM text generation
    ├── session/              Session schema, merge, persistence, projection
    ├── policy/               intent_policy.yaml loader
    └── config/               intent_policy.yaml, capabilities.yaml
```

### `core/engine/`

**Why it exists:** Houses the single orchestration entrypoint that coordinates every stage of a turn. `ConversationEngine` is the production owner — `message.py` calls it directly.

**What belongs here:** The engine class and outcome builder. Nothing else belongs here.

**What does not belong here:** Session I/O, persistence, HTTP concerns, business logic for specific intents.

---

### `core/orchestration/`

**Why it exists:** Historically the entire system lived here. Now it hosts the planning entrypoints (`plan_message()`, `handle_message()` compatibility wrapper), execution clients, NLU client wrappers, caches, and the HTTP API endpoint.

**What belongs here:**
- `api/message.py` — HTTP boundary
- `execution/` — dispatcher, availability client, booking client
- `nlu/` — Luma client and NLU utilities
- `orchestrator.py` — planning entry (`plan_message()`) and compatibility wrapper (`handle_message()`)

**What does not belong here:** New orchestration logic. Any new turn-level coordination belongs in `core/engine/`.

---

### `core/planning/`

**Why it exists:** Owns the pure planning computation: calling Luma, merging session slots, computing missing slots, and building the execution decision from `intent_policy.yaml`.

**What belongs here:** `turn_planner.py`, `plan_builder.py`, `intent_resolution.py`, `business_fact_registry.py`.

**What does not belong here:** Execution, rendering, session I/O. Planning is side-effect free.

---

### `core/execution/`

**Why it exists:** Provides the `ActionRunner` facade, the single boundary between the engine and the execution dispatcher.

**What belongs here:** `ActionRunner` and nothing else at this level. The actual dispatch logic lives in `core/orchestration/execution/dispatcher.py`.

---

### `core/workflows/`

**Why it exists:** Domain-specific post-processing boundaries. After raw execution results arrive, workflows own the interpretation and enrichment within their domain.

**What belongs here:** `AvailabilityWorkflow`, `BookingWorkflow`, `WorkflowRouter`.

**What does not belong here:** Business logic that belongs in the planner (intent classification, slot requirements).

---

### `core/rendering/`

**Why it exists:** All LLM text generation passes through here. The renderer transforms a structured decision + execution result into natural language.

**What belongs here:** `ResponseRenderer`, availability renderer, booking confirmation renderer, `llm_renderer.py`.

**What does not belong here:** Business decisions about what to say. The renderer receives instructions (render_instruction, facts) and executes them.

---

### `core/session/`

**Why it exists:** Owns the schema and lifecycle of persisted session state: what is stored, how it is merged, when it is invalidated, and how an execution outcome projects into a new session.

**What belongs here:** `persist.py`, `merge.py`, `invalidation.py`, `schema.py`, `confirmation_gate.py`, `session_projector.py`.

**What does not belong here:** HTTP session I/O. `get_session()`/`save_session()` live in `core/orchestration/session/`.

---

### `core/policy/` and `core/config/`

**Why they exist:** `intent_policy.yaml` is the single source of truth for intent behaviour: which slots are required, what execution steps exist, what business facts each step requires, and whether an intent is durable. `core/policy/intent_policy.py` loads and caches it.

**What belongs here:** Policy definition and loading only. No business logic.

---

## 4. Production Request Walkthrough

Request: **"book me premium haircut tomorrow by 9am"**

---

### Step 1 — HTTP entry

**File:** `core/orchestration/api/message.py`  
**Function:** `post_message(request, http_request)`  
**Responsibility:** Receives the HTTP POST, bootstraps extensions (once per process), determines trace configuration, generates a transaction ID.

**Output:** Continues to session loading.

---

### Step 2 — Session load

**File:** `core/orchestration/api/message.py`  
**Lines:** ~201–237  
**Responsibility:** Loads the raw session from the store unconditionally.

```python
session_state = get_session(request.user_id)   # always loaded
_raw_session  = session_state                   # captured unfiltered
```

For a first turn, `_raw_session` is `None`. For a returning user mid-booking (e.g., `status=READY` with `service_id` already collected), `_raw_session` contains the full persisted session.

A status filter runs next — but it only affects the capability boundary call, not the engine. `_raw_session` is always what the engine receives (see §9).

**Output:** `_raw_session` — unfiltered session dict or `None`.

---

### Step 3 — Engine entry

**File:** `core/engine/conversation_engine.py`  
**Class:** `ConversationEngine`  
**Function:** `process_turn(text, user_id, session_state=_raw_session, ...)`  
**Responsibility:** Owns the complete turn lifecycle. Instantiates façades (`ActionRunner`, `ResponseRenderer`, `AvailabilityWorkflow`, `BookingWorkflow`, `WorkflowRouter`), sets up tracing, and coordinates all subsequent stages.

**Output:** Delegates to `plan_message()`.

---

### Step 4 — Planning entry

**File:** `core/orchestration/orchestrator.py`  
**Function:** `plan_message(text, user_id, session_state, ...)`  
**Responsibility:** Thin planning entry point. Delegates to `plan_turn()` with `planning_only=True`.

**Output:** Delegates to `plan_turn()`.

---

### Step 5 — Tenant context

**File:** `core/planning/planner/turn_planner.py`  
**Function:** `plan_turn(...)`  
**Responsibility:** Resolves `organization_id`, loads the catalog (cached), builds an alias map (`"premium haircut"` → service ID), and creates `tenant_context` for Luma.

**Output:** `tenant_context` dict passed to Luma.

---

### Step 6 — NLU call (Luma)

**File:** `core/planning/planner/turn_planner.py`  
**Responsibility:** Calls `luma_client.call(text, context=tenant_context)`. Luma classifies intent (`CREATE_APPOINTMENT`), extracts slots (`service_id=<haircut_id>`, `date=tomorrow`, `time=09:00`), and returns a structured response.

Core treats Luma as a black box — only the output matters.

**Output:** `luma_response` dict with `intent`, `slots`, `facts`, `operation`.

---

### Step 7 — Contract validation

**File:** `core/orchestration/nlu/__init__.py`  
**Function:** `assert_luma_contract(luma_response)`  
**Responsibility:** Validates that the Luma response is structurally valid. Raises `ContractViolation` on malformed responses.

**Output:** Validated `luma_response`.

---

### Step 8 — Intent resolution

**File:** `core/planning/planner/intent_resolution.py`  
**Function:** `resolve_effective_intent(...)`  
**Responsibility:** Determines the effective intent for this turn. For a first turn with `CREATE_APPOINTMENT`, this is straightforward. On follow-up turns, if the session has a durable intent and Luma returned a continuation signal, the session intent takes precedence.

**Output:** `effective_intent = "CREATE_APPOINTMENT"`.

---

### Step 9 — Missing slots computation

**File:** `core/planning/planner/turn_planner.py`  
**Responsibility:** Computes `missing_slots` from the intent contract defined in `intent_policy.yaml`. For `CREATE_APPOINTMENT`, required slots are `service_id`, `date`, `time`. All three are present. `missing_slots = []`.

**Output:** `effective_response["missing_slots"] = []`.

---

### Step 10 — Session merge

**File:** `core/session/merge.py`  
**Function:** `should_merge_session_context()` → `merge_luma_with_session()`  
**Responsibility:** Decides whether to merge persisted session slots into this turn's response.

For a first turn: `session_state=None`, merge is skipped.

For a returning user with `service_id` already in session: `should_merge_session_context()` checks that the session intent is durable (`CREATE_APPOINTMENT` → `durable: true`) and that no intent reset occurred. If eligible, `merge_luma_with_session()` merges session slots with current-turn slots (current turn wins on conflict).

**Invariant:** Merge eligibility is gated on durable intent, never on session `status`.

**Output:** `effective_response` with merged slots.

---

### Step 11 — Plan building

**File:** `core/planning/planner/plan_builder.py`  
**Function:** `build_decision_plan(effective_response, session_state, ...)`  
**Responsibility:** Derives business facts from the current state and evaluates `intent_policy.yaml` to select the next execution step.

For "book me premium haircut tomorrow by 9am" (first turn, all slots collected):

1. Business facts derived: `availability_check_required=True`, `availability_ready=False`, `time_selection_ready=False`, `user_confirmation_satisfied=False`.
2. Policy for `CREATE_APPOINTMENT` has two steps: `SEARCH_AVAILABILITY` (requires `availability_check_required`) and `CONFIRM_APPOINTMENT` (requires `availability_ready`, `time_selection_ready`, `user_confirmation_satisfied`).
3. `SEARCH_AVAILABILITY` requirement satisfied → selected.
4. `CONFIRM_APPOINTMENT` requirements not satisfied → blocked.

**Output:** `plan = {status: "READY", action: "SEARCH_AVAILABILITY", stage: "AVAILABILITY", slots: {...}, missing_slots: [], ...}`.

---

### Step 12 — Back in the engine: browse short-circuit

**File:** `core/engine/conversation_engine.py`  
**Responsibility:** After `plan_message()` returns, the engine first checks whether this is a browse/pagination turn (e.g., "show me more times"). If so, `_availability_workflow.try_handle_browse_turn()` handles it from session cache and returns early. For our first-turn request, browse is not detected.

**Output:** Browse check returns `None`; engine continues to execution eligibility.

---

### Step 13 — Execution eligibility

**File:** `core/engine/conversation_engine.py`  
**Responsibility:** Evaluates `can_execute`:
- `plan.action` = `"SEARCH_AVAILABILITY"` (not None)
- `plan.status` = `"READY"`
- No missing slots
- Execution clients available

`can_execute = True`.

**Output:** Proceeds to workflow routing.

---

### Step 14 — Workflow routing

**File:** `core/workflows/router.py`  
**Class:** `WorkflowRouter`  
**Function:** `get_route(client_name)`  
**Responsibility:** Reads `client` from the selected execution step in `intent_policy.yaml`. For `SEARCH_AVAILABILITY`, `client: availability_client` → route `"availability"`.

**Output:** `route = "availability"`.

---

### Step 15 — Action execution

**File:** `core/execution/action_runner.py`  
**Class:** `ActionRunner`  
**Function:** `run(plan, availability_client=...)`

**File:** `core/orchestration/execution/dispatcher.py`  
**Function:** `execute(plan, availability_client, booking_client)`  
**Responsibility:** The dispatcher reads `plan.action = "SEARCH_AVAILABILITY"` and calls `availability_client.search(slots)`. Returns raw availability slots from the external API.

**Output:**
```python
{
    "type": "availability",
    "status": "success",
    "slots": [
        {"start": "2025-07-08T09:00:00", "staff_id": 3, ...},
        ...
    ]
}
```

---

### Step 16 — Availability post-processing

**File:** `core/workflows/availability/workflow.py`  
**Function:** `process_search_result(...)`  
**Responsibility:** Computes the availability fingerprint (hash of search parameters), resolves time matching against the user's proposal ("by 9am"), builds the presentation payload, and persists `last_execution_result` and `presented_availability` to session via the session store.

**Output:** Updated `slots` with fingerprint + `session_state` with cached results.

---

### Step 17 — Result construction

**File:** `core/engine/conversation_engine.py`  
**Responsibility:** Merges plan structure (`status`, `stage`, `action`) into `execution_result`. Builds `result = {success: True, result: execution_result, outcome: execution_result, plan: plan}`.

---

### Step 18 — Rendering

**File:** `core/rendering/response_renderer.py`  
**Class:** `ResponseRenderer`  
**Function:** `render_availability(result, decision, execution_result, session_state)`  
**Responsibility:** Calls `build_availability_render_request()` to assemble a render request (available times, business context, conversation history), then calls `render_llm()` to generate natural language text.

**Output:** `result["text"] = "Here are available times for your premium haircut tomorrow: 9:00 AM with [staff]..."`

---

### Step 19 — Engine return

**File:** `core/engine/conversation_engine.py`  
**Responsibility:** Attaches decision trace (if enabled). Returns the result dict to `message.py`.

---

### Step 20 — Capability boundary

**File:** `core/orchestration/api/message.py`  
**Function:** `apply_capability_to_result(...)`  
**Responsibility:** If the outcome status is `AWAITING_CAPABILITY` (e.g., payment required), routes to the capability runner. For our availability result, status is `READY` — capability boundary is a no-op.

---

### Step 21 — Persistence

**File:** `core/orchestration/api/message.py`  
**Class:** `SessionProjector` (via `_session_projector.project()`)  
**File:** `core/session/persist.py`  
**Function:** `build_session_state_from_outcome(...)`  
**Responsibility:** Projects the outcome dict into a new session state dict. For a `READY` outcome with `CREATE_APPOINTMENT`, persists: `intent_name`, `status`, `slots` (`service_id`, `date`, `time`), `last_execution_result`, `presented_availability`, `availability_presentation`.

Then `save_session(user_id, new_session_state)` writes to the session store.

---

### Step 22 — Response

**File:** `core/orchestration/api/message.py`  
**Responsibility:** Returns `MessageResponse(success=True, outcome=..., text=..., decision_trace=...)`.

---

### Summary diagram

```
"book me premium haircut tomorrow by 9am"
    │
    ▼
[message.py] load session (_raw_session=None on first turn)
    │
    ▼
[ConversationEngine.process_turn()]
    │
    ├──► [plan_message()] ──► [plan_turn()]
    │         │
    │         ├── tenant context + catalog resolution
    │         ├── NLU call (`LumaClient` → `src/nlu`) → intent=CREATE_APPOINTMENT, slots={service_id, date, time}
    │         ├── contract validation
    │         ├── intent resolution (effective=CREATE_APPOINTMENT)
    │         ├── missing_slots=[] (all 3 slots collected)
    │         ├── session merge (skipped: first turn, no session)
    │         └── plan_builder → SEARCH_AVAILABILITY selected
    │
    ├── browse check → None (not a browse turn)
    │
    ├── eligibility: can_execute=True
    │
    ├──► [WorkflowRouter.get_route()] → "availability"
    │
    ├──► [ActionRunner.run()] ──► [dispatcher.execute()]
    │         └── availability_client.search(slots) → [{start: 9am, ...}]
    │
    ├──► [AvailabilityWorkflow.process_search_result()]
    │         └── fingerprint + time-match + present + persist cache
    │
    ├── result construction
    │
    └──► [ResponseRenderer.render_availability()] ──► render_llm() → "Here are times..."
    │
    ▼
[message.py] capability boundary (no-op for READY)
    │
    ▼
[message.py] SessionProjector.project() → build_session_state_from_outcome()
    │
    ▼
[message.py] save_session() → MessageResponse
```

---

## 5. Planning Layer

### Responsibility

The planning layer converts an incoming message and current session state into a typed decision. It is **pure**: no API calls that mutate state, no side effects on the session. It calls Luma (read-only for Core) and reads from caches.

### Inputs

- User text
- `session_state` — raw, unfiltered session dict (may be `None`)
- `organization_id`, `domain`, `timezone`
- Optional: `luma_client`, `organization_client`, `catalog_client`

### Outputs — the `plan` dict

```python
{
    "intent_name": "CREATE_APPOINTMENT",
    "status":      "READY",
    "action":      "SEARCH_AVAILABILITY",
    "stage":       "AVAILABILITY",
    "slots":       {"service_id": 12, "date": "2025-07-08", "time": "09:00"},
    "missing_slots": [],
    "_decision":   {...},               # full decision for renderer
    "_merged_luma_response": {...},     # merged NLU response for trace
}
```

### Luma input

`plan_turn()` builds `tenant_context` from the catalog and calls the NLU service (`src/nlu` via `LumaClient` / `LUMA_BASE_URL`) with:
- `text` — the user message
- `context` — conversation history + alias map (service name → ID mapping) + prior intent

### Luma output

Core receives from Luma:
- `intent.name` — e.g., `"CREATE_APPOINTMENT"`
- `slots` — extracted values (`service_id`, `date`, `time`, etc.)
- `facts` — additional extracted context
- `operation` — for availability browse (`browse_next`, `browse_previous`)
- `missing_slots` — *not computed by Core from Luma*; Luma provides a hint; Core recomputes from policy

### Session merge

After the Luma response is validated, `should_merge_session_context()` evaluates two conditions:
1. Session intent is durable (`is_durable_intent(session.intent_name) = True`).
2. No intent reset occurred this turn.

If both are true, `merge_luma_with_session()` combines session slots with current-turn slots. Current-turn slots always win on conflict. This is the slot carry-forward mechanism across multi-turn booking conversations.

### Planning and execution decision

`build_decision_plan()` in `plan_builder.py` derives **business facts** at runtime:

| Fact | How derived |
|---|---|
| `availability_check_required` | No fingerprint match with current slots |
| `availability_ready` | Fingerprint match exists in session |
| `time_selection_ready` | User has selected a specific time from presented results |
| `user_confirmation_satisfied` | Confirmation gate returned `confirmed` |
| `payment_satisfied` | Payment capability completed this or a prior turn |

These facts are evaluated against the `requires` list for each execution step in `intent_policy.yaml`. The first step whose requirements are all satisfied is selected as `plan.action`. Steps with unmet requirements are recorded as `blocked_actions`.

`intent_policy.yaml` is the **only** source of sequencing logic. Planners derive facts; policy dictates what comes next.

---

## 6. Execution Layer

### Execution eligibility

Before dispatching, the engine evaluates `can_execute`:

```
can_execute = (
    plan.action is not None
    AND plan.status == "READY"
    AND missing_slots == []
    AND execution_client is available
)
```

If `can_execute = False`, the engine returns a planning result directly (no API calls).

### Workflow routing

`WorkflowRouter.get_route(client_name)` maps the `client` field from `intent_policy.yaml` to a domain:

| `client` value | Route |
|---|---|
| `availability_client` | `"availability"` → availability path |
| `booking_client` | `"booking"` → booking path |
| unknown | No-op; returns planning result |

### ActionRunner

`ActionRunner.run(plan, availability_client, booking_client)` is a thin facade over `dispatcher.execute()`. It provides a clean boundary: the engine calls `ActionRunner`; `ActionRunner` calls the dispatcher; the dispatcher calls the API client.

### Dispatcher

`core/orchestration/execution/dispatcher.py:execute()` reads `plan.action` and routes:

- `SEARCH_AVAILABILITY` → `availability_client.search(slots)`
- `CONFIRM_APPOINTMENT` → `booking_client.create_appointment(slots)`
- `CONFIRM_CANCELLATION` → `booking_client.cancel_booking(slots)`

### `SEARCH_AVAILABILITY` vs `CONFIRM_APPOINTMENT`

These are the two primary execution paths. They differ in mode, client, and post-processing:

| | `SEARCH_AVAILABILITY` | `CONFIRM_APPOINTMENT` |
|---|---|---|
| **Mode** | `exploratory` | `committing` |
| **Client** | `availability_client` | `booking_client` |
| **Requires** | `availability_check_required` | `availability_ready` + `time_selection_ready` + `user_confirmation_satisfied` |
| **Post-processing** | `AvailabilityWorkflow.process_search_result()` | `BookingWorkflow.process_result()` |
| **Result type** | `{type: "availability", slots: [...]}` | `{status: "EXECUTED", booking: {...}}` |
| **Session after** | `READY` — availability cached | `EXECUTED` — booking persisted |
| **Reversible?** | Yes | No — explicit confirmation required |

---

## 7. Workflows

### AvailabilityWorkflow

**File:** `core/workflows/availability/workflow.py`

**Owns:**
- Browse/pagination short-circuit (`try_handle_browse_turn()`) — serves results from `last_execution_result` in session without re-executing search
- Fingerprint computation (`compute_fingerprint(slots)`) — deterministic hash of search parameters
- Fingerprint comparison (`slots_match_fingerprint()`) — determines if a cached search is still valid
- Search execution facade (`search(plan, client)`)
- Post-search processing (`process_search_result()`) — fingerprint storage, time-match resolution, presentation payload construction, session persistence of cache keys

**Does NOT own:**
- When to search (that is the planner's decision via policy)
- Session merge or slot decisions
- Rendering text

**Key invariant:** Browsing availability (`browse_next`, `browse_previous`) must never call `SEARCH_AVAILABILITY`. It always reads from `last_execution_result` in session. Only a change to search parameters triggers a new search.

### BookingWorkflow

**File:** `core/workflows/booking/workflow.py`

**Owns:**
- Post-execution slot propagation (`process_result()`) — after a booking commit, propagates execution facts (e.g., `booking_id`) back into `slots` for persistence

**Does NOT own:**
- Whether to commit (planner decision, gated by `user_confirmation_satisfied`)
- Rendering of confirmation text (owned by `ResponseRenderer`)
- Session persistence (owned by `message.py` / `SessionProjector`)

---

## 8. Rendering

### ResponseRenderer

**File:** `core/rendering/response_renderer.py`

The `ResponseRenderer` is the single boundary for LLM text injection. It is called from the engine after execution completes. Rendering is **best-effort**: all injection calls catch exceptions and silently skip if rendering fails. A rendering failure never fails a turn.

### Three rendering paths

| Path | When | Function |
|---|---|---|
| Availability | `SEARCH_AVAILABILITY` succeeded | `render_availability()` → `build_availability_render_request()` → `render_llm()` |
| Outcome | Booking confirmed, cancellation, etc. | `render_outcome()` → booking confirmation renderer |
| Clarification | Missing slots require user input | `_inject_rendering_text()` → missing-slot render instruction |

### Rendering lifecycle

```
execution_result available
    ↓
ResponseRenderer.render_availability(result, decision, execution_result, session_state)
    ├── build_availability_render_request(decision, execution_result, ...)
    │       slots + business context + conversation history
    └── render_llm(LlmRenderRequest)
            ↓
        result["text"] = "Here are your options..."
        result["outcome"]["text"] = same
```

### Where rendering begins and ends

Rendering **begins** inside `ConversationEngine.process_turn()` immediately after `execution_result` is post-processed.

Rendering **ends** before the engine returns. `message.py` does not call any render functions — it reads `result["text"]` from the returned dict.

`result["text"]` is the canonical field for the conversational reply. Downstream (`message.py`, callers) read this field.

---

## 9. Session Lifecycle

### Phase 1 — Load (HTTP layer)

```python
# message.py
session_state = get_session(request.user_id)   # always load; may be None
_raw_session  = session_state                   # capture before any filter
```

The raw session is always loaded, regardless of its status or content.

### Phase 2 — Status filter (HTTP layer, capability boundary only)

```python
# message.py
if session_state and session_state.get("status") not in (
    "NEEDS_CLARIFICATION",
    "AWAITING_CAPABILITY",
):
    session_state = None
```

This filtered `session_state` variable is used **only** for the capability boundary call (`apply_capability_to_result`). It is never passed to `ConversationEngine`. This filter prevents capability runners from acting on sessions that are not in an active capability state.

### Phase 3 — Engine receives raw session

```python
# message.py
result = _engine.process_turn(
    session_state=_raw_session,   # unfiltered, always
    ...
)
```

`ConversationEngine` always receives the unfiltered raw session. This is required because the planning layer needs full access:

- **Confirmation gate** (`turn_planner.py`) — reads `intent_name` and `confirmation_state` from `AWAITING_CONFIRMATION` sessions; that status is filtered out by the HTTP filter, so only `_raw_session` contains it.
- **Session merge gate** (`should_merge_session_context()`) — reads `intent_name` to check durability; relevant for `READY` and `AWAITING_CONFIRMATION` sessions, both of which the HTTP filter would null out.
- **Pagination** (`availability_pagination.py`) — reads `last_execution_result` from `READY` sessions.
- **Luma error fallback** — recovers intent/slots from any durable session status.

### Phase 4 — Planner interprets session

Inside `plan_turn()`, the planner owns all decisions about what session information is relevant:

| Decision | Guard |
|---|---|
| Merge session slots | `should_merge_session_context()` — durable intent AND no reset |
| NEEDS_CLARIFICATION slot merge only | Internal status check at `turn_planner.py:~280` |
| Confirmation gate | Only reached when planning produces `AWAITING_CONFIRMATION` branch |
| Availability cache reads | `last_execution_result` present in session |

### Phase 5 — Persist (HTTP layer)

```python
# message.py
new_session_state = _session_projector.project(outcome, outcome_status, ...)
new_session_state = append_messages_turn(new_session_state, text, reply_text)
save_session(user_id, new_session_state)
```

`SessionProjector.project()` delegates to `build_session_state_from_outcome()`, which constructs the new session from the outcome. HTTP owns the write.

### Session contract summary

| Role | What it does |
|---|---|
| HTTP (`message.py`) | Loads raw session; passes `_raw_session` to engine; applies status filter for capability boundary only; owns `save_session()` |
| `ConversationEngine` | Receives `_raw_session` as `session_state`; passes it through to planner unchanged |
| `TurnPlanner` | Owns all per-consumer session decisions: merge eligibility, confirmation gate, slot carry-forward, pagination cache |
| `SessionProjector` | Projects outcome → new session state for persistence |

---

## 10. Responsibility Matrix

| Component | Owns | Does NOT Own |
|---|---|---|
| `message.py` | HTTP transport, session I/O, capability boundary, handler boundary, persistence, response serialization | Turn orchestration, planning, rendering, session interpretation |
| `ConversationEngine` | Turn lifecycle coordination (plan → browse check → execute → post-process → render → return) | Session I/O, persistence, capability/handler dispatch, HTTP concerns |
| `TurnPlanner` | NLU call, session merge, missing-slot derivation, business-fact computation, policy-driven execution decision | Execution, rendering, session writes, capability logic |
| `WorkflowRouter` | Mapping `client_name` → domain route (`availability`/`booking`) | Execution logic, business decisions |
| `ActionRunner` | Dispatching `plan` to `dispatcher.execute()` | Routing decisions, post-processing, result interpretation |
| `AvailabilityWorkflow` | Fingerprint, time-match, browse pagination, post-search session cache | When to search (planner), rendering, session persistence beyond cache |
| `BookingWorkflow` | Post-commit slot propagation (`booking_id` etc.) | Whether to commit (planner + confirmation), rendering, session persistence |
| `ResponseRenderer` | LLM text generation for all outcome types | What to say (render instruction is provided by callers), session, execution |
| `SessionProjector` | Projecting an outcome dict → new session state struct | When to persist (HTTP), what to persist beyond the projection schema |

---

## 11. Architecture Principles

### Single orchestration owner

One class — `ConversationEngine` — owns the complete turn lifecycle. Before this was true, the lifecycle was spread across `handle_message()` in `orchestrator.py`. Now `message.py` calls the engine directly. This makes the call graph linear and the ownership unambiguous.

### Planner decides; engine executes

`plan_turn()` returns a decision. The engine evaluates that decision (eligibility, routing) and dispatches execution. Planning never executes; execution never re-plans. This separation makes each independently testable.

### Policy is the only sequencing source

`intent_policy.yaml` defines what execution steps exist, what slots they require, what business facts they require, and whether they are exploratory or committing. Planners derive facts; policy selects the step; the engine dispatches it. No sequencing logic is hardcoded in planners or executors.

### Durable intent gates session continuity

Session merge is gated on `is_durable_intent()`, not on `session.status`. Status is a presentation signal, not an ownership signal. This means a session can be in `READY` status (which the HTTP filter would null out) while still being eligible for slot carry-forward into the next turn.

### Workflows own domain post-processing

After a raw execution result arrives, workflows own the domain-specific interpretation. `AvailabilityWorkflow` owns everything after a search result arrives; `BookingWorkflow` owns everything after a booking commit. This prevents the engine from accumulating domain knowledge.

### Rendering is best-effort

All rendering calls are wrapped in exception handling. A rendering failure never fails a turn. The engine always returns a structurally correct result dict; `text` may be absent if rendering failed.

### HTTP owns persistence

`ConversationEngine` never calls `save_session()`. The engine returns a result dict; `message.py` projects it and persists. This makes the engine side-effect free with respect to session writes.

### Raw session to the engine, filtered only at the capability boundary

The HTTP-layer status filter exists for a narrow purpose: preventing capability runners from acting on non-active-capability sessions. It has no effect on planning. Every consumer inside the planning layer has its own per-decision guard.

### Composition over accumulation

Each layer calls the next; no layer accumulates responsibilities from adjacent layers. Adding a new capability requires: extending `intent_policy.yaml` (sequencing), extending `business_fact_registry.py` (facts), and optionally adding an execution client. No changes to the engine or HTTP layer.

---

## 12. Sequence Diagram

```
User          message.py        ConversationEngine    plan_message / TurnPlanner    Luma     ActionRunner   AvailabilityWorkflow   ResponseRenderer   save_session
  |               |                     |                       |                     |            |                 |                      |               |
  |─POST /message→|                     |                       |                     |            |                 |                      |               |
  |               |─get_session()──────►|                       |                     |            |                 |                      |               |
  |               |  _raw_session=None  |                       |                     |            |                 |                      |               |
  |               |─process_turn()─────►|                       |                     |            |                 |                      |               |
  |               |                     |─plan_message()────────►                      |            |                 |                      |               |
  |               |                     |                       |─resolve org + catalog|            |                 |                      |               |
  |               |                     |                       |─luma_client.call()──►|            |                 |                      |               |
  |               |                     |                       |◄─{intent, slots}────|            |                 |                      |               |
  |               |                     |                       |─validate contract    |            |                 |                      |               |
  |               |                     |                       |─resolve intent       |            |                 |                      |               |
  |               |                     |                       |─compute missing_slots|            |                 |                      |               |
  |               |                     |                       |─session merge (skip) |            |                 |                      |               |
  |               |                     |                       |─build_decision_plan()|            |                 |                      |               |
  |               |                     |◄─plan={action:SEARCH} |                     |            |                 |                      |               |
  |               |                     |─browse check (None)   |                     |            |                 |                      |               |
  |               |                     |─eligibility: can=True |                     |            |                 |                      |               |
  |               |                     |─WorkflowRouter.get_route()→"availability"   |            |                 |                      |               |
  |               |                     |──────────────────────────────────────────── ActionRunner.run(plan)         |                      |               |
  |               |                     |                       |                     |       dispatcher.execute()   |                      |               |
  |               |                     |                       |                     |       availability_client.search()                |               |
  |               |                     |                       |                     |            |◄─{slots:[...]}──|                      |               |
  |               |                     |────────────────────────────────────────────────────────────process_search_result()               |               |
  |               |                     |                       |                     |            |                 |─fingerprint          |               |
  |               |                     |                       |                     |            |                 |─time-match           |               |
  |               |                     |                       |                     |            |                 |─persist cache────────────────────────►|
  |               |                     |─result construction   |                     |            |                 |                      |               |
  |               |                     |─render_availability()─────────────────────────────────────────────────────►render_llm()          |               |
  |               |                     |◄─result{text:"Here…"} |                     |            |                 |                      |               |
  |               |◄─result────────────|                       |                     |            |                 |                      |               |
  |               |─capability boundary (no-op for READY)      |                     |            |                 |                      |               |
  |               |─SessionProjector.project()                  |                     |            |                 |                      |               |
  |               |─save_session()─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────►|
  |               |─MessageResponse───►|                       |                     |            |                 |                      |               |
  |◄─response─────|                   |                       |                     |            |                 |                      |               |
```

---

## 13. Compatibility Layer

The following symbols exist for backward compatibility. They are not the primary architecture.

### `handle_message()` — `core/orchestration/orchestrator.py:264`

Before the ConversationEngine migration, `handle_message()` owned the complete orchestration lifecycle (~800 lines). Today it is a ~50-line compatibility wrapper:

1. Session loading via three-fallback chain:
   - FALLBACK 0: `session_store.get_session(user_id)` if `session_store` provided
   - FALLBACK 1: `kwargs["session_state"]` if provided
   - FALLBACK 2: default session manager
2. `ConversationEngine().process_turn(...)` delegation

**Who still uses it:** Test suites and any external callers that imported `handle_message` before the migration. Production requests use `_engine.process_turn()` directly from `message.py`.

### `ConversationEngine.handle_turn()` — `core/engine/conversation_engine.py:809`

A one-line alias for `process_turn()`. Exists because earlier versions of the `ConversationEngine` stub exposed `handle_turn()` as the public interface. Callers of the stub are unaffected.

```python
def handle_turn(self, text, user_id, **kwargs):
    return self.process_turn(text=text, user_id=user_id, **kwargs)
```

### `ConversationEngine.plan_turn()` — `core/engine/conversation_engine.py:822`

A thin wrapper over `plan_message()`. Exposes planning-only access through the engine interface.

```python
def plan_turn(self, text, user_id, session_state=None, **kwargs):
    return plan_message(text=text, user_id=user_id, session_state=session_state, **kwargs)
```

---

## 14. Extension Points

Three systems deliberately live outside `ConversationEngine`. They are owned and invoked by `message.py` after the engine returns.

### CapabilityRunner

**File:** `extensions/capabilities/runner.py`  
**Called from:** `message.py` via `apply_capability_to_result()`  
**Why outside the engine:** Capabilities (e.g., payment collection) are external business operations that may require their own I/O lifecycle, async operations, or multi-turn interactions. The engine produces a structured outcome; the capability runner acts on it; `message.py` merges the result. Core never imports adapters or branches on capability names.

**Contract:** Core emits `status=AWAITING_CAPABILITY` + `active_capability=<name>`. The runner reads this and takes over. When complete, the runner returns `facts` that Core merges into the session on the next turn.

### HandlerRunner

**File:** `extensions/handlers/runner.py`  
**Called from:** `message.py` for `HANDLER_DELEGATED` outcomes  
**Why outside the engine:** Intent handlers (e.g., a RAG search handler) are domain extensions, not Core logic. The engine classifies the intent as `HANDLER_DELEGATED` and returns. `message.py` passes context to the registered handler, receives rendered text, and persists the conversation turn. Core stays ignorant of handler internals.

### SessionProjector

**File:** `core/session/session_projector.py`  
**Called from:** `message.py` after `process_turn()` returns  
**Why outside the engine:** Session persistence is an HTTP-layer concern. The engine must be free of session write side effects so it can be tested in isolation without a session store. `SessionProjector` is the named boundary between "turn outcome" and "persisted state". `message.py` owns when to persist; `SessionProjector` owns how to project.

---

## 15. Reading Order

For a new engineer joining the project, read in this order:

### 1. `core/orchestration/api/message.py`

Start at the HTTP boundary. This is where every production request enters. Reading it gives you the full lifecycle at a high level: session load → engine call → capability boundary → handler boundary → persistence → response. You will encounter every major component by name.

### 2. `core/engine/conversation_engine.py`

Read `process_turn()`. This is the orchestration contract: what enters (text, user_id, session), what is called in sequence (plan → browse → eligibility → route → execute → post-process → render), and what exits (result dict). After reading this, you understand what every other layer does for the engine.

### 3. `core/planning/planner/turn_planner.py`

The largest and most complex single function in the system. Read `plan_turn()` in sections: tenant context, Luma call, intent resolution, missing-slot computation, session merge gate, plan building. Understanding the merge gate (`should_merge_session_context`) is essential.

### 4. `core/config/intent_policy.yaml`

Read the `CREATE_APPOINTMENT` and `CANCEL_BOOKING` entries. The policy file makes the planner's behaviour concrete: you will see exactly which slots are required, which steps exist, and what business facts gate each step.

### 5. `core/planning/planner/plan_builder.py`

Read `build_decision_plan()`. This is where `intent_policy.yaml` is consumed and the execution decision is made. After `turn_planner.py` and the policy file, this function is straightforward.

### 6. `core/workflows/router.py` + `core/execution/action_runner.py`

Short files. Understand the routing table (`availability_client` → `"availability"`) and the dispatcher call chain. These make Step 14–15 of the walkthrough concrete.

### 7. `core/workflows/availability/workflow.py`

Read `process_search_result()`. This is where the system goes after a search returns: fingerprint, time-match, presentation payload, cache writes. Understanding this makes the availability fingerprint invariant clear.

### 8. `core/workflows/booking/workflow.py`

Shorter than availability. Read `process_result()` to understand slot propagation after a booking commit.

### 9. `core/rendering/response_renderer.py`

Read `render_availability()` and `_inject_rendering_text_impl()`. These show the three rendering paths: availability text, clarification text, and outcome text.

### 10. `core/session/session_projector.py` → `core/session/persist.py`

`SessionProjector` is a one-method facade. The real logic is in `build_session_state_from_outcome()` in `persist.py`. Reading this shows exactly what gets persisted and why.

---

```
Architecture documentation complete.

Document:
docs/architecture/core-request-flow.md

Covers:

✓ Layered architecture
✓ Request lifecycle
✓ Session lifecycle
✓ Component responsibilities
✓ Sequence diagram
✓ Reading guide
```
