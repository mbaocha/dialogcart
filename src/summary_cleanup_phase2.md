# Package Cleanup – Phase 2: Compatibility Shim Removal

## Deleted Shims

| File | Reason |
|---|---|
| `core/orchestration/api/session_merge.py` | Pure re-export shim; all callers migrated to canonical modules |
| `core/session/session_manager.py` | Phase 1 facade class with zero callers anywhere in the codebase |

---

## Migrated Callers

### From `core.orchestration.api.session_merge` → canonical `core.session.*`

**`build_session_state_from_outcome`** — canonical: `core.session.persist`

| File | Change |
|---|---|
| `core/orchestration/api/message.py` | Removed dead re-export (`# noqa: F401 (re-exported for compat)`) — nobody imported it from `message.py` |
| `core/tests/execution/test_core_capability_payment_e2e.py` | Import migrated |
| `core/tests/orchestration/test_availability_pagination_flow.py` | Import migrated |
| `core/tests/planning/test_availability_booking_continuation.py` | Import migrated |
| `core/tests/planning/test_planning.py` | Import migrated |
| `core/tests/planning/test_proposal_temporal_triggers_search.py` | Import migrated |
| `core/tests/planning/test_reject_booking_confirmation.py` | Import migrated |
| `core/tests/rendering/test_adaptive_clarification_rendering_e2e.py` | Import migrated |
| `core/tests/session/test_availability_browse.py` | Import migrated |
| `core/tests/session/test_awaiting_slot_e2e.py` | Import migrated |
| `core/tests/session/test_commit_consumes_confirmation.py` | Import migrated |
| `core/tests/session/test_session.py` | Import migrated |
| `core/tests/session/test_slot_retry_tracking_e2e.py` | Import migrated |

**`_compute_effective_collected_slots`** — canonical: `core.session.effective_slots`
**`merge_luma_with_session`** — canonical: `core.session.merge`

| File | Change |
|---|---|
| `core/planning/orchestration/turn_planner.py` | Lazy import (line ~1305) migrated from shim to `core.session.effective_slots` and `core.session.merge` |

---

### From orchestrator re-exports → canonical modules

`availability_pagination.py` was the only production caller of the orchestrator re-export shim. Three symbols migrated:

| Symbol | Old import | New import |
|---|---|---|
| `_structured_context_from_decision` | `core.orchestration.orchestrator` | `core.rendering.response_renderer` |
| `_persist_to_session` | `core.orchestration.orchestrator` | `core.orchestration.session_ops` |
| `build_outcome_from_decision` | `core.orchestration.orchestrator` | `core.engine.outcome_builder` |

---

## Orchestrator Cleanup

After migrating `availability_pagination.py`, the re-export block in `orchestrator.py` had zero remaining callers. Removed:

- The `# Symbols implemented in neutral modules; re-exported here...` header block (5 import statements, 22 lines)
- 10 comment-only stub lines documenting the removed re-exports (`# _build_planning_outcome — implemented in ...`, etc.)

All other orchestrator content is unchanged. `handle_message`, `plan_message`, `_return_with_execution_spine`, `_handle_non_core_intent`, and `_invoke_workflow_after_execute` are real functions defined in orchestrator and were not touched.

---

## Retained Shims (with justification)

### `core/orchestration/api/slot_contract.py`

Self-declared `DEPRECATED` — re-exports `compute_missing_slots` and `get_planning_required_slots_for_intent` from `core.planning.orchestration.missing_slots`, and contains `get_required_slots_for_intent`, `filter_slots_by_domain`, `filter_collected_slots_for_intent`, `promote_slots_for_intent` as local implementations.

**Retained because:** Not in Phase 2 scope. Caller investigation required before removal. The local functions (`filter_slots_by_domain`, `filter_collected_slots_for_intent`, `promote_slots_for_intent`) are non-trivial domain logic that must be traced before determining safety of removal.

### `core/orchestration/orchestrator.py` itself

Retained in full as a production module. It owns `handle_message` (compatibility wrapper for tests and legacy callers), `plan_message` (planning-only entry), `_return_with_execution_spine`, `_handle_non_core_intent`, and `_invoke_workflow_after_execute`. These are not shims — they are real functions with active callers (35+ test files, `conversation_engine.py`, `app.py`).

---

## Confirmation: Production Behaviour Unchanged

All changes are import path migrations only. No function signatures, return values, or call sequences were modified.

- `build_session_state_from_outcome` is the same function — previously re-exported from `core.session.persist` via the shim; callers now import it from `core.session.persist` directly.
- `_compute_effective_collected_slots` and `merge_luma_with_session` — same functions from `core.session.effective_slots` and `core.session.merge`; previously proxied via shim.
- `_structured_context_from_decision`, `_persist_to_session`, `build_outcome_from_decision` in `availability_pagination.py` — same functions from their canonical modules; previously proxied via orchestrator.
- `SessionManager` in `core/session/session_manager.py` — had zero callers; deletion has no runtime effect.
- The orchestrator re-export block — had zero callers after `availability_pagination.py` was migrated; removal has no runtime effect.

The docstring mentions of `core.orchestration.api.session_merge` in `core/session/effective_slots.py`, `merge.py`, and `persist.py` (historical notes about where the code was extracted from) were left as-is; they do not affect behaviour.

---

## Post-Cleanup Import Map for Key Symbols

| Symbol | Canonical import |
|---|---|
| `build_session_state_from_outcome` | `from core.session.persist import build_session_state_from_outcome` |
| `merge_luma_with_session` | `from core.session.merge import merge_luma_with_session` |
| `_compute_effective_collected_slots` | `from core.session.effective_slots import _compute_effective_collected_slots` |
| `_structured_context_from_decision` | `from core.rendering.response_renderer import _structured_context_from_decision` |
| `_persist_to_session` | `from core.orchestration.session_ops import _persist_to_session` |
| `build_outcome_from_decision` | `from core.engine.outcome_builder import build_outcome_from_decision` |

---

## Architectural Observations

- `core/session/__init__.py` already re-exports all five symbols from `core.session.*` sub-modules. Tests could equally import from `core.session` directly; using the sub-module path was chosen for precision and consistency with how the canonical modules already import each other.
- The `core.orchestration.session_merge` logger suppression in `orchestrator.py` (line ~77) references a logger namespace that no longer has any code emitting to it. It is harmless but can be cleaned up in a separate pass.
- `core/orchestration/api/slot_contract.py` is the remaining declared-deprecated file in `core/orchestration/api/`. It is a candidate for Phase 3.
