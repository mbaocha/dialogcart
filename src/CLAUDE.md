# DialogCart — Claude Code Operational Handbook

This file exists to minimize repository exploration. Read this first. Read only the files named here for any given task. Do not search unrelated packages.

<!-- Architectural constitution — component ownership and boundaries -->
@../AGENTS.md
@core/AGENTS.md
<!-- Add @nlu/AGENTS.md and @extensions/capabilities/AGENTS.md when those files are created -->

---

## 1. Repository Map

### Top-Level Packages

| Package | Owns |
|---|---|
| `nlu/` | Production NLU: stage1/stage2 SLM extraction, normalisation, calendar binding (HTTP `/resolve` on port 9002) |
| `luma/` | Legacy rule-based NLU (retained for reference; not the production implementation) |
| `core/orchestration/` | Turn orchestration, session merge, availability fingerprint, execution dispatch |
| `core/planning/` | Turn planner, business facts, plan builder, intent resolution, missing slots |
| `core/policy/` | Intent policy loader (`intent_policy.yaml` consumer) |
| `core/session/` | Session schema, merge eligibility, invalidation, confirmation gate, persistence |
| `core/rendering/` | LLM-rendered reply text (availability, booking confirmation) |
| `core/planning/policy/` | Handler router, base intents, intent→action mapping |
| `core/workflows/` | Domain workflows + extensibility registry |
| `core/tracing/` | Decision trace, invariant trace, spine, formatters |
| `core/config/` | `intent_policy.yaml`, `capabilities.yaml`, `dialog_policy.yaml` |
| `extensions/` | Capability adapters (payment, noop) |
| `core/tests/` | All test suites |

### Entry Points

| Entry Point | Purpose |
|---|---|
| `app.py:lambda_handler()` | AWS Lambda entry; routes to `route_event()` |
| `core/orchestration/orchestrator.py:handle_message()` | Canonical per-turn entry for orchestration + execution |
| `core/orchestration/orchestrator.py:plan_message()` | Planning-only wrapper (calls `plan_turn`) |
| `core/planning/planner/turn_planner.py:plan_turn()` | NLU → session merge → plan, called by plan_message |
| `nlu/pipeline.py:NLUPipeline.run()` | Production NLU pipeline entry |
| `nlu/api.py` | Production NLU HTTP service (`/resolve`, default port 9002) |
| `run.py` | Starts `nlu.api` with fixed `LUMA_TEST_NOW` for deterministic testing |
| `luma/pipeline.py:LumaPipeline.run()` | Legacy NLU pipeline (not production) |
| `luma/api.py` | Legacy NLU service API (not production) |

### Frequently Modified Modules

These are the modules most likely to need changes in any given feature:

- `core/planning/planner/turn_planner.py` — turn flow, session merge gating, intent recovery
- `core/planning/planner/plan_builder.py` — action/stage selection logic
- `core/planning/facts/business_fact_registry.py` — business fact derivation
- `core/policy/intent_policy.yaml` — intent durable flags, execution steps, required slots
- `core/orchestration/availability_fingerprint.py` — fingerprint computation
- `core/orchestration/orchestrator.py` — execution dispatch, text injection
- `core/session/persist.py` — session state building from outcome
- `core/session/confirmation_gate.py` — confirmation classify logic
- `core/session/invalidation.py` — session invalidation triggers
- `core/rendering/availability_renderer.py` — availability reply text
- `core/orchestration/availability_pagination.py` — browse/next/previous handling
- `nlu/pipeline.py` — NLU pipeline and post-processing
- `nlu/stages/` — stage1 intent + stage2 slot extractors

### Test Locations

| Subsystem | Test Directory |
|---|---|
| Planning | `core/tests/planning/` |
| Orchestrator | `core/tests/orchestration/` |
| Session | `core/tests/session/` |
| Rendering | `core/tests/rendering/` |
| Execution | `core/tests/execution/` |
| Decision Trace | `core/tests/tracing/` |
| E2E (full turn) | `core/tests/e2e/` |
| Smoke (real NLU via `LUMA_BASE_URL`) | `core/tests/smoke/` |
| NLU pipeline | `nlu/tests/` |
| Legacy Luma NLU | `luma/tests/` (not in default pytest paths) |

---

## 2. Fast Navigation

For each task type, start with exactly these files. Read no others unless a specific symbol is missing.

### Availability (SEARCH_AVAILABILITY)
1. `core/orchestration/execution/clients/availability_client.py` — execution
2. `core/orchestration/availability_fingerprint.py` — fingerprint computation
3. `core/orchestration/temporal_proposal.py` — date/time proposal resolution for search
4. `core/rendering/availability_renderer.py` — rendering
5. Test: `core/tests/orchestration/test_availability_fingerprint.py`, `core/tests/execution/test_availability_execution.py`

### Pagination / Browse
1. `core/orchestration/availability_pagination.py` — `try_handle_availability_browse_turn()`
2. `core/orchestration/availability_browse.py` — browse operation normalization
3. `core/session/persist.py` — `presented_availability`, `availability_presentation` keys
4. Test: `core/tests/orchestration/test_availability_pagination.py`

### Booking / Confirmation (CONFIRM_APPOINTMENT)
1. `core/session/confirmation_gate.py` — gate classification
2. `core/planning/planner/plan_builder.py` — how AWAITING_CONFIRMATION is reached
3. `core/orchestration/execution/clients/booking_client.py` — execution
4. `core/rendering/booking_confirmation_renderer.py` — confirmation text
5. Test: `core/tests/session/test_confirmation_gate.py`, `core/tests/execution/test_confirmation_execution.py`

### Session (merge, persist, invalidation)
1. `core/session/merge.py` — `should_merge_session_context()`
2. `core/session/persist.py` — `build_session_state_from_outcome()`
3. `core/session/invalidation.py` — `apply_invalidation()`
4. `core/session/schema.py` — shape helpers
5. `core/orchestration/api/session_merge.py` — `merge_luma_with_session()`
6. Test: `core/tests/session/test_session.py`, `core/tests/session/test_merge_eligibility.py`

### Business Facts
1. `core/planning/facts/business_fact_registry.py` — `derive_business_facts()`
2. `core/planning/planner/plan_builder.py` — where facts are consumed
3. Test: `core/tests/planning/test_business_fact_registry.py`

### Fingerprint
1. `core/orchestration/availability_fingerprint.py` — all fingerprint logic
2. `core/orchestration/orchestrator.py` lines ~1344–1380 — where fingerprint is computed and persisted
3. Test: `core/tests/orchestration/test_availability_fingerprint.py`

### Decision Trace
1. `core/tracing/decision_trace.py` — `TurnTrace`, node types
2. `core/tracing/invariant_trace.py` — `TurnInvariantTrace`, `trace_stage()`
3. `core/tracing/spine.py` — `emit_execution_eligibility()`
4. `core/tracing/formatters.py` — output formatting
5. Test: `core/tests/tracing/test_decision_trace.py`, `core/tests/e2e/test_decision_trace_spine.py`

### Rendering
1. `core/rendering/llm_renderer.py` — `LlmRenderRequest`, `render_llm()`
2. `core/rendering/availability_renderer.py` — availability render request builder
3. `core/rendering/booking_confirmation_renderer.py` — booking confirmation
4. `core/orchestration/orchestrator.py` — `_inject_*` functions (where text is attached)
5. Test: `core/tests/rendering/test_availability_renderer.py`

### Intent Policy / Execution Steps
1. `core/config/intent_policy.yaml` — single source of truth
2. `core/policy/intent_policy.py` — loader (cached)
3. `core/orchestration/orchestrator.py` lines ~840–870 — how steps are matched and executed

### NLU / Intent Resolution
1. `nlu/pipeline.py` — production NLU pipeline
2. `nlu/stages/` — stage1 intent + stage2 slot extractors
3. `core/planning/planner/intent_resolution.py` — `resolve_effective_intent()`
4. `core/orchestration/nlu/luma_response_processor.py` — response interpretation
5. `core/session/confirmation_gate.py` — confirmation turn classification

---

## 3. Engineering Invariants

These rules must not be violated. Each has been observed directly in code.

### Core Is Planning-Only
Core NEVER calls booking/availability APIs directly. It only returns planning outcomes. Execution is handled by `handle_message()` after `plan_message()` returns.

### missing_slots Must Be Computed Before Planning
`effective_response["missing_slots"]` must be a non-None list before `process_luma_response()` is called. Verified by assertion at `turn_planner.py:1687-1693`. `missing_slots=[]` is valid — it means all required slots are satisfied, not an error.

### Merge Is Gated on Durable Intent, Not Status
`should_merge_session_context()` returns True only when `is_durable_intent(session.intent_name)` is True AND `session_reset_occurred` is False. Session `status` is irrelevant to merge eligibility.

### Fingerprint = Search Criteria Only
The fingerprint hash includes only: `organization_id`, `service_id`, `date`, `start_date`, `date_range`, `location`, `staff`, `resource`. Time selection, proposals, page index, and presented availability must never affect the fingerprint.

### Non-Durable Intents Never Reach Planning
Intents where `durable=false` in `intent_policy.yaml` short-circuit before `build_decision_plan()`. They are not persisted to session and do not update `session.intent_name` or `session.slots`.

### Session Facts Are Visible for the Entire Turn
`session_state` is NEVER set to None even when `session_reset_occurred=True`. The session must remain visible for capability reconciliation (e.g., `payment_satisfied`). The old pattern `session_state = None` was explicitly removed.

### intent_policy.yaml Is the Only Source of Truth
All intent durable flags, execution steps, required slots, and action modes must come from `core/config/intent_policy.yaml` loaded via `core/policy/intent_policy.py`. Do not hardcode intent names or slot lists in planners or executors.

### Rendering Is Best-Effort
All `_inject_*` calls in `orchestrator.py` catch exceptions and silently skip rendering. Rendering failure must never fail a turn. Do not add `raise` in rendering code.

### Planner Does Not Execute
`plan_turn()` and `plan_message()` are pure planning — no client calls for availability or booking. Execution clients are initialized only when `planning_only=False`.

### Slot Merge Order
`effective_turn_slots = {**session_slots_for_merge, **raw_luma_slots}` — current-turn Luma slots always override session slots. This invariant is guarded by assertion in test/debug mode.

---

## 4. Testing Strategy

Run the smallest test first. Expand only if it fails.

| Task | Smallest test first | Broader regression |
|---|---|---|
| Fingerprint change | `pytest core/tests/orchestration/test_availability_fingerprint.py -q` | `pytest core/tests/orchestration/ -q` |
| Planning / business facts | `pytest core/tests/planning/test_business_fact_registry.py -q` | `pytest core/tests/planning/ -q` |
| Session merge | `pytest core/tests/session/test_merge_eligibility.py -q` | `pytest core/tests/session/ -q` |
| Confirmation gate | `pytest core/tests/session/test_confirmation_gate.py -q` | `pytest core/tests/session/ -q` |
| Availability rendering | `pytest core/tests/rendering/test_availability_renderer.py -q` | `pytest core/tests/rendering/ -q` |
| Booking execution | `pytest core/tests/execution/test_booking_execution.py -q` | `pytest core/tests/execution/ -q` |
| Pagination | `pytest core/tests/orchestration/test_availability_pagination.py -q` | `pytest core/tests/orchestration/ -q` |
| Decision trace | `pytest core/tests/tracing/test_decision_trace.py -q` | `pytest core/tests/tracing/ -q` |
| Orchestrator flow | `pytest core/tests/orchestration/test_orchestrator_flow.py -q` | `pytest core/tests/orchestration/ -q` |
| Full turn (E2E) | `pytest core/tests/e2e/test_booking.py -q` | `pytest core/tests/e2e/ -q` |
| NLU pipeline | `pytest nlu/tests/ -q` | `pytest nlu/ -q` |
| Smoke (real NLU) | `pytest core/tests/smoke/ -q` | N/A — integration only; needs `LUMA_BASE_URL` → nlu |
| Session invalidation | `pytest core/tests/session/test_invalidation.py -q` | `pytest core/tests/session/ -q` |

Do not run `pytest .` or `pytest core/` without a specific reason. It hits smoke tests and a live NLU service.

---

## 5. Repository Exploration Guidance

### Files to Inspect First

Before searching, check if the file is listed in section 2 (Fast Navigation) for the task type. If it is, read only those files. Do not grep or glob unless the symbol is not found in the named files.

### Prefer Known Entry Points

For any orchestration change: start at `core/orchestration/orchestrator.py:handle_message()`.
For any planning change: start at `core/planning/planner/turn_planner.py:plan_turn()`.
For any NLU change: start at `nlu/pipeline.py:NLUPipeline.run()`.
For any policy change: start at `core/config/intent_policy.yaml`.

### Follow Imports Selectively

Follow an import only if the symbol you need is not visible from the file you're already reading. The docstrings and function signatures in entry-point files are usually sufficient.

### Do Not Search Across Unrelated Packages

Changes to `core/session/` do not require reading `nlu/`. Changes to `nlu/stages/` do not require reading `core/orchestration/`. The packages are loosely coupled; the NLU result is a plain dict handed to core at a single boundary.

### Avoid Broad Pattern Matching

Do not `grep -r` for a symbol across the entire repo unless it is not found in the files named in section 3. Prefer reading the entry-point file directly.

---

## 6. Areas to Avoid

Do not inspect these unless the task explicitly targets them.

| Path | Reason |
|---|---|
| `**/__pycache__/` | Python bytecode cache |
| `**/*.pyc` | Compiled bytecode |
| `*.zip`, `src.zip`, `src (2).zip`, `src (3).zip` | Archived snapshots |
| `out.out`, `../out.out`, `../out2.out` | Logged output files |
| `luma/` | Legacy NLU package; not production. Read only when comparing migration history. |
| `luma/perf/` | Performance profiling scripts, not business logic |
| `*.egg-info/` | Package metadata |
| `.pytest_cache/` | Pytest internal cache |
| `core/tests/scenarios/` | YAML scenario fixtures — read only when debugging specific scenario failures |
| `core/tests/harness/` | Test harness infrastructure — read only when the harness itself is broken |
| `extensions/capabilities/` | Payment/capability adapters — read only for payment flow tasks |

---

*This handbook describes the codebase as of the `nlu` branch. Update it when architectural boundaries change.*
