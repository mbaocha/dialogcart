# Review – Merge Decomposition Phase 1 Readiness

## Source Reviewed

`core/session/merge.py` — post-Phase 1 state (2 100 lines total; `merge_luma_with_session` 1 735 lines).

---

## 1. Helper Signature Analysis

### Phase 1 helpers

| Helper | Parameters | Return | Notes |
|---|---|---|---|
| `_extract_date_from_luma_response` | 1 (`luma_resp`) | `Optional[str]` | Pure utility; no session, no merged |
| `_merge_facts` | 2 (`merged`, `session_state`) | `None` | Minimal; correct |
| `_carry_forward_time_constraint` | 2 (`merged`, `session_state`) | `None` | Minimal; correct |
| `_detect_modification_context` | 4 (`merged`, `raw_luma_slots`, `merged_intent_name`, `session_state`) | `None` | `merged_intent_name` is redundant — could be derived inside via `merged.get("_effective_intent")` |
| `_finalize_effective_slots_and_trace` | 7 (`merged`, `session_intent`, `session_status`, `effective_intent`, `durable_slots_for_persist`, `initial_session_slots`, `planning_only`) | `None` | Highest parameter count; all are distinct, non-redundant at this scope |

### Assessment

All five signatures are correct. The only minor redundancy is `merged_intent_name` in `_detect_modification_context`: the helper could derive it from `merged.get("_effective_intent", merged.get("intent", {}).get("name", ""))` internally, eliminating one parameter. This is a Phase 2A cleanup opportunity, not a defect — the current 4-parameter form is readable.

`_finalize_effective_slots_and_trace` at 7 parameters sits at the high end of the acceptable range. It is a candidate for the `MergeContext` optimization (see Section 3) but is not a problem now — all 7 parameters represent genuinely distinct values that must be passed from the coordinator.

---

## 2. Parameter Duplication Analysis

### Recurring groups across Phase 1 helpers

| Group | Appears in helpers |
|---|---|
| `(merged, session_state)` | `_merge_facts`, `_carry_forward_time_constraint`, `_detect_modification_context`, `_finalize_effective_slots_and_trace` (session_state implicit via session_intent/session_status) |
| `merged` alone | All 4 mutating helpers |
| `planning_only` | `_finalize_effective_slots_and_trace` only (Phase 1) |

`(merged, session_state)` is the universal pair. `merged` is the mutable accumulator; `session_state` is the immutable source-of-truth for prior state. This pattern will repeat in every Phase 2 helper.

### Projected Phase 2 parameter burden

Based on the current coordinator body, the proposed Phase 2 helpers will need:

| Proposed helper | Minimum parameters |
|---|---|
| `_extract_raw_luma_slots` | `merged`, `session_state`, `luma_intent_name` → 3 |
| `_extract_semantic_slots` | `merged`, `luma_slots`, `merged_intent_name` → 3 |
| `_merge_slots_additive` | `merged`, `session_state`, `raw_luma_slots`, `session_intent_name` → 4 |
| `_handle_informational_turn_and_effective_intent` | `merged`, `session_state`, `merged_slots`, `session_intent_name`, `merged_intent_name`, `planning_only` → 6+ |
| `_promote_and_bind` | `merged`, `session_state`, `merged_slots`, `effective_intent`, `planning_only` → 5 |
| `_compute_missing_slots` | `merged`, `session_state`, `effective_intent`, `durable_slots`, `luma_time_constraint`, `planning_only` → 6 |

The `(merged, session_state, ..., planning_only)` triple will appear in at least 4 of the 6 Phase 2 helpers. This is the key input to the `MergeContext` decision below.

---

## 3. MergeContext Recommendation

### What `MergeContext` would hold

```python
@dataclass
class _MergeContext:
    merged: Dict[str, Any]           # mutable accumulator — passed by reference
    session_state: Dict[str, Any]    # read-only prior state
    planning_only: bool              # turn-level flag
    session_intent: Any              # raw session intent (str|dict|None)
    session_intent_name: Optional[str]  # normalized string
    session_status: str
    luma_intent_name: str
    initial_session_slots: Dict[str, Any]  # snapshot for trace
    user_id: str                     # for logging
```

### Analysis

**Arguments for introducing `MergeContext`:**
- Eliminates the `(merged, session_state, planning_only)` repetition across 4+ Phase 2 helpers.
- Reduces `_finalize_effective_slots_and_trace` from 7 parameters to 3 (`ctx`, `effective_intent`, `durable_slots_for_persist`).
- Makes the coordinator's call sites read as sequential transformations on one context object rather than argument-threading.
- Prevents future helpers from accumulating implicit coupling through `merged` side-effects alone.

**Arguments against:**
- All Phase 1 helpers already exist with the current signatures; retrofitting them adds a Phase that's pure rename.
- `merged` is a mutable dict that all helpers write to — a dataclass wrapper over a mutable dict does not change the mutation semantics. The dataclass is not enforcing immutability.
- Python `@dataclass` is not zero-cost: it introduces an import and a class definition that must be maintained.
- The current phase has 5 small helpers; `MergeContext` pays off only when there are 6+ helpers sharing the same parameter group. That threshold is reached in Phase 2.

### Recommendation: **Recommended — introduce in Phase 2A, before large extractions**

**Not retroactively.** The 2-parameter Phase 1 helpers (`_merge_facts`, `_carry_forward_time_constraint`) do not need `MergeContext` — their signatures are clean as-is. Do not change them.

**Timing:** Phase 2A should be: define `_MergeContext`, build it at the top of `merge_luma_with_session`, and update only `_finalize_effective_slots_and_trace` (which already has 7 parameters) to use it. This is the one Phase 1 helper where the benefit is immediate and measurable. All new Phase 2 helpers then accept `_MergeContext` from the start rather than being retrofitted again in Phase 3.

**Form:** A plain `@dataclass(frozen=False)` with all fields typed. Do not use a `NamedTuple` (immutable) since `merged` will be mutated throughout. A `slots: dataclasses.field(default_factory=dict)` pattern is not needed — all fields come from `merge_luma_with_session`'s existing local variables with no defaults.

---

## 4. Updated Phase 2 Extraction Order

The Phase 1 summary listed 8 Phase 2 helpers. Recommendations for adjustments are below, followed by the revised order.

### Helpers to merge

**Merge `_handle_informational_turn` + `_resolve_effective_intent` → `_handle_informational_turn_and_effective_intent`**

Rationale: `is_informational_intent` and `has_actionable_this_turn` are computed in the informational-turn block and consumed immediately in effective-intent resolution. They are never used after effective_intent is set. Splitting them creates a 2-parameter return tuple from the first helper, which flows directly into a 1-function second helper. The coherence gain from splitting is minimal; the parameter-passing complexity is real.

**Keep `_extract_raw_luma_slots` separate from `_extract_semantic_slots`**

The Phase 1 summary proposed `_merge_facts_and_raw_slots` as a merged helper. This was a regression: `_merge_facts` is already extracted cleanly, and facts-merge has no logical coupling to raw-slot extraction. `_extract_raw_luma_slots` (the service_id reconciliation block, ~90 lines) stands alone.

### Helpers to split

**Split confirmation rehydration from `_merge_intent_and_confirmation`**

The current Phase 1 summary treats the entire STEP 1 block (intent parse + early slot check + intent authority + confirmation rehydration) as one future helper. This is too coarse. Confirmation rehydration (3 lines of logic) is trivial to extract and has no coupling to the 80-line intent enforcement block. Extract it as `_rehydrate_confirmation_state` in Phase 2A. Defer intent-authority enforcement (`_enforce_intent_authority`) to Phase 2C as the highest-risk extraction.

### Helpers to reorder

The proposed Phase 1 summary had `_merge_intent_and_confirmation` last (Phase 2 priority 8). Intent authority is the riskiest block in the function (contains `AssertionError raise`, four distinct branches, used by every downstream stage). Last is correct for intent enforcement, but confirmation rehydration should be extracted early (Phase 2A).

### Revised Phase 2 extraction order

| Phase | Step | Helper | Lines to extract | Risk | Pre-condition |
|---|---|---|---|---|---|
| 2A | 1 | Define `_MergeContext` dataclass + update `_finalize_effective_slots_and_trace` to use it | ~20 | Low | None |
| 2A | 2 | `_rehydrate_confirmation_state(ctx)` | ~5 | Trivial | `_MergeContext` exists |
| 2B | 3 | `_extract_raw_luma_slots(ctx)` → `raw_luma_slots: Dict` | ~90 | Medium | Fix `merged_intent_name` ordering issue first (see Section 5) |
| 2B | 4 | `_extract_semantic_slots(ctx, raw_luma_slots)` → `None` (mutates `raw_luma_slots`) | ~300 | Medium-High | Step 3 complete |
| 2C | 5 | `_merge_slots_additive(ctx, raw_luma_slots)` → `merged_slots: Dict` | ~250 | Medium | Steps 3-4 complete |
| 2C | 6 | `_handle_informational_turn_and_effective_intent(ctx, merged_slots, raw_luma_slots)` → `(early_return: Optional[Dict], effective_intent: str)` | ~175 | Medium | Step 5 complete |
| 2D | 7 | `_promote_and_bind(ctx, merged_slots, effective_intent)` → `durable_slots: Dict` | ~150 | Medium | Step 6 complete |
| 2D | 8 | `_compute_missing_slots(ctx, effective_intent, durable_slots)` → `missing_slots: List` | ~265 | High | Step 7 complete |
| 2E | 9 | `_enforce_intent_authority(ctx)` (from STEP 1 intent block) | ~90 | Medium | All other stages stable |

After all 9 steps, `merge_luma_with_session` will be a ~50-line coordinator reading as sequential named operations.

---

## 5. Classification: The `merged_intent_name` Issue

### Location

Line 791 (post-Phase 1 file):
```python
if merged_intent_name != "CREATE_APPOINTMENT":
```
inside the `entities["time"]` extraction block.

`merged_intent_name` is defined at line 814:
```python
merged_intent_name = merged.get("_effective_intent", ...)
```

### Reachability analysis

The guard at line 791 is reached when **both** of the following hold:
1. `"time" in entities` — the Luma response `entities` dict contains a `"time"` key
2. `"time" not in luma_slots` — the time has not already been captured via `facts_to_slots()` or `merge_promoted_luma_slots()`

The `entities` dict in a Luma response is populated by the NLU entity-extraction stage. Luma documentation and surrounding code (the `entities.date` extraction above at line 780) confirm that `entities` is a real field, not a hypothetical one. The condition `"time" in entities` is therefore reachable whenever Luma resolves a time expression through its entity layer rather than its facts layer.

Whether `"time"` is simultaneously absent from `luma_slots` at that point depends on whether `facts_to_slots()` (line 608) already promoted `entities.time` through a `facts.time` path. If Luma populates `entities.time` without a corresponding `facts.time`, the slot would not have been promoted, the condition would be true, and line 791 would be executed.

### Verdict: **Latent bug**

This is not unreachable. The condition is satisfiable by a Luma response that includes `entities.time` without a corresponding `facts.time`. If reached, Python raises `NameError: name 'merged_intent_name' is not defined` because the local variable has not been assigned yet in the function's execution at that point.

The bug is *latent* rather than *confirmed active* because:
- It has not been triggered in the test suite (no reported crash on this path)
- Luma responses in current production use may consistently populate `time` through `facts` rather than `entities`
- The test scenarios may not exercise the specific Luma response shape that triggers it

The bug is **not** intentional ordering: the comment at line 811–813 (`# Extract intent name early for reservation contract enforcement`) and the surrounding code structure indicate `merged_intent_name` was intended to be defined before the entities block, not after. The definition was placed after the entities block, likely as a patch that did not account for the earlier use site.

### Phase 2 implication

**The `merged_intent_name` definition must be moved before the entities extraction block before `_extract_semantic_slots` is extracted.** If the semantic extraction block is extracted without fixing this first, the helper function will carry the use-before-definition into a new scope where it becomes a less visible `NameError`. This fix is the mandatory prerequisite for Phase 2B step 3.

The fix is a single-line move (move the `merged_intent_name = merged.get(...)` assignment from line 814 to before line 777). It is behaviorally neutral for all currently passing code paths.

---

## 6. Coordinator Business Story

The current coordinator (`merge_luma_with_session`) still reads as dense inline logic because 1 735 of 1 735 remaining lines are unchanged business code. The five Phase 1 extractions are correct but do not yet produce a readable coordinator — they removed the smallest, most self-contained stages. This is expected: Phase 1 was explicitly scoped to low-risk extractions only.

After Phase 2 is complete, the coordinator will read as:

```python
ctx = _MergeContext(...)
_rehydrate_confirmation_state(ctx)
_enforce_intent_authority(ctx)  # Phase 2E
raw_luma_slots = _extract_raw_luma_slots(ctx)
_extract_semantic_slots(ctx, raw_luma_slots)
merged_slots = _merge_slots_additive(ctx, raw_luma_slots)
early_return, effective_intent = _handle_informational_turn_and_effective_intent(ctx, merged_slots, raw_luma_slots)
if early_return is not None:
    return _finalize_merged_luma_response(early_return, luma_response)
durable_slots = _promote_and_bind(ctx, merged_slots, effective_intent)
missing_slots = _compute_missing_slots(ctx, effective_intent, durable_slots)
ctx.merged["missing_slots"] = missing_slots
_finalize_effective_slots_and_trace(ctx, effective_intent, durable_slots)
return _finalize_merged_luma_response(ctx.merged, luma_response)
```

This is a coherent business story in ~15 lines. The current Phase 1 extraction plan leads directly to this target coordinator.

---

## Go / No-Go Recommendation for Phase 2

**Go — with one mandatory pre-condition.**

### Required before Phase 2B

Move the `merged_intent_name = merged.get("_effective_intent", ...)` assignment (currently line 814) to before the `entities` extraction block (before line 777). This is a single-line move that eliminates the latent `NameError` before it is locked into a helper function boundary.

### All Phase 1 extractions are structurally correct

- No signature errors
- No missing parameters
- Execution order preserved
- All assertions, logger calls, and exception raises present
- The backward-compatibility alias at the end of the file is untouched

### Phase 2 proceed as revised order in Section 4

Start with Phase 2A (`_MergeContext` + `_rehydrate_confirmation_state`) before any business-stage extraction. This is low-risk, eliminates parameter repetition early, and sets the foundation for all subsequent Phase 2 helpers.
