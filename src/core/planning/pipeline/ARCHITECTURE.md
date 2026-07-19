# Planning Pipeline — Frozen Architecture Inventory

Baseline architecture. Do not perform architectural refactors unless required to
support new behaviour. See `MACRO_PHASES.md` for the stage map and
`INVARIANTS.md` for rules future contributors must preserve.

## Data flow

```text
NLU admission (invoke_nlu_for_planning / nlu_failure_fallback)
 → stage01 Attach (IntentDecision → AttachedRequest)
 → stage02 WorkingTurn
 → stage03 Revision
 → stage04 Slots
 → stage05 Availability
 → stage06 Confirmation        (re-run stage04–05 when slots_adjusted)
 → stage07 Capability
 → DecisionInput (+ observational RelationshipEvaluation)
 → decide() / stage08 DecisionPlan
 → inline workflow route
 → stage09 PlanningOutcome
 → [Execution]
 → DecisionFinalization (finalize_decision_after_time_resolution)
 → Persist → Render
```

## Orchestrator (`orchestrator.py`)

- **Responsibility:** Single planning orchestration path (Stages 01–09 + admission + inline workflow route).
- **Inputs:** User text, session, NLU client, org/domain/tenant context.
- **Outputs:** Legacy turn envelope via `PlanningOutcome.to_turn_result()`.
- **Ownership boundaries:** Sequencing only; never invents planner outcomes.
- **May depend on:** All stage modules, `requests`, `decision`, `relationship_evaluator`, NLU admission.

## Pipeline stages

### Stage 01 — Intent / Attach (`stage01_intent.py`)
- **Responsibility:** Reconcile raw NLU intent with durable workflow; classify confirmation gate; derive `turn_operation`.
- **Inputs:** Luma response, session.
- **Outputs:** `IntentDecision` → `AttachedRequest`.
- **Ownership boundaries:** Attachment interpretation only.
- **May depend on:** Intent resolution, confirmation gate, `requests.derive_turn_operation`.

### Stage 02 — Working turn (`stage02_working_turn.py`)
- **Responsibility:** Merge NLU + session into the authoritative per-turn working payload.
- **Inputs:** Luma response, `AttachedRequest`, session.
- **Outputs:** `WorkingTurn`.
- **Ownership boundaries:** Transient working state for this turn only.
- **May depend on:** Session merge, temporal proposals.

### Stage 03 — Revision (`stage03_revision.py`)
- **Responsibility:** Detect service/date/time revisions; invalidate affected booking fields.
- **Inputs:** `WorkingTurn`, session.
- **Outputs:** `RevisionResult`; may mutate working slots/proposals.
- **Ownership boundaries:** Field-aware invalidation effects/evidence, not planner outcomes.
- **May depend on:** Booking revision detection, session invalidation.

### Stage 04 — Slots (`stage04_slots.py`)
- **Responsibility:** Compute missing slots and clarification semantics.
- **Inputs:** `WorkingTurn`, intent, `AttachedRequest`, session.
- **Outputs:** `SlotTurnState`.
- **Ownership boundaries:** Slot-completeness evidence.
- **May depend on:** Turn-state / missing-slot helpers, temporal binding checks.

### Stage 05 — Availability (`stage05_availability.py`)
- **Responsibility:** Evaluate whether prior availability is trusted for current criteria.
- **Inputs:** `SlotTurnState`, `WorkingTurn`, session, `AttachedRequest`.
- **Outputs:** `AvailabilityDecision`.
- **Ownership boundaries:** Availability-trust evidence.
- **May depend on:** Fact registry, availability fingerprints.

### Stage 06 — Confirmation (`stage06_confirmation.py`)
- **Responsibility:** Confirmation authorization evidence; consume pending confirmation on superseding requests; emit reject / availability-invalidation / bound-datetime-clear evidence.
- **Inputs:** `AttachedRequest`, slot/availability evidence, `WorkingTurn`, session.
- **Outputs:** `ConfirmationDecision`.
- **Ownership boundaries:** Confirmation evidence only — no planner `action`/`status` selection.
- **May depend on:** Confirmation gate, invalidation helpers.

### Stage 07 — Capability (`stage07_capability.py`)
- **Responsibility:** Capability gating evidence before commit.
- **Inputs:** Slot/confirmation/availability evidence, session.
- **Outputs:** `CapabilityDecision`.
- **Ownership boundaries:** Capability-wait evidence.
- **May depend on:** Capability policies, intent policy.

### Stage 08 — Decision plan (`stage08_decision_plan.py` via `decide()`)
- **Responsibility:** Select planner outcomes from evidence.
- **Inputs:** `DecisionInput` / stage evidence bundle.
- **Outputs:** `DecisionPlan` (`action`, `status`, `stage`, `awaiting`, flags, facts).
- **Ownership boundaries:** Sole planning-turn outcome selection.
- **May depend on:** Policy, business facts, confirmation-evidence projection.

### Stage 09 — Outcome (`stage09_outcome.py`)
- **Responsibility:** Assemble the renderable planning envelope / clarification branch.
- **Inputs:** `DecisionPlan`, workflow route, working/slot/confirmation evidence.
- **Outputs:** `PlanningOutcome`.
- **Ownership boundaries:** Presentation assembly, not new decisions.
- **May depend on:** Render helpers, temporal strip helpers.

## Request models (`requests.py`)

| Model | Responsibility | Inputs | Outputs | Ownership boundaries | May depend on |
|---|---|---|---|---|---|
| `CurrentRequest` | Immutable current-turn NLU evidence | Raw Luma response | Frozen request | No session/workflow fields | Temporal proposal extractors |
| `AttachedRequest` | Workflow interpretation of current turn | `IntentDecision` | Frozen attach fields | Sole owner of planning_intent / turn_operation / gate / continuation | Confirmation gate types |
| Turn-operation helpers | Map raw intent → typed operation | Raw intent, planning intent, response | `TurnOperation` | Attach-boundary only | — |

## Evidence types

| Type | Producer | Meaning |
|---|---|---|
| `IntentDecision` | Stage 01 | Attach + early-exit control |
| `WorkingTurn` | Stage 02 | Working payload |
| `RevisionResult` | Stage 03 | Revision detection |
| `SlotTurnState` | Stage 04 | Missing slots / clarification |
| `AvailabilityDecision` | Stage 05 | Availability trust |
| `ConfirmationDecision` | Stage 06 | Confirmation + supersede evidence |
| `ConfirmationRejectEvidence` | Stage 06 | Gate NO → Decision |
| `AvailabilityInvalidationEvidence` | Stage 06 | Force fresh availability evaluation |
| `BoundDatetimeClearEvidence` | Stage 06 | Ignore prior bound datetime |
| `CapabilityDecision` | Stage 07 | Capability wait |
| `MissingSlotsEvidence` | Derived from Stage 04 | Decision-facing slot evidence |
| `TimeResolutionEvidence` | Execution / pre-bind | Finalization input |
| `RelationshipEvaluation` | RelationshipEvaluator | Observational expectation fit |
| `DecisionInput` | Orchestrator | Immutable evidence bundle for `decide()` |

## Decision models (`decision.py`, `types.py`)

| Model | Responsibility |
|---|---|
| `DecisionInput` | Evidence only; no final action/status/stage/awaiting |
| `DecisionPlan` | Selected planner outcome |
| `decide()` / `decide_handler_delegation()` | Sole selectors of planning-turn outcomes |
| `WorkflowRoute` | Inline projection after Decision |

## DecisionFinalization (`decision_finalization.py`)

- **Responsibility:** Complete planner fields after time-match exact/mismatch (pre-bind or post-SEARCH).
- **Inputs:** Plan + `TimeResolutionEvidence`.
- **Outputs:** Mutated plan (status/stage/awaiting/slots/confirmation presentation as needed).
- **Ownership boundaries:** Post-execution / time-resolution completion of the same Decision — not a second planner.
- **May depend on:** Time-resolution helpers, confirmation gate.

## RelationshipEvaluator (`relationship_evaluator.py`)

- **Responsibility:** Answer whether `CurrentRequest` satisfies the prior Decision expectation.
- **Inputs:** `CurrentRequest`, conversation-context snapshot, optional gate action.
- **Outputs:** `RelationshipEvaluation` (observational).
- **Ownership boundaries:** Observability / architecture tracing only — must not drive control flow.
- **May depend on:** Confirmation-gate classification (read-only).
