# DialogCart Architecture Constitution

**Status:** Canonical living architecture reference  
**Scope:** DialogCart as implemented on the `aireview` branch  
**Audience:** Human contributors and AI-assisted development tools

This document captures enduring architectural intent. It defines ownership, dependency direction, invariants, extension rules, completed recoveries, and the approved recovery roadmap. Temporary bugs, one-off investigations, and test-specific workarounds do not belong here.

The repository remains the source of truth for implementation. When implementation and this constitution diverge, the divergence must be reported explicitly; it must not be silently normalized by adding compatibility logic.

---

## 1. Vision

DialogCart is a policy-driven conversational commerce platform in which current-turn understanding, durable workflow state, planning decisions, external operations, persistence, and user-facing language have explicit owners.

The durable turn lifecycle is:

```text
Planning
  → Execution
  → Rendering
```

Inside Planning, the intended dependency flow is:

```text
CurrentRequest
  → Attach
  → Evidence
  → Planning Mutation
  → Decision
  → PlanningOutcome
```

Execution may provide evidence required to complete the existing Decision through `DecisionFinalization`. Persistence projects only durable results. Rendering converts finalized evidence into conversational output.

The architecture optimizes for:

- one authoritative owner per persistent state or semantic decision;
- traceable behaviour from utterance to response;
- policy-driven extension across booking domains;
- current-turn evidence separated from durable session state;
- small, independently releasable architectural changes;
- removal of compensating logic rather than accumulation of workarounds.

---

## 2. Architectural Principles

### 2.1 Single ownership

Every durable state field and every semantic decision has one authoritative owner. Transient projections may mirror owned data during a turn, but they are not independent sources of truth.

### 2.2 Evidence before Decision

Stages and supporting evaluators produce typed evidence. Evidence describes what is true; it does not select final planner outcomes.

Decision alone selects planning-turn outcomes such as:

- `action`;
- `status`;
- `stage`;
- `awaiting`;
- final presentation branch.

### 2.3 Mutation is explicit

Merge is additive by default. Destructive removal of slots, confirmation authorization, availability trust, or binding state must pass through an explicit planning invalidation or mutation boundary.

Evidence producers request mutations; they do not apply unrelated state changes themselves.

### 2.4 One-way dependency flow

Dependencies move forward through the lifecycle. Supporting modules must not start, re-enter, or reinterpret orchestration.

Execution does not replan. Rendering does not decide. Persistence does not infer domain meaning. NLU does not own durable workflow state.

### 2.5 Current-turn evidence is not session state

NLU reports the meaning of the current utterance. Core owns durable stickiness. Conversation context may disambiguate the current utterance but must not fabricate facts that the user did not express this turn.

### 2.6 Policy over workflow-specific branching

Required slots, sequencing, durability, committing versus exploratory steps, and execution eligibility are expressed through policy and reusable facts wherever practical.

### 2.7 Incremental recovery

Architectural recovery proceeds one ownership boundary at a time. Each phase must preserve behaviour and be independently releasable. Broad rewrites and speculative abstractions are prohibited unless separately approved.

---

## 3. Ownership Map

| Concern | Authoritative owner | Notes |
|---|---|---|
| Current utterance meaning | NLU | Intent, facts, entity resolution, current-turn temporal/service evidence |
| Durable conversation and booking state | Core session | Single persistent source of truth |
| Current-turn workflow attachment | Stage 01 / `AttachedRequest` | Effective planning intent, turn operation, gate action, continuation |
| Working-turn merge | Stage 02 | Authoritative transient payload for the turn |
| Revision detection | Stage 03 | Detects criteria changes and emits/applies revision effects through invalidation |
| Slot completeness | Stage 04 | Missing, declined, promptable and next-slot evidence |
| Availability trust | Stage 05 | Fingerprints, cache trust and readiness evidence |
| Confirmation interpretation | Stage 06 | YES/NO/ANOTHER_REQUEST semantics and typed lifecycle evidence |
| Capability readiness | Stage 07 | Capability gating evidence |
| Planning mutation | Planning mutation/invalidation boundary | Applies reject, consume/supersede and bound-datetime-clear evidence |
| Planner outcome | Decision / Stage 08 | Sole selector of final planning outcome fields |
| Post-execution/time completion | DecisionFinalization | Completes the existing Decision from new evidence |
| Renderable planning envelope | Stage 09 | Projection and assembly; must not invent new semantic decisions |
| Operational eligibility and dispatch | ExecutionCoordinator | Client binding, operational prerequisites, dispatch and workflow post-hooks |
| Durable turn-end projection | SessionProjectorV2 | Maps finalized artifacts into Session V2 |
| User-facing wording | Rendering | Composes supplied evidence; does not invent business facts |
| Observability | Decision Trace | Orthogonal and non-controlling |

---

## 4. Pipeline Stages and Responsibilities

### Stage 01 — Attach

Reconciles raw NLU intent with the active durable workflow, classifies the confirmation gate once, and derives the typed turn operation.

It does not mutate session or select the final outcome.

### Stage 02 — Working Turn

Builds the authoritative transient working payload by combining current-turn NLU deltas with the active session. It stamps current-turn provenance before sticky merge can obscure origin.

### Stage 03 — Revision

Detects service, date, time, and other booking-criteria revisions. It owns revision interpretation, not final planner outcome selection.

### Stage 04 — Slots

Computes slot-completeness evidence:

- missing slots;
- declined slots;
- promptable optional slots;
- `ask_next`;
- clarification state.

The long-term direction is for Stage 04 to return canonical evidence without hidden downstream reconstruction.

### Stage 05 — Availability

Determines whether stored availability remains trusted for current criteria. It owns readiness and fingerprint evidence, not search execution or presentation wording.

### Stage 06 — Confirmation

Interprets the attached gate result and emits confirmation evidence, including:

- user acceptance/satisfaction;
- rejection;
- supersession/consumption;
- bound-datetime-clear request;
- availability invalidation;
- awaiting confirmation.

Stage 06 must not render, select planner outcomes, prepare execution, or directly apply destructive workflow mutations.

Pending-entry and availability-reshow remain transitional responsibilities and are listed in the recovery roadmap.

### Stage 07 — Capability

Produces capability readiness/wait evidence before a committing action can proceed.

### Stage 08 — Decision

Selects the final planner outcome from evidence. It owns precedence and the final values of `action`, `status`, `stage`, `awaiting`, and presentation branch.

Stage 08 must not become the place where missing evidence is reconstructed, policy eligibility is independently recomputed in several forms, or rendering envelopes are assembled. Those construction responsibilities are the current highest-priority recovery area.

### Stage 09 — Outcome

Projects the completed Decision and supplied evidence into a renderable `PlanningOutcome` and compatibility envelope.

It must not derive new clarification semantics, decide slot exposure, or reconstruct domain truth after Decision.

### DecisionFinalization

Completes planner-owned fields when time resolution or execution supplies additional evidence for the same Decision. It is not a second planner.

---

## 5. Session Ownership

Core session is the only persistent source of booking and conversation truth.

### Session V2

Persisted sessions use `schema_version: 2` with nested sections for conversation, planning, booking, availability, confirmation, and capability state.

- V1 sessions are normalized on load.
- Legacy mirrors are transitional compatibility views.
- `SessionProjectorV2` owns durable turn-end projection.
- Planning owns planning slots and bound datetime.
- Booking contains only successfully committed identifiers.
- Raw NLU facts are not persisted as a durable bag.
- Execution results remain ephemeral unless explicitly projected.

### Durable versus transient

During a turn, working payloads, facts, proposals, execution plans, and compatibility dictionaries may exist. They are processing views. They must not compete with session or become independent lifecycle owners.

### Confirmation state

`confirmation_state` authorizes a pending irreversible operation; it is not booking truth.

The effective durable lifecycle is:

```text
pending
  → successful commit, rejection, or genuine supersession
  → cleared
```

User acceptance is turn-scoped evidence (`user_confirmation_satisfied` / continuation). `confirmed` may be understood only as transient current-commit authorization and must not persist as a resting cross-turn state.

---

## 6. NLU Contract

NLU is stateless per request and owns:

- intent classification;
- fact extraction;
- entity resolution;
- current-turn temporal, service and reference meaning;
- dialog-act distinctions such as authorization, rejection and informational inquiry.

NLU does not own:

- sticky session state;
- planner sequencing;
- booking execution;
- confirmation lifecycle;
- durable slots.

### Grounding invariant

NLU must not fabricate booking facts absent from the current utterance. Context may resolve shorthand, pronouns, continuation, option references, relative expressions, or active workflow meaning, but context alone cannot become a new current-turn slot delta.

### Availability contract

NLU emits an `AVAILABILITY` intent and optional browse operation. It describes the request; it does not direct Core to search. Core decides whether to reuse trusted cache, browse presentation, or execute a fresh search.

---

## 7. Evidence Model

Typed evidence separates interpretation from consequences.

Current evidence families include:

- `IntentDecision` / `AttachedRequest`;
- `WorkingTurn`;
- `RevisionResult`;
- `SlotTurnState`;
- `AvailabilityDecision`;
- `ConfirmationDecision`;
- `ConfirmationRejectEvidence`;
- `ConfirmationConsumeEvidence`;
- `ConfirmationLifecycleEvidence`;
- `AvailabilityInvalidationEvidence`;
- `BoundDatetimeClearEvidence`;
- `CapabilityDecision`;
- business facts;
- time-resolution evidence;
- `DecisionInput`.

Evidence objects must:

- remain outcome-free;
- contain semantic facts, not mutated payloads;
- avoid final conversational wording;
- avoid hidden instructions encoded as arbitrary flags;
- be immutable where practical.

`RelationshipEvaluator` remains observational until an explicit architecture decision activates it as controlling evidence.

---

## 8. Mutation Boundary

Planning mutation is the sole application point for destructive changes requested by confirmation and revision evidence.

The current confirmation mutation boundary applies, in order:

1. rejection invalidation;
2. confirmation consume/supersede;
3. bound-datetime clear;
4. synchronization of working slot projections.

The mutation boundary may update request-scoped working state so stale pre-mutation data cannot be resurrected during the same turn. It must not replace the SessionProjector as the durable persistence owner.

### Invalidation registry

`core.session.invalidation` owns explicit invalidation mechanics and declarative triggers. It should receive complete typed commands from upstream policy rather than infer broad workflow meaning from many pipeline representations.

The registry must not become a second orchestrator.

---

## 9. Decision Responsibilities

Decision owns:

- precedence between rejection, clarification, confirmation, capability, presentation and execution;
- final `status`;
- final `action`;
- final `stage`;
- final `awaiting`;
- final presentation branch;
- final policy-client/route identity;
- terminal-state legality.

Decision should consume precomputed evidence for:

- execution eligibility;
- temporal readiness;
- clarification selection;
- presentation eligibility;
- availability trust;
- slot completeness;
- confirmation lifecycle;
- capability readiness.

Decision must not independently manufacture all of these inputs while also selecting the outcome.

The next approved architectural recovery is to extract evidence construction from `stage08_decision_plan.py` while preserving Decision selection and precedence.

---

## 10. Execution Responsibilities

Execution begins only after policy has selected an execution action.

ExecutionCoordinator owns:

- operational precondition validation;
- client binding;
- identity and tenant prerequisites;
- normalized execution input preparation;
- dispatch;
- workflow post-processing;
- structured failure/blocked results.

Execution does not decide whether the conversation should execute a business step. It may fail closed when operational prerequisites are missing.

The target boundary is a typed execution command from Decision so Execution validates operational feasibility without rerunning planning-policy eligibility.

Customer identity is tenant-scoped. Core resolves or creates the canonical commerce customer before commit. No hardcoded customer ID is permitted, and chat `user_id` is never treated as a commerce customer primary key.

---

## 11. Rendering Responsibilities

Rendering owns:

- conversational wording;
- composition of planning, execution, handler and capability evidence;
- formatting of prepared availability and confirmation models;
- workflow resume wording.

Rendering does not own:

- planning facts;
- slot completeness;
- confirmation interpretation;
- action selection;
- session mutation;
- world knowledge absent from evidence.

Stage 09 may assemble a rendering model, but it must not make new domain decisions after Decision.

---

## 12. Architectural Invariants

1. Core session is the single durable owner of booking state.
2. NLU emits current-turn deltas and never fabricates unsupported slots.
3. Merge is additive unless an explicit invalidation trigger applies.
4. Explicit state removal passes through the invalidation/mutation boundary.
5. Temporal proposals are not durable until bound or explicitly confirmed.
6. Current-turn provenance is stamped before merge.
7. Confirmation is classified once per pending turn.
8. User acceptance is evidence; successful commit consumes pending authorization.
9. Failed or blocked execution must not leave false durable confirmation.
10. Decision is the sole selector of planning outcomes.
11. DecisionFinalization completes the same Decision; it does not create a parallel planner.
12. Execution does not reinterpret planning policy.
13. Rendering does not invent facts or mutate planning/session state.
14. SessionProjectorV2 owns durable turn-end projection.
15. Availability browsing never executes `SEARCH_AVAILABILITY`.
16. Presentation state never changes booking truth.
17. Availability trust is invalidated when search criteria change.
18. Ambiguous offered-slot selections resolve only against the active presented window.
19. Handler delegation preserves durable booking state unless explicit invalidation applies.
20. RelationshipEvaluator remains observational unless explicitly activated by a future ADR.
21. Decision Trace is observational and never controls business behaviour.
22. Architectural changes are incremental, scoped, and behaviour-preserving unless product behaviour is explicitly changed.

---

## 13. Completed Recoveries

The following architectural recoveries are complete in the current direction of `aireview`:

- canonical Stage 01–09 planning pipeline and Decision ownership established;
- planning/orchestrator circular dependency broken through neutral modules;
- current-turn planning evidence stamped before merge;
- NLU Stage 2 strengthened as semantic authority;
- confirmation dialog-act boundaries restored for authorization, rejection and meta-questions;
- non-confirmation and invalid-turn confirmation preservation/resume restored;
- durable pre-execution `confirmed` write removed;
- ExecutionCoordinator confirmation rollback removed;
- confirmation rejection converted to semantic evidence;
- rejection rendering moved out of Stage 06;
- bound-datetime clearing converted to typed evidence;
- confirmation consume/reject/bound-clear mutations centralized after Stage 06;
- tenant-scoped customer resolve-or-create lifecycle added;
- hardcoded customer ID fallback removed;
- unsupported context-leaked temporal evidence stripped in NLU;
- successful/blocked confirmation lifecycle aligned with durable `pending` → cleared semantics.

---

## 14. Current Recovery Roadmap

### Priority 1 — Decision evidence construction

Recover `stage08_decision_plan.py` by extracting pre-Decision evaluators for:

- execution eligibility;
- temporal readiness;
- clarification selection;
- presentation/recovery eligibility;
- canonical ask-next evidence.

Decision retains precedence and final outcome selection.

### Priority 2 — Pending-entry evidence and mutation effects

- represent confirmation `ENTER_PENDING` as lifecycle evidence;
- apply it through the planning mutation boundary;
- replace `slots_adjusted` with typed mutation effects such as slot, availability and confirmation invalidation domains;
- rerun evaluators based on invalidated evidence domains rather than a confirmation-specific Boolean.

### Priority 3 — Stage 09 projection recovery

Move clarification semantics, slot exposure decisions, service reconciliation and cached-availability domain reconstruction before Stage 09. Stage 09 should serialize a finalized Decision and rendering model.

### Priority 4 — Typed execution command

Produce a typed execution command from Decision and remove duplicated planning eligibility from ExecutionCoordinator. Execution continues to validate operational prerequisites.

### Priority 5 — Invalidation stabilization

Keep invalidation as the mutation mechanism while reducing context interpretation and large optional-parameter surfaces. Upstream policy should issue typed invalidation commands.

### Priority 6 — Session migration completion

Complete removal of load-bearing V1 mirrors only after all runtime consumers use Session V2 ownership paths.

---

## 15. Explicit Non-Goals

The following are not approved architectural directions:

- rewriting the planning pipeline;
- introducing a parallel orchestration path;
- moving language interpretation into Core;
- allowing NLU or capabilities to persist session state;
- restoring post-NLU regex intent overrides;
- hardcoded customer or tenant identifiers;
- putting customer creation in booking dispatch or booking insert endpoints;
- making RelationshipEvaluator control flow without a new ADR;
- using `plan.action` as conversation phase;
- introducing presentation actions;
- allowing rendering to choose workflow outcomes;
- allowing execution to replan;
- replacing policy with workflow-specific branching;
- broad refactors bundled with feature changes;
- speculative abstractions not required by an approved recovery phase;
- weakening tests to make architectural migrations pass.

---

## 16. ADR Index

### Accepted

- [`ADR-001: Final Planning Pipeline Architecture`](adr/ADR-001-planning-pipeline.md) — canonical pipeline, evidence ownership, Decision ownership, one-way dependency flow, extension guidance.

### Supporting architecture references

- [`../AGENTS.md`](../AGENTS.md) — root subsystem ownership constitution.
- [`../src/core/AGENTS.md`](../src/core/AGENTS.md) — Core-specific lifecycle, session, confirmation, availability, execution and rendering rules.
- [`../src/core/planning/pipeline/ARCHITECTURE.md`](../src/core/planning/pipeline/ARCHITECTURE.md) — frozen pipeline inventory and stage boundaries.
- [`../src/core/planning/pipeline/MACRO_PHASES.md`](../src/core/planning/pipeline/MACRO_PHASES.md) — stage-to-macro-phase mapping.
- [`../src/core/planning/pipeline/INVARIANTS.md`](../src/core/planning/pipeline/INVARIANTS.md) — code-level planning invariants.
- [`../src/core/orchestration/contracts/AVAILABILITY_INTERACTION_CONTRACT.md`](../src/core/orchestration/contracts/AVAILABILITY_INTERACTION_CONTRACT.md) — search versus browse semantics.
- [`../src/core/tracing/DECISION_TRACE.md`](../src/core/tracing/DECISION_TRACE.md) — primary orchestration observability contract.

---

## Maintenance Rules

Update this constitution only when an enduring ownership decision changes.

For every architectural change:

1. identify the owner before implementation;
2. express new runtime meaning as typed evidence where applicable;
3. preserve single-source-of-truth session semantics;
4. add or update invariants and Decision Trace reason codes;
5. complete focused parity validation;
6. update this document and any affected ADR after the implementation is accepted.

Do not record temporary failures, investigation transcripts, branch-local experiments, or test fixture details here.
