# Core — Architectural Constitution

Extends the root [Architectural Constitution](../../AGENTS.md). Core-specific ownership and constraints—not implementation details.

---

## Scope

Orchestration between NLU, Capabilities, and the user. Applies only within Core; cross-system boundaries are in the root constitution.

The production owner of each conversational turn within Core is **ConversationEngine** (`process_turn`). HTTP and compat shims prepare context and persist results; they do not own the turn pipeline.

---

## Turn orchestration

- The primary durable orchestration lifecycle for a turn is **Planning → Execution → Rendering**.
- The engine sequences those stages. It must not re-implement planning, booking dispatch, or domain post-processing owned elsewhere.
- **Planning** produces the plan (intent, status, stage, action, slots, missing slots, and related planning artifacts).
- **Execution** decides whether a policy-selected step runs and, when it does, performs coordination through to pre-render business outcomes.
- **Rendering** produces the user-facing response from plan, execution, and other supplied evidence (see Rendering).
- Planning failure, handler delegation, browse/pagination, and clarification-without-tool-run are **control-flow branches**, not peer orchestration stages.
- Rendering may also be invoked from those branches when a user-facing reply is required; that does not create additional durable stages.
- Browse/pagination remains presentation routing after planning; it must not become a fourth durable stage.
- Non-execute and clarification response shapes belong with outcome builders—not ad hoc in the turn owner.
- Grow turn-lifecycle behaviour in the engine and its execution-coordination boundary—not in orchestration compat shims.
- Observability (Decision Trace, invariant stage checks) is **orthogonal** to business logic. Tracing must not dictate business return types or orchestration APIs; emitters wrap seams owned by the subsystem that makes the decision.

---

## Session

- Core session is the **single owner of persistent booking state** (see root Ownership).
- Transient state during merge, planning, or execution is a processing view—not a competing source of truth.
- Core merges each NLU response with persisted session before planning or execution.
- NLU produces per-turn deltas; Core retains, promotes, or discards.
- Merge applies when a durable booking flow is active—not conversation status alone.
- Session intent is immutable within a turn unless explicitly reset.
- `missing_slots` are derived from intent policy and effective slots, computed once per turn.

### Session schema V2

- Persisted sessions use ``schema_version: 2`` with nested sections: ``conversation``, ``planning``, ``booking``, ``availability``, ``confirmation_state``, ``capability``.
- V1 sessions are normalized on load; legacy top-level mirrors are hydrated in memory for existing consumers.
- ``core.session.session_schema_v2`` owns shape, validation, and migration only—not planning or execution decisions.
- **Planning** owns ``planning.*``, including ``planning.slots`` and ``planning.bound_datetime``.
- **Execution** results are ephemeral. The projector persists only durable committed identifiers and availability artifacts.
- **Booking** contains only successfully committed ``booking_id`` and ``booking_code`` values.
- **SessionProjectorV2** owns durable turn-end writes (no direct storage I/O).
- **Confirmation gate** owns ``confirmation_state``.
- **API** owns persisted ``conversation.history``; NLU conversation continuation data lives in ``conversation.memory``.
- **Capabilities** produce ``capability.active`` and minimal ``capability.results`` continuation facts.
- **Rendering** and **NLU** do not own persisted session fields.
- The raw NLU ``facts`` bag is not persisted in V2; only explicitly mapped continuation keys (e.g. ``payment_satisfied``) live under ``capability.results``.

---

## Proposals and durable slots

- NLU may surface proposals; Core decides what becomes **durable** session slots.
- Temporal proposals are not durable until bound to presented availability or explicitly confirmed.
- Only durable slots participate in persistence and planning completeness.
- NLU must not fabricate booking slots absent from the current utterance.
- Core carries durable state across turns.

---

## Confirmation

- Core owns explicit user approval before irreversible booking actions.
- `confirmation_state` (`pending`, `confirmed`, cleared) is managed via the confirmation gate.
- `session.confirmation_state` is the canonical authorization field; it is not nested booking state.
- Committing steps require confirmation when policy and workflow state demand it.
- While `pending`, each turn is classified once as `YES`, `NO`, or `ANOTHER_REQUEST`; downstream branches on that decision—not re-derived. The gate does not interpret the request that supersedes confirmation.
- On `ANOTHER_REQUEST`, the gate consumes only confirmation authorization. Planning owns all resulting booking-slot and availability invalidation.
- `confirmation_state` exists only to authorize a pending irreversible operation; it is not durable booking truth.
- Lifecycle: `pending` → confirmation required; `confirmed` → transient authorization for the current commit only; cleared → no active workflow.
- A successful commit **consumes** `confirmation_state`; it must not remain after completion.
- `booking_id` is the authoritative post-commit marker; when present, confirmation must not re-run for the same booking.
- Durable booking truth is persisted identifiers and execution results—not `confirmation_state`.

---

## Availability

- Core owns when an availability outcome is **trusted** for the current booking parameters.
- Core owns availability presentation to the user.
- Trust is established via slot fingerprints, bound datetimes, or equivalent session evidence.
- A prior search is not trusted after booking parameters change.
- Parameter changes require re-established availability before committing steps proceed.
- `availability_ready` reflects trust at planning time.
- `AvailabilityCache` is the authoritative trusted search result until invalidated. It is currently persisted using the legacy session field `last_execution_result`; that storage key is not part of the domain contract.
- `PresentedAvailability` is the current discovery/disambiguation window shown to the user.
- Domain modules read cache and presentation via the availability presentation session adapter—not by storage-field name.
- Slot selection is hybrid: ambiguous or presentation-anchored choices resolve against the current presented availability only (never cache fallthrough); explicit complete current-turn choices may resolve against the trusted availability cache when they uniquely identify one offer; slots absent from that cache must not bind.
- Current-turn explicit date/time provenance (`_current_turn_has_date` / `_current_turn_has_time`) distinguishes utterance facts from carried session proposals; session date alone must not activate cache selection.
- Browse exhaustion preserves the last successful `PresentedAvailability` as the ambiguous-selection window.
- Presentation is a view over cached search used for discovery and disambiguation; it does not own booking truth.
- The availability renderer formats prepared `PresentedAvailability` only; it does not derive presentation windows from raw slots.
- Browsing availability must **never** execute `SEARCH_AVAILABILITY`.
- Only search-parameter changes may invalidate cache and require a new search.
- Search parameters: service, date, date range, duration, resource, location, and other availability criteria.
- **SEARCH_AVAILABILITY** owns all search constraints. **Browse** owns only page-cursor movement inside the presentation result set shaped by those criteria.
- Single-day search criteria shape the presentation result set to that day even when the provider returns surplus dates; pagination must not spill into off-criteria days.
- Explicit multi-day / exploratory criteria may retain multiple dates in the presentation result set; pagination may traverse that criteria-shaped set.
- Presentation state must not modify booking slots, proposals, fingerprints, or other durable state.
- Pagination is presentation state only.
- NLU emits `AVAILABILITY` with `operation: browse_next | browse_previous | null`.
- Browse aliases are deliberately small (`next` / `show more` / `more`, `previous` / `show previous` / `back`). Date phrases (`next day`, `previous day`, absolute dates) are SEARCH semantics — never Browse.
- Core owns the browse execution decision; browsing never executes `SEARCH_AVAILABILITY`.
- Core may reuse cache, paginate presentation, or execute `SEARCH_AVAILABILITY`.
- NLU never instructs Core to search.

Reference: [`AVAILABILITY_INTERACTION_CONTRACT.md`](orchestration/contracts/AVAILABILITY_INTERACTION_CONTRACT.md)

---

## Booking invariants

- Session merge is additive by default.
- Durable booking slots are preserved unless an explicit invalidation rule applies.
- Explicit state removal must go through the invalidation registry.

---

## Planner

- Runtime derives business facts from session, slots, fingerprints, confirmation gates, and workflow state.
- Booking sequencing, slot requirements, and execution-step selection remain **policy-driven** and reusable across intents.
- Planner infrastructure must stay generic: new booking behaviour extends the fact registry, expresses sequencing in policy, and lets the generic interpreter select the step.
- Intent classification and some routing concerns (for example handler delegation) may exist **outside** booking sequencing; they must not reinvent booking step selection.
- `plan.action` is execution-only; `null` when no execution step runs.
- Conversation phase must never be inferred from `plan.action`.
- No `presentation_action`.
- Presentation uses `status`, `stage`, `awaiting`, `missing_slots`, and execution artifacts.
- Awaiting confirmation (no commit): `status=AWAITING_CONFIRMATION`, `stage=CONFIRM`, `awaiting=USER_CONFIRMATION`, `action=null`.
- After user confirmation, policy may select a commit step (e.g. `CONFIRM_APPOINTMENT`).
- Eligibility inputs: business facts. Sequencing: policy + generic selector. Fact computation: runtime fact registry. Conversation phase/UI: presentation fields. Slot completeness and safety checks belong to planning infrastructure—not sequencing. Whether and how `plan.action` runs belongs to **execution coordination**—not the planner.

### Planning ownership model (Phase 1 — boundaries only)

Target inputs: **Workflow**, **Current Request**, **Conversation Context** (previous Decision outputs). Evidence producers feed a pure **Relationship Evaluator**; **Decision** is the intended aggregate root for action/stage/status/next context.

```text
CurrentRequest  →  Attach  →  AttachedRequest
     (NLU)      Stage 2    (planning_intent, turn_operation, …)
```

Numbered Stage 01–09 modules expose the unchanged runtime order. Macro-phase classification: [`planning/pipeline/MACRO_PHASES.md`](planning/pipeline/MACRO_PHASES.md). Phase 5 completes Decision ownership: `decide()` selects planning-turn outcomes; `finalize_decision_after_time_resolution()` owns post-execution / time-match finalization. `nlu_failure_fallback` remains a planner admission boundary (pre-Attach). Decision Trace records CurrentRequest, AttachedRequest, DecisionInput, Decision, and DecisionFinalization.

---

## Intent policy

Booking sequencing, required slots, execution steps, step modes (exploratory/committing), and intent durability are **policy-driven**. Code reads that policy and derives facts—it must not invent booking behaviour or execution sequencing outside policy. Other routing concerns (for example handler delegation) may use separate declarative policy; they do not replace booking execution policy.

---

## Booking execution

- Policy selects the execution step (`plan.action`); it may be `null` when nothing should run.
- Execution coordination owns eligibility, preparation, client binding, dispatch, and workflow post-hooks needed before rendering.
- Execution clients perform the operation and return business outcomes; they do not decide the next user action.
- Rendering produces the user-facing response from plan, execution, and other supplied evidence.
- HTTP and session infrastructure own persistence of durable state for the following turn—not the turn orchestrator.

---

## Capabilities

- Planning decides whether a capability must activate before booking can proceed.
- Execution and API boundaries coordinate capability execution once planning has selected it.
- Capabilities return structured results; Core merges durable continuation facts into session.
- Capabilities do not own session state, planner logic, or conversation flow.
- Rendering remains responsible for conversational output from capability outcomes and other supplied evidence.

---

## Handler delegation

- Some intents are delegated rather than booked through the durable Planning → Execution path.
- Delegated handlers may coordinate supporting capabilities to produce **structured evidence** for the turn.
- Handlers own coordination of that supporting work; they do **not** own final user-facing wording.
- Rendering remains responsible for producing the final conversational response from handler evidence and instructions.
- Delegated conversational requests may generate evidence independently of booking execution.
- Durable booking state remains preserved across delegated conversational handling unless an explicit invalidation rule applies.

---

## Rendering

- Rendering owns conversational wording, conversational composition, and presentation of supplied evidence.
- Rendering does **not** own business facts, world knowledge, planning, or orchestration.
- Rendering consumes evidence produced elsewhere (planning artifacts, execution outcomes, handlers, capabilities).
- Rendering must not invent facts outside the evidence it is given.

---

## Evidence → Rendering

- Producing components (planning, execution, handlers, capabilities, and related evidence adapters) generate structured evidence.
- Rendering consumes that evidence and turns it into the user-facing reply.
- Rendering must not invent business facts, world knowledge, or other claims absent from supplied evidence.
- Evidence producers must not emit final conversational wording as a substitute for Rendering.

---

## Implementation discipline

### Incremental implementation

- Architecture evolves through small, reviewable changes.
- Each PR should have one clearly defined architectural objective.
- Implement only the agreed scope for the current PR.
- Do not implement future phases ahead of the agreed roadmap.
- If a better design is discovered, report it separately rather than implementing it.

### Minimal change principle

- Prefer the smallest change that satisfies the current architectural objective.
- Do not refactor unrelated code while implementing a feature.
- Do not introduce abstractions or extension points until they are required.
- Avoid speculative improvements and premature generalization.

### Preserve architectural ownership

- Respect existing ownership boundaries documented in this constitution and the root [Architectural Constitution](../../AGENTS.md).
- Do not move responsibilities between components unless the current task explicitly changes the architecture.
- Architectural improvements outside the requested scope should be proposed, not implemented.

### Reporting

- **Work completed** — what was implemented for the current objective
- **Architectural observations** — relevant findings that do not require immediate action
- **Recommended future improvements** — proposed follow-ups for separate approval

Future improvements should never be implemented without explicit approval.

---

## Decision Trace

Decision Trace is the **primary debugging tool** for orchestration behaviour. Reference: [`tracing/DECISION_TRACE.md`](tracing/DECISION_TRACE.md).

### When changing orchestration

- **New decisions** — emit Decision Trace records (`emit_evidence`, `decide`, `emit_mutation`) from the owning subsystem. Use stable node ids (e.g. `decision.planner.select_action`).
- **New business rules** — add stable `reason_code`s to `tracing/reason_codes.py` and human `reason_text` on every decision. Rejected candidates must explain what blocked each alternative.
- **New E2E scenarios** — where behaviour hinges on routing (pagination, binding, confirmation, execution eligibility), assert on `decision_trace` `reason_code`s or key decision nodes when tracing is enabled.

### What not to do

- Do not add bracket-prefixed `logger.error` instrumentation for decision debugging; use Decision Trace emitters.
- Do not extend the trace framework proactively. Record operational gaps first; improve only when a genuine blind spot is confirmed.
- Do not implement deferred rollout phases (invariant bridge, dual-emit removal) without explicit approval driven by real debugging pain.

### Enable for investigation

```bash
export DIALOGCART_TRACE_DECISIONS=1
# or: pytest --trace-decisions
# or: python -m core.tracing.decision_trace_cli saved_response.json
```
