# Core Architecture Investigation — Production Request Path

**Utterance traced:** "book me a premium haircut tomorrow by 9am"  
**Method:** Static code analysis only. No test execution. No code changes. All line-number references verified by direct file reads.

---

## 1. Production Request Flow

The complete call chain from HTTP receipt to session persistence.

### Entry: FastAPI

```
POST /api/message
  → core/orchestration/api/message.py:post_message()         [line 109]
```

**`post_message()` steps (lines 134–579):**

1. Bootstrap extensions (once per process, guarded by `_BOOTSTRAPPED`).
2. Initialize `TurnTrace.begin()` if decision-trace header/env active.
3. `get_session(request.user_id)` — unconditional load from store.
4. **Session filter (lines 216–220):** pass session only if `status in ("NEEDS_CLARIFICATION", "AWAITING_CAPABILITY")`, else `session_state = None`.
5. Call `handle_message(user_id, text, domain, timezone, organization_id, session_state, transaction_id, availability_client, booking_client, session_store)`.
6. Capability boundary: if outcome status is `AWAITING_CAPABILITY`, delegate to `CapabilityRunner` and return early.
7. Handler boundary: if status is `HANDLER_DELEGATED`, invoke `HandlerRunner`, render via LLM, save session directly, return.
8. Session persistence: if outcome status in `("NEEDS_CLARIFICATION", "AWAITING_CONFIRMATION", "AWAITING_CAPABILITY", "READY", "EXECUTED", "success")`:
   - `_session_projector.project(...)` → `build_session_state_from_outcome()`
   - `append_messages_turn(new_session_state, ...)`
   - `save_session(request.user_id, new_session_state)`
9. Serialize to `MessageResponse` and return.

Execution clients (`_availability_client`, `_booking_client`) are module-level singletons initialized at import time. They are passed into `handle_message()` — they are never instantiated inside the orchestrator.

---

### Orchestrator: `handle_message()`

```
core/orchestration/orchestrator.py:handle_message()          [line 279]
```

**Steps (lines 327–1082):**

1. Instantiate per-call Phase 2 boundaries (local; not module-level):
   - `ActionRunner`, `ResponseRenderer`, `AvailabilityWorkflow`, `BookingWorkflow`, `WorkflowRouter`
2. Load session from `session_store.get_session()`. Falls back to `kwargs["session_state"]`, then to `get_session()` directly (FALLBACK 2, line 410).
3. Call `plan_message(text, user_id, session_state, ...)` → `plan_turn()`.
4. If `plan.error`: return failure.
5. If `plan.status == "HANDLER_DELEGATED"`: return early without execution.
6. `_availability_workflow.try_handle_browse_turn(plan, session_state, session_store, user_id)` — returns `None` when not a browse turn, returns full result when it is.
7. Policy eligibility check (lines 573–631):
   - `get_execution_steps(intent_name)` — from `intent_policy.yaml`
   - Match `plan.action` to step; read `mode` and `required_slots`
   - `mode=="exploratory"` → `can_execute = slots_satisfied`
   - `mode=="committing"` → `can_execute = plan_status=="READY" and slots_satisfied`
   - If `not can_execute`: return `build_planning_response_from_plan(plan)` (no execution)
8. Client resolution (lines 660–709):
   - `client_name = execution_step["client"]` (from policy)
   - Map `"availability_client"` → `availability_client` arg
   - Map `"booking_client"` → `kwargs["booking_client"]`
   - If client missing: return planning outcome
9. Pre-execution slot injection:
   - `organization_id` into `slots` if absent
   - `slots_for_availability_search()` for date/time proposal resolution (SEARCH_AVAILABILITY only)
   - `load_sku_to_catalog_id_for_org()` → `plan["sku_to_catalog_id"]`
   - FINALIZE_RESERVATION: inject `facts` from session into `plan`
   - CONFIRM_APPOINTMENT: inject `resolved_datetime_range` from session into `slots["datetime_range"]`
10. Dispatch (lines 849–999):
    - `_workflow_router.get_route(client_name)` → `"availability"` or `"booking"`
    - `_action_runner.run(plan, ...) → dispatcher.execute(plan, ...)`
11. Post-execution processing:
    - `_booking_workflow.process_result()` — slot propagation for booking actions
    - If `execution_result["type"]=="availability" and status=="success"`: `_availability_workflow.process_search_result()` — fingerprint, time resolution, presentation, `_persist_to_session()`
    - `build_outcome_from_decision(decision)` — attach plan structure
    - `sync_execution_plan_from_time_resolution()`
    - `_renderer.render_availability()` + `_renderer.render_outcome()`
12. Return `{"success": True, "result": execution_result, "outcome": execution_result, "plan": plan}`.

---

### Planning: `plan_turn()`

```
core/planning/orchestration/turn_planner.py:plan_turn()      [line 37]
```

**Key steps (abbreviated):**

1. Resolve `organization_id` from env/arg.
2. `org_domain_cache.get_domain()` — derive domain from org (cached).
3. `catalog_cache.get_services()` — get catalog for NLU context (cached).
4. `update_conversation()` — build conversation history for NLU context.
5. `luma_client.call(text, context)` — NLU call (Luma).
6. `assert_luma_contract(response)` — validate shape.
7. Durability/handler check: `is_durable_intent(effective_intent)`. Non-durable → return early. `resolve_handler()` → `HANDLER_DELEGATED` if RAG-routed.
8. `effective_response = luma_response.copy()` with overridden intent name.
9. `missing_slots = compute_missing_slots(...)` — computed before `process_luma_response()`.
10. `effective_response["missing_slots"] = missing_slots` — MUST be non-None (invariant).
11. `process_luma_response(effective_response, session_state, ...)` — session merge + plan.
12. Returns `{"success": True, "outcome": {...}, "_merged_luma_response": ..., "_decision": ...}`.

---

### Execution: `dispatcher.execute()`

```
core/orchestration/execution/dispatcher.py:execute()         [line 17]
```

Routes on `plan["action"]`:

| Action | Handler | Client method |
|---|---|---|
| `SEARCH_AVAILABILITY` | `_execute_search_availability()` | `availability_client.get_service_availability(org_id, service_id, date)` |
| `CONFIRM_APPOINTMENT` | `_execute_confirm_appointment()` | `booking_client.create_booking(...)` |
| `CONFIRM_CANCELLATION` | `_execute_confirm_cancellation()` | `booking_client.cancel_booking(...)` |
| `FETCH_BOOKING` | `_execute_fetch_booking()` | `booking_client.get_booking(booking_code)` |
| `CREATE_BOOKING_HOLD` | `_execute_create_booking_hold()` | `booking_client.create_booking(...)` |
| `APPLY_MODIFICATION` | `_execute_apply_modification()` | `booking_client.update_booking(...)` |
| `FINALIZE_RESERVATION` | `_execute_finalize_reservation()` | `booking_client.confirm_booking(...)` |

SEARCH_AVAILABILITY normalization: `_normalize_availability_response()` maps raw `{slots: [{start, end, ...}]}` to `{type: "availability", status: "success", slots: [{starts_at, ends_at}]}`.

---

### Session Projection: `build_session_state_from_outcome()`

```
core/session/persist.py:build_session_state_from_outcome()   [~line 1]
core/session/appointment_extensions.py:apply_create_appointment_extensions()  [line 232]
```

Called by `SessionProjector.project()` from `message.py`. Builds session dict then calls `apply_create_appointment_extensions()` as a post-construction mutation. For `CREATE_APPOINTMENT`, this function writes: `last_execution_result`, `presented_availability`, `availability_presentation`, `resolved_datetime_range`, `availability_fingerprint`, `confirmation_state`.

---

## 2. Responsibility Map

| Concern | Owner | File |
|---|---|---|
| HTTP boundary, session filter, client lifecycle | `post_message()` | `core/orchestration/api/message.py` |
| Capability activation | `apply_capability_to_result()` | `core/orchestration/api/capability_boundary.py` |
| Session projection + persist | `SessionProjector` → `build_session_state_from_outcome()` | `core/session/session_projector.py`, `core/session/persist.py` |
| Turn orchestration (planning + execution) | `handle_message()` | `core/orchestration/orchestrator.py` |
| NLU call, session merge, plan construction | `plan_turn()` | `core/planning/orchestration/turn_planner.py` |
| Action/stage selection | `build_decision_plan()`, `plan_builder.py` | `core/planning/orchestration/plan_builder.py` |
| Business facts derivation | `derive_business_facts()` | `core/planning/facts/business_fact_registry.py` |
| Availability pagination | `try_handle_browse_turn()` | `core/orchestration/availability_pagination.py` |
| Policy step eligibility | `get_execution_steps()` | `core/policy/intent_policy.py` + `core/config/intent_policy.yaml` |
| Execution dispatch routing | `WorkflowRouter.get_route()` | `core/workflows/router.py` |
| Action execution dispatch | `ActionRunner.run()` → `dispatcher.execute()` | `core/execution/action_runner.py`, `core/orchestration/execution/dispatcher.py` |
| Fingerprint, time resolution, presentation | `AvailabilityWorkflow.process_search_result()` | `core/workflows/availability/workflow.py` |
| Booking slot propagation | `BookingWorkflow.process_result()` | `core/workflows/booking/workflow.py` |
| CREATE_APPOINTMENT session fields | `apply_create_appointment_extensions()` | `core/session/appointment_extensions.py` |
| Rendering (availability, outcome) | `ResponseRenderer` | `core/rendering/response_renderer.py` |
| Intent durability gate | `is_durable_intent()` | `core/orchestration/persistence/durable_intents.py` |
| Handler routing (RAG) | `resolve_handler()` | `core/routing/handler_router.py` |
| Workflow after_execute hooks | `WorkflowRegistry.get()` | `core/routing/workflows/workflow.py` |

---

## 3. Dynamic Call Graph

For "book me a premium haircut tomorrow by 9am" — Turn 1 (no session, first message):

```
POST /api/message
  post_message()
    get_session(user_id)                      → None (no prior session)
    session_state = None                      (status filter: no session)
    handle_message(text, user_id, ..., session_state=None)
      plan_message(text, user_id, session_state=None)
        plan_turn(...)
          LumaClient.call(text, context)
            → {intent: CREATE_APPOINTMENT, slots: {service: "premium haircut", date: "tomorrow", time: "9am"}}
          is_durable_intent("CREATE_APPOINTMENT")   → True
          missing_slots = compute_missing_slots(...)
          process_luma_response(effective_response, session_state=None)
            merge_luma_with_session(...)            → effective_turn_slots
            build_decision_plan(...)
              derive_business_facts(...)
              plan_builder.build(...)               → action=SEARCH_AVAILABILITY (service_id not yet resolved)
            → plan {intent_name, stage, action, slots, missing_slots, status, _decision}
      try_handle_browse_turn(...)                   → None (not a browse turn)
      get_execution_steps("CREATE_APPOINTMENT")     → steps from intent_policy.yaml
      step matched: {action: SEARCH_AVAILABILITY, mode: exploratory, required_slots: [service_id]}
      can_execute = slots_satisfied                 → True (service_id in slots)
      _workflow_router.get_route("availability_client")  → "availability"
      _action_runner.run(plan, availability_client=...)
        dispatcher.execute(plan, availability_client=...)
          _execute_search_availability(plan, client)
            availability_client.get_service_availability(org_id, service_id, date)
            _normalize_availability_response(response)
            → {type: "availability", status: "success", slots: [...]}
      _booking_workflow.process_result(...)         → no-op (not a booking action)
      _availability_workflow.process_search_result(execution_result, plan, slots, ...)
        build_availability_fingerprint_slots(...)
        compute_availability_fingerprint(...)       → fingerprint hash
        _persist_to_session(store, user_id, ..., "availability_fingerprint", fp)
        resolve_time_after_availability(...)        → time_resolution
        enrich_last_execution_result(exec_result)
        build_presented_availability(raw_slots)
        build_availability_presentation(raw_slots)
        _persist_to_session(..., "last_execution_result", ...)
        _persist_to_session(..., "presented_availability", ...)
        _persist_to_session(..., "availability_presentation", ...)
      _renderer.render_availability(result, decision, exec_result, session_state)
      _renderer.render_outcome(result, decision, exec_result)
      → {success: True, result: execution_result, outcome: execution_result, plan: plan}
    apply_capability_to_result(...)                 → None (no capability needed yet)
    _session_projector.project(outcome, "success", merged_luma, ...)
      build_session_state_from_outcome(...)
        apply_create_appointment_extensions(session_state, "CREATE_APPOINTMENT", outcome, ...)
          _extract_availability_execution_result(outcome) → exec_result (Branch 2)
          enrich_last_execution_result(exec_result)
          build_presented_availability(...)
          build_availability_presentation(...)
          resolve_availability_fingerprint(...)
          set_confirmation_state(...) or _maybe_persist_booking_confirmation_pending(...)
    append_messages_turn(new_session_state, user_text, reply_text)
    save_session(user_id, new_session_state)
    → MessageResponse(success=True, outcome=..., text=...)
```

---

## 4. Live vs Dormant Table

| Module | Status | Evidence |
|---|---|---|
| `core/orchestration/api/message.py:post_message()` | **LIVE** | Primary FastAPI entry; mounted at `/api/message` in `main.py` |
| `app.py:lambda_handler()` | **LIVE** | AWS Lambda entry; parallel path to FastAPI |
| `core/orchestration/orchestrator.py:handle_message()` | **LIVE** | Called from `message.py` line 227; core dispatch |
| `core/orchestration/orchestrator.py:plan_message()` | **LIVE** | Called from `handle_message()` line 476 |
| `core/planning/orchestration/turn_planner.py:plan_turn()` | **LIVE** | Called from `plan_message()` line 1123 |
| `core/orchestration/execution/dispatcher.py:execute()` | **LIVE** | Called via `ActionRunner.run()` line 41 |
| `core/workflows/availability/workflow.py:AvailabilityWorkflow` | **LIVE** | Instantiated in `handle_message()` line 336; `process_search_result()` is the post-search owner |
| `core/workflows/booking/workflow.py:BookingWorkflow` | **LIVE** | Instantiated in `handle_message()` line 337; `process_result()` owns booking slot propagation |
| `core/workflows/router.py:WorkflowRouter` | **LIVE** | Used in `handle_message()` line 849 to select availability vs booking path |
| `core/execution/action_runner.py:ActionRunner` | **LIVE** | Instantiated in `handle_message()` line 334; thin facade over dispatcher |
| `core/session/session_projector.py:SessionProjector` | **LIVE** | Instantiated at module level in `message.py` line 37; `project()` called line 388 |
| `core/session/persist.py:build_session_state_from_outcome()` | **LIVE** | Called by `SessionProjector.project()` |
| `core/session/appointment_extensions.py:apply_create_appointment_extensions()` | **LIVE** | Called inside `build_session_state_from_outcome()` |
| `core/orchestration/availability_pagination.py:try_handle_availability_browse_turn()` | **LIVE** | Called via `AvailabilityWorkflow.try_handle_browse_turn()` on every turn |
| `core/rendering/response_renderer.py:ResponseRenderer` | **LIVE** | Phase 2 rendering boundary; called lines 963–966 |
| `core/engine/outcome_builder.py:build_planning_response_from_plan()` | **LIVE** | Phase 1 neutral module; used for fallback outcome construction in orchestrator |
| `core/engine/outcome_builder.py:build_outcome_from_decision()` | **LIVE** | Called at lines 907, 1030 |
| `core/routing/handler_router.py:resolve_handler()` | **LIVE** | Called from `turn_planner.py` line 1115 for RAG routing |
| `core/routing/workflows/workflow.py:WorkflowRegistry` | **LIVE** | Registry is live; called from `_invoke_workflow_after_execute()` in orchestrator |
| `core/routing/workflows/examples/payment_prompt_workflow.py` | **DORMANT** | Example file; no registration in production bootstrap found |
| `core/routing/intent_router.py:INTENT_ACTIONS` | **DORMANT** | Imported only in a `logger.debug()` branch inside `luma_response_processor.py` line 1274 (dead-code path — only when `action_name` is missing) |
| `core/routing/action_router.py` | **DORMANT** | No live import found outside routing package and tests |
| `core/routing/clarification_router.py` | **DORMANT** | No live import found outside routing package |
| `core/routing/execution/config.py` | **DORMANT** | No live import found outside routing package |
| `core/orchestration/session_ops.py:_persist_to_session()` | **LIVE** | Used by `AvailabilityWorkflow.process_search_result()` for mid-turn writes |
| `core/config/org_resolver.py:_get_org_id_from_env()` | **LIVE** | Phase 1 neutral module; re-exported from orchestrator; used in turn_planner |

---

## 5. Architectural Paths by Turn Type

### Path A: Availability Search (Turn 1 — "book me a haircut tomorrow")

```
plan_turn → SEARCH_AVAILABILITY elected by planner
  → can_execute=True (exploratory, service_id present)
  → WorkflowRouter: "availability"
  → ActionRunner → dispatcher._execute_search_availability()
  → AvailabilityWorkflow.process_search_result() — fingerprint, time resolution, _persist_to_session()
  → ResponseRenderer.render_availability()
  → build_session_state_from_outcome() → apply_create_appointment_extensions() Branch 2
```

Mid-turn session writes (within `process_search_result()`): `availability_fingerprint`, `resolved_datetime_range` (if exact match), `last_execution_result`, `presented_availability`, `availability_presentation`.

End-of-turn session write: `build_session_state_from_outcome()` re-reads those same fields and re-writes them (Branch 2 in `apply_create_appointment_extensions()` recalculates from `execution_result` in `outcome`, not from store reads).

### Path B: Clarification (missing slots — "book a haircut")

```
plan_turn → SEARCH_AVAILABILITY elected by planner
  → missing_slots = ["date"]  (or service_id)
  → can_execute = False (required slots not satisfied)
  → build_planning_response_from_plan(plan)
  → status=NEEDS_CLARIFICATION, no execution
  → build_session_state_from_outcome() → status persisted
```

No execution clients are called. The session is persisted with `status=NEEDS_CLARIFICATION` and the collected slots so far.

### Path C: Confirmation (Turn N — user says "yes, confirm")

```
session loaded (status=AWAITING_CONFIRMATION preserved through filter)
plan_turn → CONFIRM_APPOINTMENT elected by planner
  → plan_status=READY, can_execute=True (committing mode)
  → WorkflowRouter: "booking"
  → ActionRunner → dispatcher._execute_confirm_appointment()
     IDEMPOTENCY CHECK: if slots.booking_id present, return existing booking
     else: booking_client.create_booking(...)
  → BookingWorkflow.process_result() → slots["booking_id"] = booking_id
  → build_session_state_from_outcome() → apply_create_appointment_extensions()
     → consume_create_appointment_confirmation() (clears confirmation_state)
```

### Path D: Browse/Pagination (Turn N — "show me more")

```
plan_turn → intent=AVAILABILITY, operation=browse_next
  → _availability_workflow.try_handle_browse_turn() returns a full result (not None)
  → handle_message returns early at line 559
  → NO execution dispatch
  → build_session_state_from_outcome() → apply_create_appointment_extensions() Browse branch
     → _apply_availability_browse_persistence() preserves full search cache
```

### Path E: Handler Delegation (informational — "what services do you offer?")

```
plan_turn → intent=PRODUCT_INQUIRY (non-durable or handler-routed)
  → resolve_handler("PRODUCT_INQUIRY") → "rag"
  → return {status: "HANDLER_DELEGATED", active_handler: "rag"}
  → handle_message returns early at line 515
  → post_message: HandlerRunner.handle("rag", context) → render via LLM → save session directly
```

---

## 6. Architectural Boundaries

The codebase has undergone a structured 3-phase refactor. Each phase introduced explicit architectural boundaries. The current state is Phase 2 complete, Phase 3 partially implemented.

### Phase 1 — Circular Dependency Break

**Problem:** `orchestrator.py` and `turn_planner.py` imported each other.

**Solution:** Four neutral modules introduced. Neither orchestrator nor planner imports the other. Both import from neutrals.

| Neutral module | Exported symbols |
|---|---|
| `core/engine/outcome_builder.py` | `_build_planning_outcome`, `build_outcome_from_decision`, `build_planning_response_from_plan` |
| `core/config/org_resolver.py` | `_get_org_id_from_env` |
| `core/rendering/response_renderer.py` | `_inject_rendering_text`, `_inject_availability_text`, `_inject_outcome_text`, `_inject_system_text`, `ResponseRenderer` |
| `core/orchestration/session_ops.py` | `_persist_to_session` |

All four are re-exported from `orchestrator.py` with `# noqa: F401` to preserve backward compatibility for existing callers.

### Phase 2 — Ownership Transfer Facades

**Problem:** `handle_message()` owned availability post-processing, booking slot propagation, and session projection inline.

**Solution:** Thin facade classes introduced. Each owns a domain; implementation still delegates to existing code.

| Facade | Domain | Implementation |
|---|---|---|
| `AvailabilityWorkflow` (`core/workflows/availability/workflow.py`) | Availability: browse, fingerprint, search, post-search processing | `availability_pagination.py`, `availability_fingerprint.py`, `dispatcher.execute()`, `session_ops._persist_to_session()` |
| `BookingWorkflow` (`core/workflows/booking/workflow.py`) | Booking: confirm, slot propagation | `dispatcher.execute()` |
| `WorkflowRouter` (`core/workflows/router.py`) | Route: client_name → domain | Static map (`availability_client` → `"availability"`, `booking_client` → `"booking"`) |
| `ActionRunner` (`core/execution/action_runner.py`) | Execute: plan → dispatcher | `dispatcher.execute()` |
| `SessionProjector` (`core/session/session_projector.py`) | Session: outcome → session state | `persist.build_session_state_from_outcome()` |
| `ResponseRenderer` (`core/rendering/response_renderer.py`) | Render: decision → text | `llm_renderer.render_llm()`, `availability_renderer.build_availability_render_request()` |

### Phase 3 — Routing/Workflow Extension System

**Problem:** No extension point for post-execution hooks without modifying core.

**Solution:** `WorkflowRegistry` in `core/routing/workflows/workflow.py`. Workflows register for an intent and receive `after_execute(outcome)` callbacks.

**Current state:** The hook is called in `orchestrator.py:_invoke_workflow_after_execute()` (line 195–227), which uses `get_workflow(intent_name)` from `core/routing/workflows/`. No workflows are registered in production (registry is empty). The example file `core/routing/workflows/examples/payment_prompt_workflow.py` is not imported by production code.

---

## 7. Unfinished Migration Analysis

### 7.1 Mid-turn vs End-of-turn Double Write

`AvailabilityWorkflow.process_search_result()` writes `last_execution_result`, `presented_availability`, `availability_presentation`, `availability_fingerprint`, and `resolved_datetime_range` mid-turn via `_persist_to_session()`. These same fields are then re-written end-of-turn by `apply_create_appointment_extensions()` (Branch 2, line 273–303 of `appointment_extensions.py`).

For normal search turns, Branch 2 recalculates from `outcome` (which IS `execution_result`), so the end-of-turn write is independent of the mid-turn writes. The mid-turn writes exist to support Branch 3 (subsequent non-search turns reading from store). This is the documented deferral in `core/session/ARCH_NOTES.md`.

### 7.2 Phase 3 Routing Modules — Partially Active

`core/routing/action_router.py`, `core/routing/clarification_router.py`, and `core/routing/execution/config.py` exist but have no live callers in the production path. `core/routing/intent_router.py:INTENT_ACTIONS` is imported only inside a `logger.debug()` call reachable only when `action_name` is missing. These are planned infrastructure for a routing layer that has not been activated.

`core/routing/handler_router.py:resolve_handler()` is live — used in `turn_planner.py` to route RAG intents.

### 7.3 `core/routing/` vs `core/workflows/`

Two routing-adjacent packages exist:

- `core/routing/` — contains `handler_router` (live), `intent_router` / `action_router` / `clarification_router` (dormant), and `workflows/` (registry live, no registered workflows).
- `core/workflows/` — contains `AvailabilityWorkflow`, `BookingWorkflow`, `WorkflowRouter` (all live Phase 2 facades).

These are architecturally separate: `core/workflows/` is the completed Phase 2 domain-boundary layer; `core/routing/` is a partially-implemented extension/routing system. They do not import each other.

### 7.4 Facades Are Thin — Implementation Lives Elsewhere

All Phase 2 facades are currently thin delegation wrappers. `AvailabilityWorkflow.process_search_result()` contains real logic (293 lines of it), but `BookingWorkflow.process_result()` (95 lines) and `SessionProjector.project()` (43 lines) are pure delegation. Phase 2's intent is that logic migrates into these owners over time; that migration is incomplete.

### 7.5 `handle_message()` Still Contains Policy Eligibility Logic

The policy step eligibility check (lines 573–631) lives inside `handle_message()`, not inside a facade or policy module. This is the most substantive planning-adjacent logic remaining in the orchestrator. It reads `get_execution_steps()` directly, branches on `mode`, and decides `can_execute`. This belongs conceptually to `ActionRunner` or a future `ExecutionEligibilityChecker` but has not been migrated.

### 7.6 CONFIRM_APPOINTMENT datetime Injection in Orchestrator

Lines 799–842 of `handle_message()` inject `resolved_datetime_range` from session into `slots["datetime_range"]` before CONFIRM_APPOINTMENT execution. This is execution-preparation logic that lives in the orchestrator rather than in `BookingWorkflow`. It reads from `session_state` and, if missing, from `session_store.get_session()` (a second store read within the same turn). This is a coupling point that belongs inside `BookingWorkflow.confirm()`.

---

## 8. Entry Points

### Production Entry Points

| Entry Point | Protocol | File | Function |
|---|---|---|---|
| FastAPI HTTP | REST | `core/orchestration/api/main.py` | Mounts `message.router` at `/api`; endpoint at `POST /api/message` in `message.py:post_message()` |
| AWS Lambda | Event | `app.py:lambda_handler()` | GET → webhook verify; POST → `route_event()` from `router` module |
| CLI (manual test) | Interactive | `luma/cli/interactive.py` | Interactive prompt loop (not production traffic) |

### Internal Architectural Entry Points

| Layer | Entry | File | Function |
|---|---|---|---|
| Turn orchestration | `handle_message()` | `core/orchestration/orchestrator.py` | Owns execution dispatch; called by `message.py` and tests |
| Planning only | `plan_message()` | `core/orchestration/orchestrator.py` | Thin wrapper; calls `plan_turn(planning_only=True)` |
| NLU + merge + plan | `plan_turn()` | `core/planning/orchestration/turn_planner.py` | The canonical planning entry; NLU → merge → plan |
| NLU pipeline | `LumaPipeline.run()` | `luma/pipeline.py` | Called by `LumaClient`; Luma-internal only |
| Execution | `dispatcher.execute()` | `core/orchestration/execution/dispatcher.py` | Action-level; called by `ActionRunner.run()` |
| Session projection | `build_session_state_from_outcome()` | `core/session/persist.py` | Called by `SessionProjector.project()` |

### Extension Points

| Hook | Registry | Trigger |
|---|---|---|
| Intent workflow hooks | `core/routing/workflows/workflow.py:WorkflowRegistry` | `_invoke_workflow_after_execute()` in orchestrator (after execution, before return) |
| Capability runners | `extensions/capabilities/runner.py:CapabilityRunner` | `apply_capability_to_result()` in `message.py` after `handle_message()` returns |
| Intent handlers (RAG) | `extensions/handlers/runner.py:HandlerRunner` | `message.py` when `outcome.status=="HANDLER_DELEGATED"` |

---

*Generated from static analysis of the `nlu` branch. All line numbers verified by direct file reads. No tests executed.*
