# Core — Architectural Constitution

Extends the root [Architectural Constitution](../../AGENTS.md). Core-specific ownership and constraints—not implementation details.

---

## Scope

Orchestration between NLU, Capabilities, and the user. Applies only within Core; cross-system boundaries are in the root constitution.

The production owner of each conversational turn within Core is **ConversationEngine** (`process_turn`). HTTP and compat entrypoints prepare context and persist results; they do not own the turn pipeline.

---

## Architectural conflict policy

Before implementing a requested change, compare it with the code structure, responsibilities, and ownership boundaries defined in this instruction file and any applicable parent or nested `AGENTS.md` files.

If the requested implementation would conflict with those boundaries:

1. Stop before modifying files.
2. Identify the specific conflicting instruction.
3. Explain how the proposed implementation violates the defined code structure, responsibility, or ownership boundary.
4. Recommend an architecturally compliant alternative.
5. Wait for explicit user direction before proceeding.

Do not silently reinterpret, weaken, bypass, or override an architectural rule merely to complete an implementation.

If the conflict is uncertain rather than definite, report the concern and inspect the relevant architecture before deciding whether implementation can safely proceed.

This stop requirement applies to implementation work. Investigation, review, and diagnosis may continue in read-only form so that existing or proposed architectural conflicts can be identified and explained.

---

## Turn orchestration

- The primary durable orchestration lifecycle for a turn is **Planning → Execution → Rendering**.
- The engine sequences those phases. It must not re-implement planning, booking dispatch, or domain post-processing owned elsewhere.
- **Planning** produces the plan (intent, status, stage, action, slots, missing slots, and related planning artifacts).
- **Execution** decides whether a policy-selected step runs and, when it does, performs coordination through to pre-render business outcomes.
- **Rendering** produces the user-facing response from plan, execution, and other supplied evidence (see Rendering).
- Planning failure, handler delegation, browse/pagination, and clarification-without-tool-run are **control-flow branches**, not peer orchestration stages.
- Rendering may also be invoked from those branches when a user-facing reply is required; that does not create additional durable stages.
- Browse/pagination remains presentation routing after planning; it must not become a fourth durable stage.
- Non-execute and clarification response shapes belong with outcome builders—not ad hoc in the turn owner.
- Grow turn-lifecycle behaviour in the engine and its execution-coordination boundary—not in orchestration compat shims.
- Observability (Decision Trace, invariant checks) is **orthogonal** to business logic. Tracing must not dictate business return types or orchestration APIs; emitters wrap seams owned by the subsystem that makes the decision.

---

## Session

- Core session is the **single owner of all persistent DialogCart conversation and booking-workflow state** (see root Ownership).
- External systems remain authoritative for their own business records. Core session retains only the identifiers, outcomes, and continuation state needed to conduct future DialogCart turns.
- **Session V2 is the canonical runtime and persisted session model.** Nested sections are the source of truth for durable booking conversation state.
- Transient state during merge, planning, or execution is a processing view—not a competing source of truth.
- Core merges each NLU response with persisted session before planning or execution.
- NLU produces per-turn deltas; Core retains, promotes, or discards.
- Merge applies when a durable booking flow is active—not conversation status alone.
- Session intent is immutable within a turn unless explicitly reset.
- `missing_slots` are derived from intent policy and effective slots, computed once per turn.

### Session schema V2

- Sessions use nested sections: ``conversation``, ``planning``, ``booking``, ``availability``, ``confirmation_state``, ``capability``.
- Session schema modules own shape and validation only—not planning or execution decisions.
- Historical persisted documents may be normalized into Session V2 on load; that load path is not a second runtime model.
- **Planning** owns ``planning.*``, including ``planning.slots`` and ``planning.bound_datetime``.
- **Execution** results are ephemeral. The projector persists only durable committed identifiers and availability artifacts.
- **Booking** contains only successfully committed ``booking_id`` and ``booking_code`` values.
- **SessionProjectorV2** computes and applies the durable turn-end session projection in memory; it does not perform storage I/O.
- **Session infrastructure** owns storage I/O for loading and persisting the projected session. HTTP and compatibility entrypoints may invoke that infrastructure but do not own projection rules.
- **Confirmation gate** owns ``confirmation_state``.
- **API** owns persisted ``conversation.history``; NLU conversation continuation data lives in ``conversation.memory``.
- **Capabilities** produce ``capability.active`` and minimal ``capability.results`` continuation facts.
- **Rendering** and **NLU** do not own persisted session fields.
- The raw NLU ``facts`` bag is not persisted in V2; only explicitly mapped continuation keys (e.g. ``payment_satisfied``) live under ``capability.results``.

---

## Proposals and durable slots

- NLU determines whether the current utterance accepts, rejects, or modifies a proposal and identifies the semantic target. Core must not derive that meaning from raw text.
- Core verifies that the referenced proposal is pending and valid, then promotes or rejects it according to policy; Core decides what becomes **durable** session slots.
- Temporal proposals are not durable until bound to presented availability or explicitly confirmed.
- Only durable slots participate in persistence and planning completeness.
- NLU must not fabricate booking slots absent from the current utterance.
- Core carries durable state across turns.

---

## Confirmation

- NLU interprets the current utterance and supplies structured dialogue evidence such as `ACCEPT_CONFIRMATION`, `REJECT_CONFIRMATION`, or `ANOTHER_REQUEST`.
- Core owns the confirmation gate before irreversible booking actions: it validates whether confirmation is pending and applies the lifecycle consequence of NLU evidence.
- `confirmation_state` (`pending`, `confirmed`, cleared) is managed via the confirmation gate.
- `session.confirmation_state` is the canonical authorization field; it is not nested booking state.
- Committing steps require confirmation when policy and workflow state demand it.
- While `pending`, Core consumes NLU's classification once; downstream branches on that evidence—not a re-derived raw-text classification. The gate does not interpret the user's words or the request that supersedes confirmation.
- The **confirmation gate** owns confirmation lifecycle evaluation, not language interpretation. It must not recreate a missing NLU classification from raw text.
- If confirmation evidence is missing or insufficient, Core may conservatively clarify while preserving safe lifecycle state. It must never silently reconstruct acceptance, rejection, or another request.
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
- `AvailabilityCache` is the authoritative trusted search result until invalidated. It lives under Session V2 ``availability``; storage layout is not part of the domain contract.
- `PresentedAvailability` is the current discovery/disambiguation window shown to the user.
- Domain modules read cache and presentation via canonical availability session accessors—not by inventing alternate session shapes.
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
- NLU alone interprets whether phrasing means browse-next, browse-previous, a date/search request, an option reference, or another dialogue act. Core consumes the structured `operation`; it must not maintain browse aliases or inspect raw text to recover one.
- Core owns the browse execution decision; browsing never executes `SEARCH_AVAILABILITY`.
- Core may reuse cache, paginate presentation, or execute `SEARCH_AVAILABILITY`.
- NLU never instructs Core to search.

Reference: [`AVAILABILITY_INTERACTION_CONTRACT.md`](orchestration/contracts/AVAILABILITY_INTERACTION_CONTRACT.md)

---

## Booking invariants

- Session merge is additive by default.
- Durable booking slots are preserved unless an explicit invalidation rule applies.
- Explicit state removal must go through the invalidation registry, applied via the planning mutation boundary.

---

## Planner

- Runtime derives business facts from session, slots, fingerprints, confirmation gates, and workflow state.
- Runtime consumes structured NLU evidence for the current utterance. It must not inspect raw user text to infer semantic facts or transitions.
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

### Planning ownership model

**Decision is the single owner of planning-turn outcomes** (action, status, stage, awaiting, and related plan fields). Evidence producers do not select outcomes. Post-execution / time-match completion of Decision is owned by Decision finalization—not by ad hoc writers.

Target inputs: **Workflow**, **Current Request**, **Conversation Context** (previous Decision outputs). Evidence producers feed a pure **Relationship Evaluator**; Decision selects planning outcomes from that evidence.

```text
CurrentRequest  →  Attach  →  AttachedRequest
     (NLU)                 (planning_intent, turn_operation, …)
         ↓
Evidence producers  →  Relationship Evaluator  →  Decision
         ↓
[execution when selected]
         ↓
Decision finalization (when required)  →  Persist  →  Render
```

Macro-phase classification (Attach → Evaluate → Decide → finalize / render): [`planning/pipeline/MACRO_PHASES.md`](planning/pipeline/MACRO_PHASES.md). NLU failure remains a planner admission boundary before Attach. Decision Trace records CurrentRequest, AttachedRequest, DecisionInput, Decision, and DecisionFinalization.

---

## Mutation Boundary

Planning turns separate **evidence** from **state change**.

- **Evidence is immutable.** Request models, confirmation classification, revision facts, availability trust evidence, and related planning evidence describe what was observed; producers must not rewrite that evidence to encode outcomes.
- **Working-session mutations occur only at the planning mutation boundary.** After evidence is produced, clears, restores, confirmation consumption, and related working-state writes go through the mutation coordinator—not through scattered direct invalidation calls from merge or evidence producers.
- **Decision consumes post-mutation state.** Outcome selection reads the working turn after authorized mutations have been applied; Decision does not itself own registry-style booking clears, and evidence stages do not select final plan outcomes.
- Invalidation **declares** trigger → clear-set rules; the mutation boundary **applies** them onto the working turn.
- Durable persistence remains SessionProjector / session infrastructure after the turn completes—not an alternate mutation path during planning.

---

## Validation and clarification ownership

- **NLU** owns semantic interpretation, grounding, ambiguity detection, and validation of structured semantic output. Core consumes that output as semantic evidence.
- **Core** owns workflow and session validation, proposal validity, confirmation authorization, availability validity, and execution eligibility.
- Core may validate the shape and workflow applicability of NLU evidence, but it must not reconstruct, reinterpret, or revalidate semantic meaning from raw text.
- When NLU emits structured uncertainty or clarification evidence, Core decides whether clarification is the next workflow action. Rendering owns the resulting user-facing wording.
- **Capabilities and execution clients** own validation of the external operation constraints, requests, and responses within their contracts. Their validation does not select workflow outcomes.
- Session schema modules validate session shape only; they do not make semantic, planning, or execution decisions.

---

## Raw-language boundary

NLU is the sole authority for semantic interpretation of raw user language, including affirmation and rejection; confirmation responses; proposal acceptance, rejection, and modification; corrections and replacements; browse/navigation language; option and ordinal references; service/date/time expressions; negation and hypothetical language; and mixed dialogue acts.

Core may consume raw text only to:

- forward it to NLU;
- retain conversation history;
- log or trace it;
- render or transport it without semantic classification.

Core must not use regexes, keyword lists, substring matching, token matching, or other raw-text inspection to infer intent, response acts, slot values, proposal acceptance or rejection, correction, option selection, navigation, negation, or workflow transitions. Prohibited patterns include:

```python
if pending_proposal and user_text.startswith(("yes", "yeah", "sure")):
    accept_proposal()
```

Equivalent regex checks, substring checks, affirmative/negative token sets, service-name matching, ordinal parsing (for example interpreting “first” or “second”), navigation aliases (for example interpreting “next” or “back”), and negation detection inside Core are equally prohibited. This prohibition applies even as a fallback when NLU evidence is absent. Conservative clarification is allowed; silent semantic reconstruction is not.

---

## Intent policy

Booking sequencing, required slots, execution steps, step modes (exploratory/committing), and intent durability are **policy-driven**. Code reads that policy and derives facts—it must not invent booking behaviour or execution sequencing outside policy. Other routing concerns (for example handler delegation) may use separate declarative policy; they do not replace booking execution policy.

---

## Booking execution

- Policy selects the execution step (`plan.action`); it may be `null` when nothing should run.
- **Execution coordination** is a Core responsibility. It owns execution eligibility, preparation, client or capability binding, dispatch, and workflow post-hooks needed before rendering. It does not interpret raw language or perform the external operation itself.
- **Execution clients** are adapters for selected external booking operations. They validate the external request and response contract they own, perform the operation, and return a structured business outcome. They do not mutate session directly or decide the next user action.
- Rendering produces the user-facing response from plan, execution, and other supplied evidence.
- SessionProjectorV2 computes or applies the durable session projection; session infrastructure performs storage I/O for the following turn. The turn orchestrator owns neither set of rules.

---

## Capabilities

- Planning decides whether a capability must activate before booking can proceed.
- Execution and API boundaries coordinate capability execution once planning has selected it.
- **Capabilities** encapsulate supporting or prerequisite external business operations, such as payment or identity verification. A capability may use an external client, but it remains distinct from Core workflow policy and from the booking-operation adapter selected by execution coordination.
- Capabilities validate the external constraints and responses within their operation contract and return structured results; Core validates workflow consequences and merges durable continuation facts into session.
- Capabilities do not own session state, planner logic, or conversation flow.
- External systems remain authoritative for the business records they own; Core session retains only the identifiers and outcomes required for continuation.
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

## Test execution policy

Codex must not execute tests after making changes.

This prohibition includes:

- unit, integration, end-to-end, and live-model tests;
- `pytest`, `unittest`, Jest, Vitest, Playwright, or equivalent test runners;
- test commands invoked indirectly through scripts, Makefiles, task runners, or CI helpers.

After every implementation:

1. Do not run tests.
2. Provide the exact recommended test commands for the user to execute.
3. Separate focused tests from the full regression suite.
4. Explain briefly what each command verifies.
5. Report tests as `NOT RUN — awaiting user execution`.
6. Never claim that tests pass until the user supplies the results.
7. When the user supplies test results, analyze them and make any required corrections, but still do not execute tests.

Codex may inspect, add, or modify test files. It may run non-test validation commands such as compilation, type checking, linting, formatting checks, and `git diff --check`, unless the user instructs otherwise.

---

## Implementation discipline

### Incremental implementation

- Architecture evolves through small, reviewable changes.
- Each PR should have one clearly defined architectural objective.
- Implement only the agreed scope for the current PR.
- Do not implement future work ahead of the agreed roadmap.
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
- Do not implement deferred tracing rollouts without explicit approval driven by real debugging pain.

### Enable for investigation

```bash
export DIALOGCART_TRACE_DECISIONS=1
# or: pytest --trace-decisions
# or: python -m core.tracing.decision_trace_cli saved_response.json
```
