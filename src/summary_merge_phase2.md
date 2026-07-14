# Merge Decomposition – Phase 2: Business Stage Extraction

## Source Modified

`core/session/merge.py`

---

## Objective

Transform `merge_luma_with_session()` from a 1 735-line monolith into a readable coordinator of named business-stage helpers. Zero behavioural changes.

---

## Helpers Extracted (Phase 2)

| Phase | Helper | Signature | Purpose |
|---|---|---|---|
| 2A | `_MergeContext` | `@dataclass` | Context bundle passed to all Phase-2 helpers |
| 2A | `_rehydrate_confirmation_state` | `(merged, session_state) → None` | Restore confirmation_state from session for multi-turn confirm flows |
| 2A | `_finalize_effective_slots_and_trace` | Signature updated to `(ctx, effective_intent, durable_slots) → None` | Accepts `_MergeContext` instead of 7 loose parameters |
| 2B | `_extract_raw_luma_slots` | `(ctx) → Dict` | Promote Luma facts → slots; reconcile service_id; snapshot `_raw_luma_slots` |
| 2B | `_extract_semantic_slots` | `(ctx, luma_slots) → None` | Extract date/time from entities, semantic trace, booking object into `luma_slots` |
| 2C | `_merge_slots_additive` | `(ctx, luma_slots) → (merged_slots, merged_intent_name)` | Additive session+Luma merge; proposals; booking re-injection; slot-durability assertion |
| 2C | `_handle_informational_turn_and_effective_intent` | `(ctx, merged_slots, merged_intent_name, raw_luma_slots) → (early_return, effective_intent, merged_slots)` | Intent-change filtering; informational-turn early return; effective_intent resolution |
| 2D | `_promote_and_bind` | `(ctx, merged_slots, effective_intent) → (durable_slots, datetime_bound_this_turn)` | Slot promotion; time-selection binding; domain filtering; temporal-slot stripping |
| 2D | `_compute_missing_slots` | `(ctx, effective_intent, effective_slots, luma_response) → None` | Planner-based missing_slots computation; all invariant checks; MODIFY_BOOKING issues fallback |
| 2E | `_enforce_intent_authority` | `(ctx) → None` | STEP 1.5 early-slot check + STEP 1 intent-authority enforcement (includes `raise AssertionError`) |

---

## Coordinator Shape (After Phase 2)

```python
def merge_luma_with_session(luma_response, session_state, planning_only=False):
    # Preamble: user_id, initial_session_slots, [SESSION_BEFORE] log
    merged = luma_response.copy()

    # session_intent, session_status, luma_intent_name extraction
    _rehydrate_confirmation_state(merged, session_state)
    # session_intent_name computation
    ctx = _MergeContext(...)

    _enforce_intent_authority(ctx)            # STEP 1.5 + STEP 1

    _merge_facts(merged, session_state)       # STEP 2

    raw_luma_slots = _extract_raw_luma_slots(ctx)     # STEP 3
    _carry_forward_time_constraint(merged, session_state)
    luma_slots = raw_luma_slots

    _extract_semantic_slots(ctx, luma_slots)  # semantic extraction

    merged_slots, merged_intent_name = _merge_slots_additive(ctx, luma_slots)  # STEP 3 merge

    early_return, effective_intent, merged_slots = \
        _handle_informational_turn_and_effective_intent(ctx, merged_slots, merged_intent_name, raw_luma_slots)
    if early_return:
        return _finalize_merged_luma_response(merged, luma_response)

    durable_slots_for_persist, _ = _promote_and_bind(ctx, merged_slots, effective_intent)  # STEP 4.1
    effective_slots_for_computation = durable_slots_for_persist.copy()

    _compute_missing_slots(ctx, effective_intent, effective_slots_for_computation, luma_response)  # STEP 4.2

    _finalize_effective_slots_and_trace(ctx, effective_intent, durable_slots_for_persist)
    return _finalize_merged_luma_response(merged, luma_response)
```

Coordinator length: **143 lines** (including docstring and preamble).  
Helper-call section: **~20 lines** of named operations.

---

## Line Count

| Metric | Before Phase 2 | After Phase 2 | Delta |
|---|---|---|---|
| Total file lines | ~2 100 | 2 095 | −5 |
| `merge_luma_with_session` body | 1 735 | ~110 (excl. docstring/preamble) | **−1 625** |
| Module-level private helpers | 7 | 14 + 1 dataclass | +8 |

---

## Behavioural Changes

None. Every extracted helper is a verbatim lift of the coordinator block it replaces:

- All `logger.*` calls preserved at identical execution order
- All `assert` statements preserved (including `assert False` in slot-durability and slot-satisfaction invariants)
- All `raise AssertionError(...)` and `raise Exception(...)` paths preserved
- All `try/except` blocks preserved
- All `del merged[...]` operations preserved
- Mutable accumulator pattern: `ctx.merged` and the coordinator's `merged` local are the same dict object; mutations in helpers are immediately visible in the coordinator

---

## _MergeContext Fields

```python
@dataclass
class _MergeContext:
    merged: Dict[str, Any]              # mutable accumulator — same object as coordinator's merged
    session_state: Dict[str, Any]       # read-only prior state
    planning_only: bool
    session_intent: Any                 # str | dict | None (raw from session)
    session_intent_name: Optional[str]  # normalized string form
    session_status: str
    luma_intent_name: str
    initial_session_slots: Dict[str, Any]  # snapshot for trace
    user_id: str                        # for logging
```

---

## Extraction Notes

### Phase 2A
- `_MergeContext` introduced to eliminate `(merged, session_state, planning_only, ...)` repetition across 8 Phase-2 helpers.
- `_finalize_effective_slots_and_trace` signature reduced from 7 loose parameters to `(ctx, effective_intent, durable_slots)`.

### Phase 2B
- `_extract_raw_luma_slots` includes the service_id reconciliation block and the `[MERGE_RESULT]` log.
- `_extract_semantic_slots` handles entities, semantic_data, date_refs, time_refs, time_constraint — all passes preserved.
- `merged_intent_name` use-before-assignment bug (pre-existing, fixed in prior session) was prerequisite for this extraction.

### Phase 2C
- `_merge_slots_additive` includes the AMBIGUOUS_SERVICE invalidation, NEW_BOOKING_REQUEST detection, context-date lifting (FIX 77), and slot-durability assertion with fail-safe restore.
- `_handle_informational_turn_and_effective_intent` includes `_detect_modification_context` call (must precede the informational-turn early return check). Returns `merged_slots` as the third element so the coordinator uses the possibly-filtered dict in `_promote_and_bind`.

### Phase 2D
- `_promote_and_bind` includes the `try_bind_offered_time_selection` path, `apply_post_bind_time_resolution`, stale-pending-confirmation invalidation, domain filtering, and temporal-slot stripping.
- `_compute_missing_slots` carries the HARD INVARIANT CHECK (test/debug only), the `assert False` slot-durability violation, and the `raise Exception(error_msg)` for dropped-slot detection — all preserved verbatim.

### Phase 2E
- `_enforce_intent_authority` is the last extraction (highest risk: contains `raise AssertionError`, four distinct intent-assignment branches, early-slot-check import).
- STEP 1.5 (`has_extracted_slots` computation) is included because `luma_slots_temp` and `has_extracted_slots` are only used within that block; no return value needed.
- `session_status` accessed through `ctx.session_status` in the `[INTENT_TRACE]` log line.

---

## Recommended Next Steps

1. **Run session regression suite** — `pytest core/tests/session/ -q` to confirm zero behavioural regressions before merging.
2. **Phase 3 (optional)**: Simplify coordinator preamble — the `session_intent`, `session_status`, `luma_intent_name` extraction lines and `session_intent_name` computation (currently ~40 lines before `ctx`) could be collapsed into a `_build_merge_context()` factory, reducing the coordinator to a pure sequence of named calls. Propose before implementing.
3. **Coordinator docstring** — the existing docstring still refers to internal implementation details; a follow-up could align it with the new helper-based structure.
