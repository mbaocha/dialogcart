# ConversationEngine Target Architecture — Design Investigation

**Scope:** Design investigation only. No code changes. No test execution.  
**Branch:** `nlu`  
**Source evidence:** `summary.md` (production request path), `core/engine/conversation_engine.py`, `core/orchestration/orchestrator.py:handle_message()`, `core/orchestration/api/message.py:post_message()`

---

## 1. Current State

`ConversationEngine` exists at `core/engine/conversation_engine.py` and is currently a stub. It exposes two methods that do nothing but delegate to the existing orchestrator:

- `handle_turn(text, user_id, **kwargs)` → `handle_message(text, user_id, **kwargs)`
- `plan_turn(text, user_id, session_state, **kwargs)` → `plan_message(...)`

No caller in the production path uses it. The production path runs through `message.py:post_message()` → `orchestrator.py:handle_message()` directly.

The intention documented in the class docstring is already stated:

> Phase 2 will replace the delegation with direct calls to each component.

This investigation designs that target state.

---

## 2. Boundary Analysis — What Lives Where Today

The current responsibilities are distributed across two functions that each do too much:

### `handle_message()` currently owns (mixed concerns)

| Concern | Should it stay? |
|---|---|
| Instantiate Phase 2 facades per-call | Move to constructor injection |
| Load session from store (3 fallbacks) | Move to HTTP layer — redundant with message.py |
| Pagination short-circuit | Move into ConversationEngine |
| Policy eligibility check | Move into ConversationEngine |
| Client name → client instance mapping | Move into ConversationEngine |
| Pre-execution slot injection (org_id, SKU resolution, proposals, datetime injection) | Move into ConversationEngine |
| Execution dispatch via WorkflowRouter → ActionRunner | Move into ConversationEngine |
| Post-execution: BookingWorkflow.process_result | Move into ConversationEngine |
| Post-execution: AvailabilityWorkflow.process_search_result | Move into ConversationEngine |
| Rendering (ResponseRenderer) | Move into ConversationEngine |
| Decision trace setup and finalization | Move into ConversationEngine |
| Result construction | Move into ConversationEngine |

### `message.py:post_message()` currently owns (stays put)

| Concern | Stays outside engine |
|---|---|
| HTTP endpoint and request validation | Yes — HTTP boundary |
| Decision trace request binding | Yes — HTTP lifecycle |
| Session load from store | Yes — HTTP layer owns session filter |
| Session status filter (NEEDS_CLARIFICATION / AWAITING_CAPABILITY only) | Yes — policy applied at API layer |
| Capability boundary (CapabilityRunner) | Yes — extension boundary |
| Handler delegation (HandlerRunner + LLM rendering) | Yes — extension boundary |
| Session projection (SessionProjector) | Yes — post-turn persistence |
| Message history append (append_messages_turn) | Yes — post-turn persistence |
| Session persistence (save_session) | Yes — post-turn persistence |
| Response serialization (MessageResponse) | Yes — HTTP boundary |
| Extension bootstrap | Yes — process-level concern |

---

## 3. Proposed Architecture

### Responsibility Allocation

```
┌─────────────────────────────────────────────────────────┐
│  HTTP Layer  (message.py)                               │
│  ─ Session load + status filter                         │
│  ─ Capability boundary (CapabilityRunner)               │
│  ─ Handler delegation (HandlerRunner)                   │
│  ─ Session projection (SessionProjector)                │
│  ─ Persistence (save_session)                           │
│  ─ Response serialization                               │
└─────────────────────┬───────────────────────────────────┘
                      │ session_state (pre-filtered)
                      ▼
┌─────────────────────────────────────────────────────────┐
│  ConversationEngine  (core/engine/conversation_engine)  │
│  ─ Turn lifecycle: planning → execution → rendering     │
│  ─ Pagination short-circuit                             │
│  ─ Policy eligibility gate                              │
│  ─ Pre-execution slot preparation                       │
│  ─ Execution dispatch (WorkflowRouter → ActionRunner)   │
│  ─ Post-execution processing (availability + booking)   │
│  ─ Rendering                                            │
│  ─ Result construction                                  │
│  ─ Decision trace turn lifecycle                        │
└──────┬────────────────────────────────────────┬─────────┘
       │ plan(...)                              │ execute(...)
       ▼                                        ▼
┌──────────────┐                      ┌─────────────────────┐
│  Planner     │                      │  ActionRunner       │
│  plan_turn() │                      │  WorkflowRouter     │
└──────────────┘                      │  AvailabilityWorkflow│
                                      │  BookingWorkflow    │
                                      └─────────────────────┘
```

### ConversationEngine Is the Composition Root for Orchestration

`ConversationEngine` should be the composition root for the orchestration layer — not for the entire application. The HTTP layer (`message.py`) remains the application composition root. `ConversationEngine` owns the turn — from planning through rendering. Nothing outside the engine should reach inside the turn lifecycle.

The distinction matters: composition root for orchestration means all orchestration collaborators are wired at engine construction time, not at call time.

---

## 4. Recommended Constructor Dependencies

### Injected at construction time (from HTTP layer)

These are application-level singletons that the HTTP layer already owns at module level. They cross the session or API boundary and therefore cannot be created inside the engine without coupling.

```
ConversationEngine(
    planner:               TurnPlanner / plan_turn callable
    action_runner:         ActionRunner
    workflow_router:       WorkflowRouter
    availability_workflow: AvailabilityWorkflow
    booking_workflow:      BookingWorkflow
    renderer:              ResponseRenderer
    availability_client:   AvailabilityClient
    booking_client:        BookingClient
    session_store:         SessionStore          # for mid-turn _persist_to_session writes
    organization_client:   OrganizationClient    # for SKU resolution + org domain
    luma_client:           LumaClient            # for plan_turn NLU calls
)
```

**Rationale for each:**

- `availability_client` / `booking_client` — already module-level in `message.py`. These are the execution API adapters; injection enables test substitution without patching.
- `session_store` — required for mid-turn writes in `AvailabilityWorkflow.process_search_result()` via `_persist_to_session()`. Also required for the CONFIRM_APPOINTMENT datetime lookup (orchestrator line 811–828). The engine cannot write mid-turn without it.
- `organization_client` — required for SKU resolution (`load_sku_to_catalog_id_for_org`) and FINALIZE_RESERVATION payment verification. Already part of the planning pipeline.
- `luma_client` — currently created inside `plan_turn()` when None. Explicit injection makes the NLU backend substitutable at construction time, which is the correct level.
- `workflow_router`, `action_runner`, `availability_workflow`, `booking_workflow`, `renderer` — currently instantiated per-call inside `handle_message()`. Moving to constructor injection eliminates per-call allocation and makes the engine testable without patching.

### Created internally (turn-scoped)

These have turn lifetime, not engine lifetime. They should not be injected.

- `TurnTrace` / `TurnInvariantTrace` — thread-local, turn-scoped tracing context; initialized at `process_turn()` entry, finalized at return.
- `TransactionId` — generated inside the turn if not passed by caller.

### Optional / defaulted at construction

- `luma_client` — can default to `LumaClient()` if not supplied (preserves current behavior for callers that rely on the default).
- `organization_client` — can default to `OrganizationClient()`.

---

## 5. Recommended Public API

```python
class ConversationEngine:
    """Single orchestration entry point for a conversational turn."""

    def __init__(
        self,
        planner,              # TurnPlanner or callable
        action_runner,        # ActionRunner
        workflow_router,      # WorkflowRouter
        availability_workflow,  # AvailabilityWorkflow
        booking_workflow,     # BookingWorkflow
        renderer,             # ResponseRenderer
        availability_client,  # AvailabilityClient
        booking_client,       # BookingClient
        session_store,        # SessionStore
        organization_client=None,  # OrganizationClient (defaults internally)
        luma_client=None,     # LumaClient (defaults internally)
    ): ...

    def process_turn(
        self,
        user_id: str,
        text: str,
        session_state: Optional[dict] = None,   # pre-filtered by HTTP layer
        organization_id: Optional[int] = None,
        transaction_id: Optional[str] = None,   # for tracing
    ) -> dict:
        """
        Coordinate a complete turn: planning → execution → rendering.

        Does NOT:
        - Load or persist session state
        - Apply capability boundaries
        - Handle extension delegation (RAG, handlers)
        - Validate or parse HTTP requests

        Returns the same result dict that handle_message() currently returns:
        {"success": bool, "outcome": dict, "result": dict, "plan": dict,
         "_merged_luma_response": dict, "text": str, ...}
        """
        ...

    def plan_only(
        self,
        user_id: str,
        text: str,
        session_state: Optional[dict] = None,
        organization_id: Optional[int] = None,
    ) -> dict:
        """
        Return planning result only. No execution, no rendering.

        Equivalent to the current plan_message() / plan_turn().
        """
        ...
```

**Why `process_turn()` not `handle_turn()`:**  
The current `handle_turn()` name implies HTTP handling. `process_turn()` names what the engine actually does: coordinate a turn's orchestration lifecycle. The name also avoids confusion with the existing `handle_message()` during the transition period.

**Why `session_state` is a parameter, not loaded internally:**  
The HTTP layer applies a status filter (`NEEDS_CLARIFICATION` / `AWAITING_CAPABILITY`) before passing the session. That filter is an HTTP-layer policy decision. If the engine loaded session internally, it would need to know the filter rule — coupling it to the HTTP contract. The engine receives whatever session the HTTP layer decided to pass; it does not question it.

**What happens to the current `handle_message()` session FALLBACKs:**  
The three-tier session load in `handle_message()` (session_store → kwargs.session_state → get_session() fallback) exists because callers can provide session in different ways. In the target state:
- FALLBACK 1 (kwargs.session_state) — moves to `message.py` as an explicit parameter to `process_turn(session_state=...)`.
- FALLBACK 2 (get_session() direct) — the reconciliation-turn problem should be resolved at the HTTP layer: if the status filter would drop a session that is needed, the filter rule should be expanded, not bypassed inside the engine with a direct store read.

---

## 6. Responsibilities That Must Remain Outside the Engine

### HTTP Layer (`message.py`)

| Responsibility | Why it stays |
|---|---|
| Session load (`get_session`) | The engine does not own I/O |
| Session status filter | HTTP policy; the engine must not know the filter rule |
| Capability boundary (`CapabilityRunner`) | Extension boundary; fires after engine returns |
| Handler delegation (`HandlerRunner`) | Extension boundary; fires after engine returns for HANDLER_DELEGATED status |
| LLM rendering for handlers | Owned by the handler extension, not the core turn |
| Session projection (`SessionProjector`) | Post-turn persistence; fires after engine returns |
| Message history (`append_messages_turn`) | Post-turn persistence |
| `save_session` | Post-turn persistence |
| Response serialization (`MessageResponse`) | HTTP boundary |
| Extension bootstrap | Process-level concern |
| Transaction ID generation | Can be generated here or passed in |

The rule: anything that must happen before or after the turn lifecycle (I/O load, I/O save, extensions) stays outside.

### Planning Layer (`turn_planner.py`)

`plan_turn()` stays as-is. The engine calls it; it does not absorb it. `TurnPlanner` is a collaborator, not a part of the engine's body.

### Execution Layer (`dispatcher.py`, `ActionRunner`)

`ActionRunner` and `dispatcher.execute()` stay as-is. The engine coordinates them but does not own their logic.

---

## 7. Should `handle_message()` Become a Wrapper or Be Removed?

**Recommendation: wrapper first, removal later.**

`handle_message()` is called directly by a large number of tests (`core/tests/`). Removing it in one step would require updating every test. A wrapper preserves the existing test surface:

```
# During transition
def handle_message(text, user_id, ...):
    engine = ConversationEngine(...)   # built from injected args
    return engine.process_turn(
        user_id=user_id,
        text=text,
        session_state=kwargs.get("session_state"),
        organization_id=organization_id,
        transaction_id=kwargs.get("transaction_id"),
    )
```

Once the test suite has migrated to calling the engine directly (or via the HTTP layer), `handle_message()` can be removed. That removal is a separate PR with no architectural content — just test updates.

The key discipline: after Step 2 of the migration (below), `handle_message()` must contain **no business logic**. It is a signature adapter, nothing more.

---

## 8. Target Request Lifecycle

```
POST /api/message
│
├─ [HTTP Layer: message.py]
│   ├─ Parse MessageRequest
│   ├─ Bootstrap extensions (once)
│   ├─ TurnTrace.begin() if trace enabled
│   ├─ get_session(user_id)          ← raw session
│   ├─ Apply status filter           ← filtered_session (or None)
│   │
│   ├─ engine.process_turn(
│   │      user_id, text,
│   │      session_state=filtered_session,
│   │      organization_id, transaction_id
│   │  )
│   │   │
│   │   ├─ [ConversationEngine]
│   │   │   ├─ planner.plan_only(text, user_id, session_state)
│   │   │   │     └─ plan_turn() → LumaClient → merge → plan_builder → plan
│   │   │   │
│   │   │   ├─ if plan.status == HANDLER_DELEGATED → return early
│   │   │   │
│   │   │   ├─ availability_workflow.try_handle_browse_turn(plan, ...)
│   │   │   │   └─ None (not browse) → continue
│   │   │   │   └─ result (browse) → return early
│   │   │   │
│   │   │   ├─ eligibility_gate.check(plan, steps)
│   │   │   │   └─ can_execute=False → return build_planning_response_from_plan(plan)
│   │   │   │   └─ can_execute=True → continue
│   │   │   │
│   │   │   ├─ prepare_slots(plan, session_state)
│   │   │   │   ├─ inject organization_id
│   │   │   │   ├─ slots_for_availability_search() [if SEARCH_AVAILABILITY]
│   │   │   │   ├─ load_sku_to_catalog_id()
│   │   │   │   └─ inject resolved_datetime_range [if CONFIRM_APPOINTMENT]
│   │   │   │
│   │   │   ├─ route = workflow_router.get_route(client_name)
│   │   │   │
│   │   │   ├─ action_runner.run(plan, client)
│   │   │   │   └─ dispatcher.execute(plan, ...)
│   │   │   │
│   │   │   ├─ booking_workflow.process_result(execution_result, plan, slots, action)
│   │   │   │
│   │   │   ├─ if availability result:
│   │   │   │   availability_workflow.process_search_result(
│   │   │   │       execution_result, plan, slots, session_state, session_store, ...)
│   │   │   │
│   │   │   ├─ renderer.render_availability(result, decision, exec_result, session_state)
│   │   │   ├─ renderer.render_outcome(result, decision, exec_result)
│   │   │   │
│   │   │   └─ return TurnResult
│   │   │
│   ├─ apply_capability_to_result(result, capability_runner, ...)
│   │   └─ if capability needed → return early (AWAITING_CAPABILITY)
│   │
│   ├─ if HANDLER_DELEGATED:
│   │   ├─ handler_runner.handle(handler_name, context)
│   │   ├─ render_llm(...)
│   │   └─ save_session directly
│   │
│   ├─ [Session Persistence]
│   │   ├─ session_projector.project(outcome, status, merged_luma, prev_session, ...)
│   │   ├─ append_messages_turn(new_session_state, user_text, reply_text)
│   │   └─ save_session(user_id, new_session_state)
│   │
│   └─ return MessageResponse(success, outcome, text, traces)
```

---

## 9. Dependency Diagram

```
                 ┌───────────────────────┐
                 │   message.py          │
                 │   (HTTP Layer)        │
                 └──────────┬────────────┘
                            │ constructs + calls
                            ▼
                 ┌───────────────────────┐
                 │  ConversationEngine   │◄── constructor dependencies:
                 │  (Orchestration Root) │    TurnPlanner
                 └──┬────────────────────┘    ActionRunner
                    │                         WorkflowRouter
          ┌─────────┼──────────────┐          AvailabilityWorkflow
          │         │              │          BookingWorkflow
          ▼         ▼              ▼          ResponseRenderer
   ┌──────────┐  ┌──────────┐  ┌──────────┐  AvailabilityClient
   │ Planner  │  │Execution │  │ Renderer │  BookingClient
   │          │  │ Layer    │  │          │  SessionStore
   └──────────┘  └──────────┘  └──────────┘  OrganizationClient
        │              │              │       LumaClient
        ▼              ▼              ▼
   plan_turn()   WorkflowRouter  ResponseRenderer
                 ActionRunner    llm_renderer
                 dispatcher
                 AvailabilityWorkflow
                 BookingWorkflow

─ ─ ─ ─ ─ ─ ─  OUTSIDE ENGINE BOUNDARY ─ ─ ─ ─ ─ ─ ─

   ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
   │CapabilityRunner│  │ HandlerRunner  │  │SessionProjector│
   │(extensions/)  │  │(extensions/)   │  │(session/)     │
   └───────────────┘  └───────────────┘  └───────────────┘
```

Dependencies flow downward. `ConversationEngine` is the single point that orchestration callers reach for turn processing. Everything below it is a collaborator; everything above it is infrastructure.

---

## 10. Interaction Between Components

### Planning Phase (engine → planner)

The engine calls `planner.plan_only(user_id, text, session_state, organization_id)`. This is a pure planning call — no execution clients are involved. The planner calls Luma, merges session, runs the plan builder, and returns a plan dict. The engine does not look inside the planning implementation.

### Pagination Short-Circuit (engine → AvailabilityWorkflow)

Before any eligibility check, the engine calls `availability_workflow.try_handle_browse_turn(plan, session_state, session_store, user_id)`. If the result is not None, the engine returns it immediately. This is a complete-result fast path that bypasses all execution logic.

### Eligibility Gate (engine → policy)

The engine calls `get_execution_steps(intent_name)` and applies the `mode` + `required_slots` check. Currently this logic lives inline in `handle_message()`. In the target state it lives inside `process_turn()`. It is not a separate collaborator — it is internal engine logic that reads from the policy module. No new abstraction is required here.

### Execution Dispatch (engine → WorkflowRouter → ActionRunner → dispatcher)

The engine uses `workflow_router.get_route(client_name)` to select the domain, then calls `action_runner.run(plan, client=execution_client)`. The router maps the policy's `client` field to an execution domain; the engine maps the execution domain to the correct pre-injected client. The engine never constructs clients at dispatch time.

### Post-Execution Processing (engine → AvailabilityWorkflow / BookingWorkflow)

After execution:
1. `booking_workflow.process_result(execution_result, plan, slots, action)` — always called; no-op for non-booking actions.
2. If `execution_result["type"]=="availability"`: `availability_workflow.process_search_result(...)` — fingerprint, time resolution, presentation payloads, mid-turn `_persist_to_session()` writes.

### Rendering (engine → ResponseRenderer)

The engine calls `renderer.render_availability(result, decision, exec_result, session_state)` and `renderer.render_outcome(result, decision, exec_result)`. Rendering is best-effort; the engine must not fail the turn if rendering fails. This matches the existing behavior.

### Session Persistence (HTTP layer → SessionProjector)

The engine does not persist. After `process_turn()` returns, `message.py` calls `_session_projector.project(outcome, status, merged_luma, prev_session, user_id, session_store)`, then `append_messages_turn()`, then `save_session()`. The engine returns `_merged_luma_response` in the result dict so the HTTP layer can pass it to the projector (matches current behavior).

---

## 11. Smallest Migration Path

The migration is safe to execute incrementally. Each step is independently deployable and leaves tests passing.

### Step 1 — Constructor Dependencies (no behavior change)

Update `ConversationEngine.__init__` to accept all collaborators as constructor arguments. Store them. `handle_turn()` still delegates to `handle_message()` — no behavior changes.

**What changes:** `ConversationEngine.__init__` gains parameters.  
**What does not change:** `handle_message()`, `message.py`, all tests.  
**Risk:** None — the engine is not called in production.

---

### Step 2 — Move Execution Body Into Engine (the core migration)

Move the body of `handle_message()` into `ConversationEngine.process_turn()`, replacing locally-instantiated facades with `self.*` references. Redirect `handle_message()` to construct a `ConversationEngine` and call `process_turn()`.

```
def handle_message(text, user_id, availability_client, booking_client,
                   session_store, organization_id, ...):
    engine = ConversationEngine(
        planner=PlannerAdapter(),
        action_runner=ActionRunner(),
        workflow_router=WorkflowRouter(),
        availability_workflow=AvailabilityWorkflow(),
        booking_workflow=BookingWorkflow(),
        renderer=ResponseRenderer(),
        availability_client=availability_client,
        booking_client=booking_client,
        session_store=session_store,
        ...
    )
    return engine.process_turn(
        user_id=user_id, text=text,
        session_state=kwargs.get("session_state"),
        organization_id=organization_id,
        transaction_id=kwargs.get("transaction_id"),
    )
```

**What changes:** The business logic moves to `process_turn()`. `handle_message()` becomes a pure constructor-and-delegate wrapper.  
**What does not change:** All callers of `handle_message()` — including `message.py` and all tests — continue to work unmodified.  
**Risk:** Low. This is a move, not a rewrite. Line-for-line equivalence is verifiable by diff.

Note on session loading: At this step, `process_turn()` should NOT reproduce the three-tier session load from `handle_message()`. Instead, `process_turn()` accepts `session_state` as a parameter. The new `handle_message()` wrapper is responsible for resolving session before calling `process_turn()`. This keeps the session loading concern in one place (the wrapper) rather than duplicated in the engine.

---

### Step 3 — `message.py` Calls Engine Directly

Construct `ConversationEngine` at module level in `message.py` alongside the existing module-level clients. Change `post_message()` to call `_engine.process_turn(...)` instead of `handle_message(...)`.

```
# module level
_engine = ConversationEngine(
    planner=PlannerAdapter(),
    action_runner=ActionRunner(),
    workflow_router=WorkflowRouter(),
    availability_workflow=AvailabilityWorkflow(),
    booking_workflow=BookingWorkflow(),
    renderer=ResponseRenderer(),
    availability_client=_availability_client,
    booking_client=_booking_client,
    session_store=_session_store,
    ...
)

# in post_message():
result = _engine.process_turn(
    user_id=request.user_id,
    text=request.text,
    session_state=session_state,  # already filtered
    organization_id=request.organization_id,
    transaction_id=transaction_id,
)
```

**What changes:** `message.py` no longer calls `handle_message()`. The production path now goes directly through `ConversationEngine`.  
**What does not change:** `handle_message()` remains for test backward compatibility. All tests still pass.  
**Risk:** Low. Equivalent to Step 2 with the construction moved from inside `handle_message()` to module level. One integration test targeting `post_message()` validates end-to-end equivalence.

---

### Step 4 — Remove Redundant Session Loading

At this point, `handle_message()` still contains the three-tier session load that fires before calling `process_turn()`. This load is redundant with `message.py`'s load and only matters for direct callers of `handle_message()` (tests).

Resolve the FALLBACK 2 concern: if any test relies on the engine loading session from the default store, that test should be updated to pass `session_state` explicitly. The default-store fallback is a `message.py` concern (the HTTP layer controls when sessions are loaded), not an engine concern.

After tests are updated, remove the session loading from `handle_message()`.

**What changes:** `handle_message()` is now fully stateless: constructor + delegate. No I/O.  
**Risk:** Low. Only affects tests that relied on implicit session loading. These tests are more correctly written with explicit session_state.

---

### Step 5 — Remove `handle_message()` (future, separate PR)

After all test callers have migrated to calling `ConversationEngine.process_turn()` directly (or via the HTTP layer), `handle_message()` can be deleted. This is a cleanup PR with no architectural content.

**Criteria for when this step is safe:**
- No test imports or calls `handle_message()` directly.
- No external caller (Lambda router, etc.) calls `handle_message()`.
- All integration tests pass against the `message.py` → `ConversationEngine` path.

---

## 12. Architectural Decisions Summary

### Decision 1: ConversationEngine is the orchestration composition root, not the application composition root.

`message.py` remains the application composition root. It constructs `ConversationEngine` and wires all collaborators. The engine is not responsible for constructing its own dependencies from configuration.

**Why:** This preserves the existing dependency injection model. The HTTP layer already constructs clients at module level; moving that to the engine would require the engine to know about configuration, environment variables, and client initialization logic — coupling that belongs in infrastructure.

### Decision 2: Session loading and persistence stay outside the engine.

The engine receives `session_state` as a parameter and returns a result dict. It never calls `get_session()` or `save_session()`.

**Why:** Session loading involves a status filter that is an HTTP-layer policy. Session persistence involves calling `build_session_state_from_outcome()` which reads from `merged_luma_response` — a post-turn concern. Neither belongs inside the turn lifecycle.

### Decision 3: Capability and handler extension boundaries stay in the HTTP layer.

`CapabilityRunner` and `HandlerRunner` fire on the result of `process_turn()`. They are not injected into the engine.

**Why:** These are extension boundaries that inspect the engine's output and potentially replace it. They must be applied after the engine has completed, not inside the engine's turn flow. Injecting them into the engine would give extensions the ability to intercept turn-internal state, violating the engine boundary.

### Decision 4: Policy eligibility logic stays internal to the engine, not extracted to a separate collaborator.

The `can_execute` check (reading policy steps, checking mode and required slots) lives inside `process_turn()`. It is not extracted to a new `ExecutionEligibilityGate` class.

**Why:** The check is simple (one loop, two branches) and has no state. Adding an abstraction here would add indirection without reducing coupling. The policy module (`intent_policy.py`) is already the collaborator; the engine reads from it directly.

### Decision 5: `handle_message()` becomes a compatibility wrapper, not a permanent fixture.

It is a shim for the migration period. Its removal is planned but sequenced after test migration.

**Why:** Removing it immediately would require updating all direct test callers in one PR. That's a large change with no architectural value. The shim costs nothing at runtime; it should be removed once tests are updated, not preserved indefinitely.

### Decision 6: No new abstractions are introduced for the migration.

The migration moves existing logic into `ConversationEngine.process_turn()` without rewriting or decomposing it further. The eligibility gate is not extracted. The pre-execution slot preparation is not extracted. The result construction is not extracted.

**Why:** The goal is to establish the correct ownership boundary (engine owns the turn lifecycle), not to refactor the turn lifecycle itself. Doing both simultaneously increases risk without additional benefit. Further decomposition is a separate, future concern.

---

## 13. Open Questions (Not Resolved in This Investigation)

These are deferred deliberately. They should not be addressed in the migration PRs.

1. **CONFIRM_APPOINTMENT datetime injection** (orchestrator lines 799–842) injects `resolved_datetime_range` from session into slots before execution. In the target state this lives in `process_turn()`. The better long-term home is `BookingWorkflow.confirm()`, but that move should be a separate PR after the engine migration is stable.

2. **FALLBACK 2 reconciliation-turn problem** — the three-tier session load exists because some reconciliation turns have their session filtered out by the status check. The correct resolution is to expand the status filter in `message.py` to include those cases, not to bypass the filter inside the engine. That change requires understanding which reconciliation turn states are affected, which is a separate investigation.

3. **`LumaClient` injection vs default** — currently `plan_turn()` creates a `LumaClient()` if none is injected. In the target state, `ConversationEngine` holds the client. The question of whether `LumaClient` should always be required (no default) is a separate decision.

4. **Decision trace request binding** — the `is_request_decision_trace_bound()` flag separates API-bound traces from direct `handle_message()` caller traces. After the migration, all production calls go through the engine from `message.py`. The direct-caller path becomes test-only. This simplifies the trace ownership model but the change should happen after Step 3.

---

*Design investigation only. All statements about file contents verified by direct reads. No code was modified. No tests were executed.*
