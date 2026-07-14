# Investigation – Architectural Hotspots After Cleanup

## Measurement Method

- **Lines**: physical source lines (blank + comment lines included, as they signal conceptual weight)
- **Functions**: top-level `def` / `class` / nested `def` counted via grep
- **Inbound callers**: file-level import grep across all production modules
- **Outbound imports**: top-level `from` / `import` statements per file
- **Monolith score**: largest single function (lines), used to distinguish "many small functions" from "few large ones"

Only production code is measured. Test files, `__pycache__`, `perf/`, and `*.pyc` are excluded.

---

## Top 10 Largest/Most Complex Production Modules

| Rank | File | Lines | Functions | Largest fn (lines) | Inbound callers | Outbound imports |
|---|---|---|---|---|---|---|
| 1 | `luma/app/resolve_service.py` | 3 985 | 8 | `resolve_message` — 3 524 | 7 | 21 |
| 2 | `luma/resolution/semantic_resolver.py` | 3 445 | 47 | `resolve_semantics` — 365 | 6 | 12 |
| 3 | `core/planning/orchestration/turn_planner.py` | 2 889 | 1 | `plan_turn` — 2 853 | 1 | 15 |
| 4 | `core/session/merge.py` | 2 059 | 3 | `merge_luma_with_session` — 1 987 | 13 | 7 |
| 5 | `luma/calendar/calendar_binder.py` | 1 952 | 28 | `bind_calendar` — 442 | 9 | 4 |
| 6 | `core/orchestration/nlu/luma_response_processor.py` | 1 505 | 11 | `process_luma_response` — 875 | 7 | 11 |
| 7 | `core/orchestration/execution/dispatcher.py` | 1 454 | 15 | `_execute_confirm_appointment` — 153 | 6 | 4 |
| 8 | `luma/decision/decision.py` | 1 158 | 5 | `decide_booking_status` — 727 | 13 | 7 |
| 9 | `core/planning/orchestration/plan_builder.py` | 1 013 | 12 | `build_decision_plan` — 461 | 9 | 5 |
| 10 | `core/session/persist.py` | 922 | 4 | `build_session_state_from_outcome` — 802 | 15 | 9 |

Notable near-misses (outside top 10 but architecturally significant):

| File | Lines | Largest fn (lines) | Inbound callers |
|---|---|---|---|
| `core/orchestration/temporal_proposal.py` | 972 | `try_bind_offered_time_selection` — 217 | 20 |
| `core/planning/orchestration/intent_resolution.py` | 619 | `resolve_effective_intent` — 582 | 4 |

---

## Responsibility Analysis

### 1 · `luma/app/resolve_service.py`

**Single-function monolith.** `resolve_message()` is 3 524 lines — the longest function in the codebase. It is the Flask `/resolve` handler body, extracted from `api.py` to make `api.py` testable. The extraction did not decompose the logic; it only relocated it.

**Responsibilities packed into one function:**
- Parse HTTP request body and validate input
- Call Luma extraction pipeline (`EntityMatcher`, date/time extraction, semantic resolver)
- Invoke intent grouping (`reservation_intent_resolver`)
- Run decision layer (`decide_booking_status`)
- Run calendar binding (`bind_calendar`, `bind_times`)
- Normalize service and time slots
- Apply session carry-forward (confirmation detection)
- Build API response body

Receives 12 parameters (Flask globals + injected helpers), making it difficult to test in isolation and impossible to trace linearly.

**Complexity verdict:** accidental — grew from handler growth; the extraction created a false sense of decomposition.

---

### 2 · `luma/resolution/semantic_resolver.py`

**Large but structurally decomposed.** 47 functions across multiple distinct responsibility groups: date semantics (16 helpers), time semantics (9 helpers), weekday/range detection (8 helpers), service variant resolution (3 helpers), temporal shape validation (1 function).

The primary public function `resolve_semantics()` (365 lines) orchestrates these helpers. It calls into all the sub-groups in sequence, making it a pipeline coordinator rather than a monolith.

**Responsibilities:**
- Date semantic classification: bare weekday, range, locale ambiguity, month-relative, etc.
- Time semantic resolution: fuzzy hour detection, 12h/24h conversion, time constraint derivation
- Service variant resolution: ambiguity detection, canonical expansion
- Temporal shape validation: ensuring date+time completeness before commit

**Complexity verdict:** mostly essential — natural language date/time resolution is inherently edge-case heavy. The 47 helper functions are signs of appropriate decomposition, not a god object.

---

### 3 · `core/planning/orchestration/turn_planner.py`

**Single-function monolith (Core's largest).** `plan_turn()` is 2 853 lines. The module docstring says "planning only" but the function contains infrastructure that transcends pure planning:

- Phase 1 (lines ~60–190): Tenant context derivation — API calls to `CatalogClient`, `OrganizationClient`, `org_domain_cache`, `catalog_cache`
- Phase 2 (lines ~190–600): Luma invocation — payload building, `LumaClient.call()`, error handling, empty-response handling, session lifecycle rules for null responses
- Step 2 (lines ~599–730): Contract assertion
- Step 3.5 (lines ~731–1148): Intent resolution — calls `resolve_effective_intent()`, evaluates `session_reset_occurred`, handles UNKNOWN→concrete intent transitions, non-durable intent short-circuit
- Step 4 (lines ~1301–2828): Session merge gating, `merge_luma_with_session()`, `process_luma_response()`, capability reconciliation, plan construction

**Responsibilities (distinct):**
1. Tenant context loading (infrastructure)
2. NLU invocation (Luma call)
3. Contract enforcement
4. Intent resolution and session lifecycle management
5. Planning (the stated purpose)
6. Outcome construction

Five responsibilities beyond planning. The function has grown to be the "integration test" of the entire Core pipeline.

**Complexity verdict:** accidental — the five phases existed before extraction; they were never separated as `plan_turn` grew to absorb them.

---

### 4 · `core/session/merge.py`

**Single-function monolith (session layer's largest).** `merge_luma_with_session()` is 1 987 lines. The three-function file exists almost entirely for this one function.

**Responsibilities inside `merge_luma_with_session`:**
1. Slot extraction from Luma facts (calls `facts_to_slots`, `merge_promoted_luma_slots`)
2. Confirmation state rehydration from persisted session
3. Intent continuity: UNKNOWN recovery, intent-change detection
4. Session reset detection and `session_reset_occurred` propagation
5. Slot merge (`{**session_slots, **raw_luma_slots}`)
6. Slot promotion (`promote_slots_for_intent`)
7. Effective collected slots computation (`_compute_effective_collected_slots_internal`)
8. Missing slots derivation (calls into planning policy)
9. Modification context detection (MODIFY_BOOKING, MODIFY_RESERVATION)
10. Domain slot filtering (`filter_slots_by_domain`, `filter_collected_slots_for_intent`)

The function handles 10 distinct concerns. It is at the session/NLU/planning boundary and has accumulated logic from all three owners.

**Complexity verdict:** mixed — slot merge rules are genuinely complex (intent continuity, domain isolation, promotion idempotency), but the 10 concerns should be separate functions even if they remain in the same file.

---

### 5 · `luma/calendar/calendar_binder.py`

**Large but functionally coherent.** The 28 functions form two groups: date binding (14 helpers, `bind_calendar`, `_bind_dates`, `_bind_single_date`, `_parse_absolute_date`) and time binding (7 helpers, `bind_times`, `_normalize_time_string`, `_parse_time`).

The largest function `bind_calendar()` (442 lines) is a pipeline coordinator for date reference resolution: it classifies date references, resolves them to concrete dates, checks ambiguity, and returns structured binding results. The 442 lines contain many conditional branches because date references in natural language have many forms.

**Complexity verdict:** mostly essential — datetime binding is a core NLU primitive with legitimate edge-case depth. The file is large but internally structured.

---

### 6 · `core/orchestration/nlu/luma_response_processor.py`

**Large primary function with adequate helpers.** `process_luma_response()` (875 lines) is the Core-side interpreter of Luma output. It produces the decision plan (status, allowed actions, awaiting) that drives the rest of the turn.

**Responsibilities inside `process_luma_response`:**
1. Intent resolution (UNKNOWN recovery, effective intent extraction)
2. Calls `build_decision_plan()` from `plan_builder.py`
3. Decides CLARIFY vs EXECUTE path based on plan status
4. Processes `NEEDS_CLARIFICATION`: builds clarification outcome, extracts reason
5. Processes `AWAITING_CONFIRMATION`: extracts booking payload
6. Processes `READY`: prepares execution payload
7. Time constraint propagation through the response
8. Turn state construction (`_build_turn_state`)

The function is large but its 11-function module includes reasonable helpers for sub-tasks. The 875-line main function remains too large for a single responsibility.

**Complexity verdict:** mixed — the three-path (CLARIFY / CONFIRM / EXECUTE) dispatch is inherently branchy, but responsibility 1 (intent resolution) belongs upstream and responsibility 7 (time constraint propagation) is an embedded side concern.

---

### 7 · `core/orchestration/execution/dispatcher.py`

**Well-structured despite size.** The dispatcher pattern is correctly applied: `execute()` is a 91-line routing table delegating to 7 `_execute_*` functions. The largest individual handler (`_execute_confirm_appointment`) is 153 lines — not a monolith.

The file is large (1 454 lines) because it contains all 7 execution action implementations inline. Each handler is self-contained.

**Complexity verdict:** mostly accidental size — the file could be split by action group (availability vs. booking vs. modification) without loss of coherence, but it is not a god object. Risk is lower than rank implies.

---

### 8 · `luma/decision/decision.py`

**Two unrelated responsibilities.** The file contains:
- `resolve_tenant_service_id()` — 256 lines; resolves NLU canonical service names to tenant-specific IDs via variant matching and fuzzy scoring. This is entity resolution, not decision logic.
- `decide_booking_status()` — 727 lines; evaluates temporal completeness, slot completeness, and service resolution to produce RESOLVED / NEEDS_CLARIFICATION / PARTIAL status.

The two functions share no state and have different owners: entity resolution is NLU-adjacent, decision logic is planning-adjacent.

`decide_booking_status()` is the second-longest function in the Luma package. It is called by 13 files (via `decision.py` imports), making it a high-churn, high-impact function.

**Complexity verdict:** `decide_booking_status` — mostly essential (booking status derivation is complex policy logic); `resolve_tenant_service_id` — misplaced, belongs in a resolution/entity module.

---

### 9 · `core/planning/orchestration/plan_builder.py`

**Well-structured policy interpreter.** 12 functions, largest is `build_decision_plan()` at 461 lines. This function interprets `intent_policy.yaml` to select the next execution step, evaluates capability blocking conditions, enforces committing step invariants, and derives the plan stage.

The 461-line `build_decision_plan` is large but necessary: it evaluates multiple policy conditions in sequence. The helper functions (`_evaluate_condition`, `_evaluate_capability_blocking`, `_enforce_committing_step_invariants`) demonstrate appropriate decomposition.

**Complexity verdict:** mostly essential — intent policy interpretation is the core of the planner and requires evaluating many conditions. The structure is sound.

---

### 10 · `core/session/persist.py`

**Single-function monolith (persistence).** `build_session_state_from_outcome()` is 802 lines — 87% of the file. It builds the complete session dict written to Redis after each turn.

**Responsibilities inside `build_session_state_from_outcome`:**
1. Normalize execution outcome fields
2. Persist intent_name and slots
3. Persist effective_collected_slots
4. Persist missing_slots and confirmation_state
5. Extract and persist service candidates
6. Persist availability results (`last_execution_result`, `presented_availability`)
7. Persist temporal proposals and time constraints
8. Apply payment/capability state
9. Persist modification context
10. Handle booking_id and post-commit state cleanup

10 distinct concerns, identical to `merge_luma_with_session`. The function is the write-side of the session boundary; `merge.py` is the read-side. Both suffer from the same structural problem: all concerns accumulated in one function.

**Complexity verdict:** accidental — each concern is a legal mapping from planning outcome to session key, but they should be extracted into composable sub-functions rather than inline blocks.

---

## Dependency Analysis

### High-fan-in modules (inbound callers ≥ 10)

These are **infrastructure touchpoints** — the most likely blast radius for any change.

| Module | Inbound callers | Risk of change |
|---|---|---|
| `core/policy/intent_policy.py` | 22 | **Critical** — all planning logic flows through it |
| `core/orchestration/temporal_proposal.py` | 20 | **High** — availability + slot expansion shared widely |
| `core/session/persist.py` | 15 | **High** — session shape changes propagate everywhere |
| `core/session/merge.py` | 13 | **High** — merge behavior affects all multi-turn flows |
| `luma/decision/decision.py` | 13 | **High** — booking status shared by Luma pipeline and Core |
| `core/session/confirmation_gate.py` | 13 | **High** — confirmation state queried by merge, planner, orchestrator |

### High-fan-out modules (outbound imports ≥ 10)

These are **integration hubs** — modules that have no clear owner because they import from many layers.

| Module | Outbound imports | Architectural concern |
|---|---|---|
| `luma/app/resolve_service.py` | 21 | Imports across all Luma layers: extraction, calendar, decision, grouping |
| `core/planning/orchestration/turn_planner.py` | 15 | Imports from NLU, session, planning, orchestration, rendering |
| `core/orchestration/nlu/luma_response_processor.py` | 11 | Imports from planning, session, policy, temporal |
| `core/session/persist.py` | 9 | Imports from orchestration, session, tracing |

---

## Architectural Risk Assessment

### Risk Level: Critical

**`luma/app/resolve_service.py` — `resolve_message()` (3 524 lines)**

A 3 524-line function that cannot be unit-tested without mocking Flask globals, 4 injected helpers, and the entire Luma pipeline. Any change anywhere in Luma risks undetected regression here. The extraction from `api.py` deferred the decomposition without solving it.

### Risk Level: High

**`core/session/merge.py` — `merge_luma_with_session()` (1 987 lines)**

13 callers, 10 concerns, one function. The session merge rules are the primary source of multi-turn bugs. When a bug report says "slot X disappeared after turn 2", this is almost always where the investigation starts. A 1 987-line function is hard to reason about and harder to test at the granularity needed.

**`core/planning/orchestration/turn_planner.py` — `plan_turn()` (2 853 lines)**

The only caller is `plan_message()` in `orchestrator.py`. The function is effectively the entire Core pipeline. Changes to tenant-context loading, Luma invocation strategy, intent resolution logic, or session lifecycle all land here. No decomposition exists below the single function boundary.

**`core/session/persist.py` — `build_session_state_from_outcome()` (802 lines)**

15 callers. Any change to what gets persisted (new session key, new normalization rule) must be made in a single 802-line function with no internal structure. Side effects are hard to isolate.

**`luma/decision/decision.py` — `decide_booking_status()` (727 lines)**

13 callers across Luma and Core. The booking status decision is a load-bearing invariant — it determines whether the Luma pipeline returns RESOLVED, NEEDS_CLARIFICATION, or PARTIAL. At 727 lines with multiple conditional branches per booking type, changes carry high regression risk.

### Risk Level: Medium

**`core/orchestration/nlu/luma_response_processor.py` — `process_luma_response()` (875 lines)**

Well-supported by 10 helper functions, but the main function mixes intent resolution, dispatch to plan_builder, and response construction. Changes to any of the three paths risk affecting the others.

**`core/orchestration/execution/dispatcher.py` (1 454 lines)**

Low structural risk (15 functions, none large). The risk is that adding a new execution action requires adding a new handler inline — the file grows without bound. Not urgent.

### Risk Level: Low (essential complexity)

**`luma/resolution/semantic_resolver.py`** — Large but decomposed into 47 functions. The complexity is inherent to NLP date/time semantics. No structural intervention needed; any decomposition would be into separate files (not functions), which is cosmetic.

**`luma/calendar/calendar_binder.py`** — Similar to `semantic_resolver.py`. Date binding is genuinely complex. The functions are appropriately sized.

---

## Essential vs. Accidental Complexity

| Module | Dominant complexity type | Justification |
|---|---|---|
| `luma/resolution/semantic_resolver.py` | **Essential** | NLP date/time semantics are inherently edge-case dense |
| `luma/calendar/calendar_binder.py` | **Essential** | Timezone-aware date binding with ambiguity resolution is domain-hard |
| `luma/decision/decision.py` | **Essential** (one fn), **Misplaced** (one fn) | `decide_booking_status` — domain hard; `resolve_tenant_service_id` — belongs elsewhere |
| `core/planning/orchestration/plan_builder.py` | **Essential** | Policy evaluation requires many condition checks by design |
| `core/session/confirmation_gate.py` | **Essential** | Confirmation state machine is inherently complex |
| `core/orchestration/temporal_proposal.py` | **Essential** | Temporal proposal/slot duality is domain-hard |
| `luma/app/resolve_service.py` | **Accidental** | Handler body was extracted but not decomposed |
| `core/planning/orchestration/turn_planner.py` | **Accidental** | Pipeline phases accumulated in one function |
| `core/session/merge.py` | **Mixed** | Merge rules are essential; 10 concerns in one function is accidental |
| `core/session/persist.py` | **Accidental** | Each persistence concern is trivial; their aggregation in one function is historical |

---

## Prioritized Roadmap for Future Decomposition

### Priority 1 — `core/session/merge.py` : `merge_luma_with_session()`

**Why first:** 13 callers, 10 responsibilities, 1 987-line function. This is the primary location where multi-turn session bugs occur and where regression risk from any slot-related change is highest. Decomposing it into focused sub-functions (one per concern) would yield:

- `_rehydrate_confirmation_state(merged, session_state)` — confirmation state carry-forward
- `_resolve_luma_intent(merged, session_state)` — UNKNOWN recovery, intent continuity
- `_extract_and_merge_slots(merged, session_state, effective_intent)` — slot extraction + merge
- `_promote_and_filter_slots(merged, effective_intent)` — promotion + domain filter
- `_compute_effective_and_missing_slots(merged, effective_intent)` — planning completeness
- `_detect_modification_context(merged, effective_intent)` — modification type detection

Each sub-function becomes independently testable. `merge_luma_with_session()` becomes a coordinator of ~15 lines. The session merge invariants (documented in AGENTS.md) would each map to one function, making them verifiable.

**Estimated scope:** medium — all logic stays in `merge.py`, only internal structure changes.

---

### Priority 2 — `core/session/persist.py` : `build_session_state_from_outcome()`

**Why second:** 15 callers (highest inbound count among monolith functions), 10 concerns, 802 lines. The persistence layer is the write-side counterpart to merge's read-side. The same decomposition pattern applies: extract each persistence concern (slots, confirmation, availability, modification context, payment state) into a named sub-function that accepts the builder dict and mutates it in place.

**Estimated scope:** medium — same file, structural only.

---

### Priority 3 — `luma/app/resolve_service.py` : `resolve_message()`

**Why third:** highest absolute line count (3 524 lines), but this is the Luma package and active production uses `luma/`. The function already receives 12 injected dependencies — the natural decomposition would extract pipeline stages into named functions that `resolve_message()` coordinates:

- `_run_extraction_pipeline(request_data, intent_resolver, g)` — extraction + semantic
- `_run_decision_pipeline(semantic_result, session, ...)` — decision + calendar binding
- `_build_api_response(decision_result, ...)` — response normalization

This would make `resolve_message()` a 50-line coordinator and each stage independently testable.

**Estimated scope:** large — the function has many local variable dependencies across stages; the refactor requires careful closure over shared state.

---

### Priority 4 — `core/planning/orchestration/turn_planner.py` : `plan_turn()`

**Why fourth:** only 1 caller, so change blast radius is limited. But as the Core pipeline entry point, it is the primary target for debugging any integration issue. Extracting the 5 phases into named functions would make the pipeline readable:

- `_derive_tenant_context(organization_id, ...)` — context loading
- `_invoke_luma(text, tenant_context, session, luma_client, ...)` — NLU call + empty handling
- `_resolve_intent(luma_response, session_state, ...)` — intent resolution
- `_gate_session_merge(luma_response, session_state, ...)` — merge gating + merge
- `_build_plan(effective_response, domain, session_state, ...)` → planning output

`plan_turn()` becomes a 30-line orchestrator. Each phase becomes testable with a mock for the prior phase's output.

**Estimated scope:** large — many local variables cross phase boundaries; requires careful extraction.

---

### Priority 5 — `luma/decision/decision.py` : relocate `resolve_tenant_service_id()`

**Why fifth:** this is a targeted extraction, not a decomposition. `resolve_tenant_service_id()` (256 lines) is entity resolution logic — it does variant matching, fuzzy scoring, and canonical-to-full normalization. It does not belong in `decision.py` (which owns booking status policy). Moving it to a dedicated `luma/resolution/entity_resolver.py` (or merging with `semantic_resolver.py`) clarifies ownership without breaking any callers.

**Estimated scope:** small — one function, move and update import.

---

### Priority 6 — `core/orchestration/execution/dispatcher.py` : split by action group

**Why sixth:** not urgent (well-structured internally), but the file will grow with every new execution action. Pre-emptive split by intent domain:

- `dispatcher.py` — `execute()` routing table only
- `execution/availability_handlers.py` — `_execute_search_availability`, `_execute_service_availability`, `_execute_reservation_availability`
- `execution/booking_handlers.py` — `_execute_confirm_appointment`, `_execute_create_booking_hold`, `_execute_finalize_reservation`, `_execute_fetch_booking`
- `execution/modification_handlers.py` — `_execute_apply_modification`, `_execute_confirm_cancellation`

**Estimated scope:** small — pure file split, no logic changes.

---

## Summary Table

| Priority | Module | Function | Lines | Dominant issue | Scope |
|---|---|---|---|---|---|
| 1 | `core/session/merge.py` | `merge_luma_with_session` | 1 987 | 10 concerns in 1 fn, 13 callers | Medium |
| 2 | `core/session/persist.py` | `build_session_state_from_outcome` | 802 | 10 concerns in 1 fn, 15 callers | Medium |
| 3 | `luma/app/resolve_service.py` | `resolve_message` | 3 524 | Pipeline handler never decomposed | Large |
| 4 | `core/planning/orchestration/turn_planner.py` | `plan_turn` | 2 853 | 5 phases in 1 fn, 1 caller | Large |
| 5 | `luma/decision/decision.py` | `resolve_tenant_service_id` | 256 | Misplaced responsibility | Small |
| 6 | `core/orchestration/execution/dispatcher.py` | (file-level) | 1 454 | Pre-emptive split before growth | Small |
