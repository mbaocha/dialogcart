# Merge Decomposition – Phase 1: Low-Risk Extractions

## Source Modified

`core/session/merge.py`

---

## Helpers Extracted

Five helpers were extracted from `merge_luma_with_session()` and placed at module level between `_finalize_merged_luma_response` and `merge_luma_with_session`. All five are private (`_` prefix). None are exported from the module.

### 1. `_extract_date_from_luma_response(luma_resp)` → `Optional[str]`

**Extracted from:** nested function definition at lines 464–593 (original), lifted to module level.  
**Body moved:** 130 lines → now at module level lines 73–172.  
**Change inside `merge_luma_with_session`:** `def _extract_date_from_luma_response(...)` definition removed; call at `_extract_date_from_luma_response(merged)` preserved unchanged.  
**Risk:** Trivial. The nested function had no closure over outer-scope locals — it received `luma_resp` explicitly and used only `logger` (already module-level). The call signature and name are identical.

---

### 2. `_merge_facts(merged, session_state)` → `None`

**Extracted from:** STEP 2 block (original lines 299–315).  
**Replaced with:** `_merge_facts(merged, session_state)` — one call line.  
**Lines removed from `merge_luma_with_session`:** 16 lines → 1 line.  
**Behaviour:** Reads `session_state.get("facts", {})` and `merged.get("facts", {})`, merges `{**session_facts, **luma_facts}`, writes `merged["facts"]`. Identical to original.

---

### 3. `_carry_forward_time_constraint(merged, session_state)` → `None`

**Extracted from:** STEP 2.5 block (original lines 423–434).  
**Replaced with:** `_carry_forward_time_constraint(merged, session_state)` — one call line.  
**Lines removed from `merge_luma_with_session`:** 12 lines → 1 line.  
**Behaviour:** When `session_state` has a `time_constraint` and `merged` does not, writes `merged["time_constraint"] = session_time_constraint` with a `logger.debug`. Identical to original.

---

### 4. `_detect_modification_context(merged, raw_luma_slots, merged_intent_name, session_state)` → `None`

**Extracted from:** STEP 3.4.1 block (original lines 1323–1376).  
**Replaced with:** `_detect_modification_context(merged, raw_luma_slots, merged_intent_name, session_state)` — one call line.  
**Lines removed from `merge_luma_with_session`:** 54 lines → 1 line.  
**Behaviour:** For `MODIFY_BOOKING` and `MODIFY_RESERVATION`, builds a `modification_context` dict from `raw_luma_slots` keys, writes to `merged["_modification_context"]`. Falls back to `session_state.get("_modification_context")` if neither intent matches. Identical to original.  
**Note on downstream reads:** The secondary fallback at stage 4.1.1 (inside `merge_luma_with_session` lines ~1603–1614 in the updated file) re-reads `modification_context` from `merged.get("_modification_context")` rather than the local variable, so this extraction has no downstream impact on that block.

---

### 5. `_finalize_effective_slots_and_trace(merged, session_intent, session_status, effective_intent, durable_slots_for_persist, initial_session_slots, planning_only)` → `None`

**Extracted from:** final block (original lines 2001–2053).  
**Replaced with:** 9-line call block.  
**Lines removed from `merge_luma_with_session`:** 53 lines → 9 lines.  
**Behaviour:** Calls `_compute_effective_collected_slots_internal`, writes `merged["_effective_collected_slots"]`; asserts `merged.intent.name == session_intent_name` for concrete session intents; emits `emit_merge_slot_trace` (wrapped in `try/except ImportError`). Identical to original.  
**Parameters passed:** `session_intent` (raw value, may be str/dict/None) and `session_status` (str) are passed explicitly from `merge_luma_with_session`'s local scope where they were captured at lines 124 and 128.

---

## Line Count

| Metric | Before | After | Delta |
|---|---|---|---|
| Total file lines | 2 059 | 2 100 | +41 |
| `merge_luma_with_session` lines | 1 987 | 1 735 | **−252** |
| Module-level private helpers | 2 | 7 | +5 |

Net file growth (+41) is due to docstrings and preserved comments added to the new helpers. The function body itself shrank by 252 lines.

---

## Execution Order

Unchanged. Each call replaces the exact block it was extracted from, at the same position in the function body. The `_finalize_effective_slots_and_trace` call is placed immediately before the `return _finalize_merged_luma_response(...)` line, matching the original code order.

---

## Issues Encountered

**Pre-existing bug (not introduced, not fixed):** `merged_intent_name` is referenced at the `entities["time"]` extraction guard (original line 656, updated file ~line 790) before the variable is defined at original line 679 (updated ~line 813). This would raise `NameError` at runtime if `"time"` is in `entities` and not in `luma_slots`. The bug predates Phase 1 and was not introduced or modified by this extraction. Documenting it here for Phase 2 planning.

---

## Behaviour Changes

None. All logic is preserved verbatim. All assertions, `logger.*` calls, and exception raises are present in the same execution order.

---

## Remaining Extraction Work (Phase 2+)

Ordered by risk (lowest first):

| Priority | Helper | Lines to extract | Risk | Location in current file |
|---|---|---|---|---|
| 1 | `_merge_facts_and_raw_slots` (facts merge + raw slot extraction combined) | ~155 | Medium | STEP 2 + STEP 3, ~lines 591–700 |
| 2 | `_extract_semantic_slots` | ~320 | Medium-High | Semantic data extraction + two date_refs passes |
| 3 | `_merge_slots_additive` | ~200 | Medium | STEP 3 additive merge + context dates + proposals |
| 4 | `_handle_informational_turn` | ~135 | Medium | STEP 3.5 early-return path |
| 5 | `_resolve_effective_intent` | ~40 | Low | Post-informational effective_intent resolution |
| 6 | `_promote_and_bind` | ~120 | Medium | STEP 4.1 + 4.1.5 + datetime binding |
| 7 | `_compute_missing_slots` | ~265 | High | STEP 4.2 — contains `assert False` + invariant checks |
| 8 | `_merge_intent_and_confirmation` | ~185 | Medium | STEP 1 + STEP 1.5 + confirmation rehydration + intent authority |

After all phases, `merge_luma_with_session` is estimated at ~50–60 lines (coordinator only).
