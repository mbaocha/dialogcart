# Planning Pipeline — Architectural Invariants

The planner architecture is frozen. Preserve these invariants; extend behaviour
through evidence and policy, not by relocating ownership. See `ARCHITECTURE.md`
and `MACRO_PHASES.md`.

1. **Single orchestration path** — production planning turns go through `run_planning_pipeline` only.
2. **Stages emit evidence, not outcomes** — Stages 03–07 must not select final `action` / `status` / `stage` / `awaiting`.
3. **Decision is the sole planning-turn outcome owner** — only `decide()` / handler-delegation Decision selects planner outcomes.
4. **DecisionFinalization owns post-execution decision completion** — time-match exact/mismatch updates go through `finalize_decision_after_time_resolution`; operational execution blocks go through `finalize_decision_after_execution_blocked`. After `CUSTOMER_ID_REQUIRED`, Decision demotes to `NEEDS_CLARIFICATION` while keeping `confirmation_state=pending`; the next turn with customer identity re-presents via Stage 08 (`identity_resolved_reconfirm`) without durable reconfirm flags.
5. **RelationshipEvaluator is observational** — it must not influence Decision or any stage's control flow.
6. **Request models are immutable** — `CurrentRequest` / `AttachedRequest` are frozen; later phases derive new evidence instead of mutating them.
7. **AttachedRequest is the sole attachment read model** — do not re-derive attachment from payload flags.
8. **Confirmation gate classification stays YES / NO / ANOTHER_REQUEST** — the gate owns only the user's relationship to a pending confirmation.
9. **Explicit user requests supersede pending workflow via evidence** — e.g. AVAILABILITY during pending confirmation emits invalidation evidence; Decision selects `SEARCH_AVAILABILITY`. Stages must not write outcomes directly.
10. **Invalidation is operation-specific** — never globally clear booking facts for every `ANOTHER_REQUEST`.
11. **Core session owns durable state** — planning evidence is transient unless explicitly projected by the session projector.
12. **Rendering / outcome assembly does not invent decisions** — Stage 09 projects Decision + evidence only.
13. **NLU failure is an admission boundary** — handled before Attach/Decision, never as a Decision writer.
14. **One-way dependency flow** — orchestration may call algorithms/policy/facts; those modules must not re-enter orchestration.
15. **`plan.action` is execution-only** — conversation phase is expressed by `status` / `stage` / `awaiting`, never inferred from `action`.
16. **No dead READY terminal** — illegal: `status=READY` + `action=None` + non-empty `missing_slots` without an explicit planner presentation outcome (`availability_reshow`, cache-satisfiable browse, `recovery_presentation`). After action selection, Stage 08 must reconcile to `NEEDS_CLARIFICATION` (with `awaiting` from `ask_next`), keep a presentation outcome, or keep an execution action.
