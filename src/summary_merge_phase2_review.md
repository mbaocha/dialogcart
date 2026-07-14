# Review – Merge Decomposition Phase 2 Equivalence

## Source Reviewed

`core/session/merge.py` — post-Phase-2 coordinator + helpers.

**Baseline for comparison:** git `HEAD` monolith of `merge_luma_with_session` (pre Phase 1/2), with Phase 1 helpers and the `merged_intent_name` use-before-assignment fix treated as approved prerequisites (see `summary_merge_phase1.md`, `summary_merged_intent_name_fix.md`).

**Method:** static inspection only — no pytest, no imports of application code, no runtime validation.

---

## 1. Original-block → helper mapping

| Original stage (HEAD / decomposition map) | New owner | Notes |
|---|---|---|
| Preamble (`user_id`, `initial_session_slots`, `[SESSION_BEFORE]`) | Coordinator (inline) | Unchanged |
| Session/Luma intent field parse | Coordinator (inline) | Unchanged |
| Confirmation rehydrate | `_rehydrate_confirmation_state` (Phase 2A / Phase 1) | Same position before intent authority |
| Build shared params | `_MergeContext` + coordinator ctor | New bundle; same values |
| STEP 1.5 early slot check + STEP 1 intent authority | `_enforce_intent_authority` | Includes dead `has_extracted_slots` (already unused in HEAD) |
| STEP 2 facts merge | `_merge_facts` (Phase 1) | Unchanged call |
| STEP 3 raw Luma slots + service_id reconcile + `[MERGE_RESULT]` | `_extract_raw_luma_slots` | See log-order note in §4 |
| STEP 2.5 time_constraint carry-forward | `_carry_forward_time_constraint` (Phase 1) | Still after raw extraction |
| Semantic / entities / booking date-time extraction | `_extract_semantic_slots` | Includes prerequisite `merged_intent_name` assignment-before-entities |
| STEP 3 additive merge, FIX77, proposals, durability assert, booking reinject | `_merge_slots_additive` | Returns `(merged_slots, merged_intent_name)` |
| STEP 3.6 intent-change filter | `_handle_informational_turn_and_effective_intent` | First section of helper |
| STEP 3.4.1 modification context | `_detect_modification_context` via that helper | Still before informational early return |
| STEP 3.5 informational early return | same helper → `(True, "", merged_slots)` | Coordinator finalizes |
| Effective-intent resolve + non-core override | same helper → `(False, effective_intent, merged_slots)` | Writes `merged["_effective_intent"]` |
| STEP 4.1 context merge + promote | `_promote_and_bind` | |
| CREATE_APPOINTMENT bind / invalidation | `_promote_and_bind` | BOOKING_REVISION → TIME_REBOUND / post-bind → UNBOUND_PROPOSAL |
| STEP 4.1.5 domain filter + temporal strip | `_promote_and_bind` | Returns `(durable_slots, datetime_bound_this_turn)` |
| STEP 4.2 missing_slots + invariants | `_compute_missing_slots` | Writes `merged["missing_slots"]`; clears force-recompute flag |
| Effective collected slots + intent assert + merge trace | `_finalize_effective_slots_and_trace` | Uses `ctx` instead of 7 loose params |
| Return finalize | Coordinator | `_finalize_merged_luma_response` on both paths |

Import relocation (concurrent cleanup, not Phase-2 logic): `filter_*` / `promote_slots_for_intent` now come from `core.session.slot_operations` instead of `core.orchestration.api.slot_contract`. AST comparison of the three functions against HEAD `slot_contract.py`: **identical**.

---

## 2. Execution-order verification

Coordinator call order:

1. preamble → rehydrate → `_MergeContext`
2. `_enforce_intent_authority`
3. `_merge_facts`
4. `_extract_raw_luma_slots`
5. `_carry_forward_time_constraint`
6. `luma_slots = raw_luma_slots`
7. `_extract_semantic_slots`
8. `_merge_slots_additive`
9. `_handle_informational_turn_and_effective_intent` → possible early return
10. `_promote_and_bind`
11. `effective_slots_for_computation = durable_slots_for_persist.copy()`
12. `_compute_missing_slots`
13. `_finalize_effective_slots_and_trace`
14. `_finalize_merged_luma_response`

Matches HEAD stage order for all state-mutating steps.

**Special-case checks**

| Concern | Verdict |
|---|---|
| Informational-turn early return | Helper mutates `merged` (slots, proposals, missing_slots, `_effective_collected_slots`) then returns `early_return=True`; coordinator returns `_finalize_merged_luma_response(merged, …)`. Same mutations, same finalize, skips promote/missing/finalize-trace. |
| `merged_slots` after intent filter | Helper does `merged_slots = filter_collected_slots_for_intent(...)` and returns that dict as third tuple element; coordinator rebinds `merged_slots` before `_promote_and_bind`. Required — filtering replaces the dict. |
| `_effective_intent` | Written in `_enforce_intent_authority`; may be overridden in informational helper after early-return gate; promote/missing use coordinator’s `effective_intent`. Order preserved. |
| Datetime bind / invalidation | Inside `_promote_and_bind`, order preserved: revision invalidation → `try_bind_offered_time_selection` → TIME_REBOUND → else post-bind → else UNBOUND_PROPOSAL → domain filter → strip temporal. |
| Missing-slot invariants | HARD_INVARIANT (`raise Exception`), list/None asserts, `assert False` satisfaction check, MODIFY_BOOKING issues path, `_normalize_modify_booking_missing_slots`, force-flag delete — all present in `_compute_missing_slots` in the same relative order. |
| Modification detect before informational return | `_detect_modification_context` still called before the early-return gate. |

---

## 3. Mutation-flow verification (`_MergeContext`)

- `ctx.merged` is the same object as the coordinator’s `merged` (`luma_response.copy()`).
- Helpers that take `ctx` mutate via `ctx.merged[...]` (or a local `merged = ctx.merged` alias). Mutations are visible to later helpers and to both return paths.
- `session_state`, `planning_only`, `session_intent*`, `luma_intent_name`, `initial_session_slots`, `user_id` are read through `ctx` equivalently to former locals.
- `_finalize_effective_slots_and_trace(ctx, …)` correctly uses `ctx.session_intent`, `ctx.session_status`, `ctx.planning_only`, `ctx.initial_session_slots`.
- Local-only carriers still returned where needed: `raw_luma_slots`, `merged_slots`, `merged_intent_name`, `effective_intent`, `durable_slots_for_persist`.
- After `_promote_and_bind`, HEAD kept updating a local `merged_slots` alias to match `promoted_slots` / durable slots. That alias is no longer needed outside the helper; durable slots are returned explicitly. Equivalent for downstream use.

---

## 4. Non-mechanical differences

| # | Difference | Behavioural impact |
|---|---|---|
| 1 | Informational path returns `(early_return, effective_intent, merged_slots)` instead of returning the finalized response directly | None — coordinator performs the same finalize |
| 2 | `[MERGE_RESULT]` log now runs inside `_extract_raw_luma_slots`, **before** `_carry_forward_time_constraint`; HEAD logged MERGE_RESULT **after** carry-forward | None on returned state; only relative log order of MERGE_RESULT vs time_constraint debug |
| 3 | `_promote_and_bind` omits unused HEAD locals `merged_session_slots = merged_slots.copy()` and post-bind `merged_slots = …` alias updates | None — those locals were unused / internalized |
| 4 | Coordinator binds `datetime_bound_this_turn` from `_promote_and_bind` but never reads it (strip already uses it inside the helper) | None — dead local |
| 5 | Comment / docstring trimming; type annotation `previous_missing_slots: list = []` | None |
| 6 | Slot helpers imported from `slot_operations` (package cleanup) | None — AST-identical to HEAD `slot_contract` functions |
| 7 | `merged_intent_name` assigned before entities in `_extract_semantic_slots` | Intentional pre-Phase-2 bugfix vs raw HEAD; not a Phase-2 regression |
| 8 | Intent-change block still re-derives `session_intent_name` from `session_intent` with the looser `""`/`None` formula (same as HEAD shadowing) | Pre-existing; falsy checks behave the same |

No assertion, raise, or control-flow condition was found dropped or reordered in a state-affecting way.

---

## 5. Risks / defects found

| Severity | Finding |
|---|---|
| None (equivalence) | No behavioural defect attributed to Phase 2 extraction |
| Low / pre-existing | `has_extracted_slots` and unused `merge_promoted_luma_slots` import in `_enforce_intent_authority` remain dead (already dead in HEAD) |
| Low / cosmetic | Unused coordinator binding of `datetime_bound_this_turn` |
| Observability only | MERGE_RESULT vs time_constraint log ordering swap (§4.2) |

**Coordinator:** should **remain as-is**. No correction required for equivalence.

---

## 6. Final approval status

**APPROVED — behaviourally equivalent** to the pre-Phase-2 merge pipeline (Phase 1 + intent-name fix baseline).

Regression suite still recommended before merge (`pytest core/tests/session/`), but that is outside this static review’s scope.

---

## Checklist (requested attentions)

| Item | Status |
|---|---|
| Informational-turn early return | Preserved |
| `merged_slots` reassignment after intent filtering | Preserved (returned + rebound) |
| `_effective_intent` changes | Preserved |
| Datetime binding and invalidation ordering | Preserved |
| Missing-slot invariants | Preserved |
| Mutation through `_MergeContext` | Same object; safe |
| Coordinator correction needed? | No |
