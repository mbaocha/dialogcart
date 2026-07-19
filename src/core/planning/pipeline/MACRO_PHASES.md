# Planning Pipeline — Macro-Phase Classification

**Phase 5 (complete):** Decision owns planner outcomes including post-execution
time-resolution finalization. Numbered modules expose runtime order directly.

## Target model

```text
CurrentRequest
    ↓
Attach
    ↓
AttachedRequest
    ↓
Evidence Producers
    ↓
Relationship Evaluator
    ↓
Decision (decide)
    ↓
[execution…]
    ↓
DecisionFinalization (finalize_decision_after_time_resolution)
    ↓
Persist
    ↓
Render
```

## Ownership

- **CurrentRequest** — immutable current-turn NLU evidence.
- **AttachedRequest** — sole owner of workflow attachment fields.
- **DecisionInput** — Evaluate evidence bundle (no final outcomes).
- **decide()** — sole selection of action/status/stage/awaiting for the planning turn.
- **finalize_decision_after_time_resolution()** — sole Decision finalization for
  time-match exact/mismatch (planning-time pre-bind and post-SEARCH).
- **nlu_failure_fallback** — intentional **planner admission boundary** (not Decision).

## Stage map

NLU invocation and failure admission occur before the numbered pipeline.

| Order | Module | Component | Phase |
|-------|--------|-----------|-------|
| 01 | `stage01_intent.py` | intent reconciliation / Attach | Attach |
| 02 | `stage02_working_turn.py` | working-turn construction | Attach |
| 03 | `stage03_revision.py` | revision policy | Evaluate |
| 04 | `stage04_slots.py` | slot completeness | Evaluate |
| 05 | `stage05_availability.py` | availability trust | Evaluate |
| 06 | `stage06_confirmation.py` | confirmation evidence | Evaluate |
| 07 | `stage07_capability.py` | capability gating | Evaluate |
| 08 | `stage08_decision_plan.py` via `decision.decide()` | planner outcome | Decide |
| 09 | `stage09_outcome.py` | outcome / clarification assembly | Render |
| post-exec | `decision_finalization.py` | time-resolution completion | Decision finalization |

When Stage 06 adjusts slots, the orchestrator re-runs Stages 04 and 05 before
continuing to Stage 07. Workflow-route derivation is an inline orchestrator
projection between Stages 08 and 09, not an independent execution stage.

## Package roles

- ``planning.pipeline`` — orchestration (stages, Decision, finalization).
- ``planning.pipeline.requests`` — CurrentRequest, AttachedRequest,
  turn-operation interpretation, and Attach diagnostics.
- ``planning.pipeline.decision`` — DecisionInput, Decision evidence DTOs, and
  the cohesive Decision API.
- ``planning.planner`` — algorithms / helpers (intent resolution, slot policy,
  post-exec projection); not a second orchestration layer.
- ``planning.policy`` — static intent/action policy tables.
