# ADR-001: Final Planning Pipeline Architecture

- **Status:** Accepted
- **Date:** 2026-07-17

## Context and Motivation

The planner was refactored to establish explicit boundaries between current-turn
NLU evidence, workflow interpretation, planning evidence, outcome selection, and
post-execution completion.

Previously, those responsibilities were distributed across planner stages,
mutable payload fields, compatibility projections, workflow code, and
post-execution helpers. The same information could be read from several
representations, and multiple components could write planner outcomes such as
`action`, `status`, `stage`, `awaiting`, and `missing_slots`. This made ownership
unclear, allowed stale or session-derived data to be mistaken for current-turn
evidence, and made it difficult to determine where a planning decision had been
made. Compatibility wrappers also obscured the canonical orchestration path.

The refactor preserves the existing planning and execution behaviour while
making each phase and its owner explicit.

## Decision

Core planning has one canonical orchestration path:

```text
CurrentRequest
    ↓
Attach
    ↓
AttachedRequest
    ↓
Evidence
    ↓
RelationshipEvaluator
    ↓
Decision
    ↓
Execution
    ↓
DecisionFinalization
    ↓
Persist
    ↓
Render
```

`CurrentRequest` contains immutable evidence from the current NLU response.
Attach interprets that request in the active workflow and produces one
`AttachedRequest`. Evidence producers then derive planning facts without
selecting final outcomes. `RelationshipEvaluator` evaluates the relationship
between the request, workflow, and conversation context. `Decision` selects the
planner outcome. Execution may perform the selected external operation.
`DecisionFinalization` completes planner-owned fields when execution supplies
new evidence, after which durable state is persisted and the response is
rendered.

The durable Core lifecycle remains Planning → Execution → Rendering. Persist is
the durable turn-end projection boundary, and Decision finalization is the
completion of an existing planning decision, not an additional peer lifecycle
stage.

## Ownership

### Current user turn

`CurrentRequest` owns the immutable semantic evidence from the current user
utterance. It contains only current-turn NLU output and must not contain merged
session state or workflow interpretations. `ConversationEngine` remains the
owner of the complete conversational turn lifecycle.

### Workflow interpretation

Attach owns interpretation of the current request in workflow context.
`AttachedRequest` is the sole read model for attachment-derived fields such as
the effective planning intent, turn operation, reset state, confirmation
continuation, and confirmation-gate action.

### Evidence

Evidence producers own derived observations. `DecisionInput` is the evidence
bundle consumed by Decision. Evidence describes facts and relationships; it
does not select planner outcomes.

### Planner outcome

`Decision` is the sole owner of selecting planner outcomes, including `action`,
`status`, `stage`, `awaiting`, and the other fields that describe what planning
decided for the turn.

### Post-execution completion

`DecisionFinalization` owns completion of planner outcomes when execution or
time resolution provides additional evidence. It may finalize the existing
Decision but must not create an independent decision path.

### Session

Core session is the single owner of persistent booking and conversation state.
NLU, planning evidence, execution results, and rendering artifacts are
transient unless the session projector explicitly maps them into durable
session fields.

### Rendering

Rendering owns conversion of finalized planning and execution artifacts into a
user-facing response. It does not infer planning decisions or mutate durable
booking state.

## Package Responsibilities

### `planning.pipeline`

The canonical planning orchestration layer. It owns the staged flow, request
boundaries, evidence assembly, relationship evaluation, Decision selection,
and Decision finalization. New turn-level planning orchestration belongs here.

### `planning.planner`

Planning algorithms and supporting logic used by the pipeline, including
effective-intent resolution, required-slot helpers, and post-execution
projection helpers. It is not a second orchestration layer.

### `planning.policy`

Static policy access and routing helpers. Policy defines intent requirements,
eligible actions, and sequencing constraints; it does not own runtime state or
orchestrate a turn.

### `planning.facts`

Runtime business-fact derivation. It translates session, slot, confirmation,
availability, and workflow state into evidence that generic planning policy can
evaluate. Facts do not choose the final outcome.

### `planning.time_resolution`

Deterministic interpretation and binding of requested times against available
offers. It produces time-resolution evidence and low-level plan-container
patching support; Decision finalization owns the resulting planner outcome.

### `planning.temporal_proposal`

Extraction and normalization of date and time proposals. Proposals represent
user constraints or preferences and are not durable booking slots until Core
binds or explicitly confirms them.

## Design Principles

### Single orchestration path

All production planning turns pass through `planning.pipeline`. Alternate
planner entry points, compatibility orchestration, and duplicated stage
sequences are not permitted.

### Single ownership

Each piece of state or interpretation has one authoritative owner. Transient
processing views may project owned data but must not become competing sources
of truth.

### Immutable request objects

`CurrentRequest` and `AttachedRequest` are immutable boundaries. Later phases
derive new evidence or outcomes instead of adding hidden control fields to
earlier request representations.

### Evidence before Decision

Business facts, confirmation classifications, slot completeness, availability
state, and relationship evaluations are produced as evidence before Decision.
Evidence producers must not write final planner outcomes.

### One-way dependency flow

Dependencies follow the pipeline direction. Orchestration may invoke policy,
fact, proposal, resolution, and planner-support modules; those modules must not
start or re-enter orchestration. Execution and rendering consume finalized
artifacts and do not reach backward to reinterpret the request.

### Decision owns planner outcomes

Decision selects planner outcome fields. If execution provides evidence needed
to complete them, DecisionFinalization performs that completion. Workflows,
fact producers, executors, persistence, and renderers must not independently
select or override planner outcomes.

## Consequences

The architecture provides a single traceable route from user evidence to a
rendered response, with explicit models at ownership boundaries. New behaviour
requires an evidence source and a Decision or policy rule rather than a hidden
payload flag or parallel planner. Some supporting package names remain broad,
but their responsibilities are constrained by this ADR.

## Future Extension Guidance

New capabilities such as payments, cancellations, promotions, identity checks,
or deposits must integrate through the existing flow:

1. Represent current-turn meaning in NLU output and `CurrentRequest` without
   copying durable workflow state into it.
2. Interpret capability or workflow context during Attach and add an
   attachment field only when the interpretation is broadly required
   downstream.
3. Add runtime observations through a typed evidence producer or the business
   fact registry. Evidence must remain outcome-free.
4. Express sequencing and eligibility in policy where possible. Decision uses
   the evidence and policy to select the next planner outcome.
5. Execute the selected operation through the execution/capability boundary.
   Capability clients perform business operations and return outcomes; they do
   not control the conversation.
6. Use DecisionFinalization only when execution evidence must complete
   planner-owned fields for the same Decision. Do not introduce a second
   post-execution planner.
7. Persist only explicitly mapped durable results through the session
   projector. Rendering consumes finalized artifacts without making new
   planning decisions.

For example, payment readiness or promotion validity should be evidence;
payment collection or cancellation should be policy-selected execution;
successful transaction identifiers should be projected into session only when
durable. None of these extensions should add mutable compatibility flags,
parallel orchestration paths, direct capability-to-session writes, or
renderer-owned planning logic.
