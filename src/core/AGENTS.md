# Core — Architectural Constitution

Extends the root [Architectural Constitution](../../AGENTS.md). Core-specific ownership and constraints—not implementation details.

---

## Scope

Orchestration between NLU, Capabilities, and the user. Applies only within Core; cross-system boundaries are in the root constitution.

The production owner of each conversational turn within Core is **ConversationEngine** (`process_turn`). HTTP and compat shims prepare context and persist results; they do not own the turn pipeline.

---

## Turn orchestration

- Durable orchestration stages for a turn are **Planning → Execution → Rendering**.
- The engine sequences those stages. It must not re-implement planning, booking dispatch, or domain post-processing owned elsewhere.
- **Planning** produces the plan (intent, status, stage, action, slots, missing slots, and related planning artifacts).
- **Execution** decides whether a policy-selected step runs and, when it does, performs coordination through to pre-render business outcomes.
- **Rendering** turns plan and execution artifacts into the user-facing response.
- Planning failure, handler delegation, browse/pagination, and clarification-without-tool-run are **control-flow branches**, not peer orchestration stages.
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
- Committing steps require confirmation when policy and workflow state demand it.
- While `pending`, each turn is classified once (accept, reject, revise, none); downstream branches on that decision—not re-derived.
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
- `last_execution_result` is the authoritative cached search until invalidated.
- `presented_availability` is the only selectable availability set.
- Presentation is a view over cached search; it does not own booking truth.
- Browsing availability must **never** execute `SEARCH_AVAILABILITY`.
- Only search-parameter changes may invalidate cache and require a new search.
- Search parameters: service, date, duration, resource, location.
- Presentation state must not modify booking slots, proposals, fingerprints, or other durable state.
- Pagination is presentation state only.
- NLU emits `AVAILABILITY` with `operation: browse_next | browse_previous | null`.
- Core consumes structured `operation` only—not raw user text for browse direction.
- NLU classifies intent and operation; Core owns the execution decision.
- Core may reuse cache, paginate, or execute `SEARCH_AVAILABILITY`.
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
- Policy owns business sequencing via `intent_policy.yaml` and `requires`.
- Planner interprets policy only—planner code is intent-agnostic.
- New booking behaviour: extend the fact registry → express sequencing in policy → generic interpreter selects the step.
- `plan.action` is execution-only; `null` when no execution step runs.
- Conversation phase must never be inferred from `plan.action`.
- No `presentation_action`.
- Presentation uses `status`, `stage`, `awaiting`, `missing_slots`, and execution artifacts.
- Awaiting confirmation (no commit): `status=AWAITING_CONFIRMATION`, `stage=CONFIRM`, `awaiting=USER_CONFIRMATION`, `action=null`.
- After user confirmation, policy may select a commit step (e.g. `CONFIRM_APPOINTMENT`).
- Eligibility inputs: business facts. Sequencing: policy + generic selector. Fact computation: runtime fact registry. Conversation phase/UI: presentation fields. Slot completeness and safety checks belong to planning infrastructure—not sequencing. Whether and how `plan.action` runs belongs to **execution coordination**—not the planner.

---

## Intent policy

`src/core/config/intent_policy.yaml` is the single source of truth for planning rules, slot requirements, execution sequencing, step modes (exploratory/committing), and intent durability. Code reads policy and derives facts—it must not invent behaviour or sequencing outside policy.

---

## Booking execution

- Policy selects the execution step (`plan.action`); it may be `null` when nothing should run.
- Execution coordination owns eligibility, preparation, client binding, dispatch, and workflow post-hooks needed before rendering.
- Execution clients perform the operation and return business outcomes; they do not decide the next user action.
- Rendering produces the user-facing response from plan and execution artifacts.
- HTTP and session infrastructure own persistence of durable state for the following turn—not the turn orchestrator.

---

## Capabilities

Core activates capabilities, merges durable outcomes into session. Capabilities do not own session state, planner logic, or conversation flow.

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
