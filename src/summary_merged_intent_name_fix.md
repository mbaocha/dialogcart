# Defect Fix – `merged_intent_name` Use-Before-Assignment

## Confirmed Failure Path

**File:** `core/session/merge.py` — `merge_luma_with_session()`

**Failure type:** `NameError: name 'merged_intent_name' is not defined`

**Trigger conditions (all three must hold simultaneously):**

1. `"time" in merged.get("entities", {})` — the Luma response carries a time value in its `entities` field (rather than in `facts` or `slots`)
2. `"time" not in luma_slots` — the time was NOT already captured via `facts_to_slots()` / `merge_promoted_luma_slots()` during the raw slot extraction step (STEP 3). This is true when Luma places time exclusively in `entities` with no corresponding `facts.time` path.
3. Execution reaches line 791 (pre-fix numbering): `if merged_intent_name != "CREATE_APPOINTMENT":` — which is always reached when conditions 1 and 2 hold.

Python raises `NameError` at that point because `merged_intent_name` was not yet assigned in the function's local scope. The assignment `merged_intent_name = merged.get("_effective_intent", ...)` was placed **after** the entities block (old line 814), 23 lines below the first use (old line 791).

**This path is reachable in production.** The entities field is a real NLU output field (the date extraction block above it at line 780 reads `entities["date"]` with the same pattern). When Luma resolves time through its entity layer independently of its facts layer, both conditions hold and the crash occurs.

---

## Source Change

**File:** `core/session/merge.py`

**Change:** Moved the `merged_intent_name` assignment from after the entities block to before it. Removed the now-redundant original placement.

### Before (old lines 776–821)

```python
    # Also check entities for date/time (Luma may provide date directly in entities)
    entities = merged.get("entities", {})
    if isinstance(entities, dict):
        ...
        if "time" in entities and "time" not in luma_slots:
            if merged_intent_name != "CREATE_APPOINTMENT":   # ← NameError here
                ...

    ...

    # Extract intent name early for reservation contract enforcement
    # CRITICAL: Use effective_intent (computed EARLY after UNKNOWN override)...
    merged_intent_name = merged.get(                          # ← defined here (too late)
        "_effective_intent", ...
    )
```

### After (new lines 776–821)

```python
    # Extract intent name before entity/semantic extraction.
    # CRITICAL: Use effective_intent (computed EARLY after UNKNOWN override)...
    # This ensures effective_intent is used consistently throughout, including the entity-time
    # guard below which requires merged_intent_name to already be defined.
    merged_intent_name = merged.get(                          # ← now defined first
        "_effective_intent", ...
    )

    # Also check entities for date/time (Luma may provide date directly in entities)
    entities = merged.get("entities", {})
    if isinstance(entities, dict):
        ...
        if "time" in entities and "time" not in luma_slots:
            if merged_intent_name != "CREATE_APPOINTMENT":   # ← safe: already defined
                ...

    ...

    # (duplicate assignment removed)
```

### Duplicate assignment analysis

Two further assignments to `merged_intent_name` remain in the function body:

| Line (post-fix) | Expression | Retained? | Reason |
|---|---|---|---|
| ~1196 | `merged.get("intent", {}).get("name", "")` (no `_effective_intent`) | **Retained** | Different expression; not identical to the moved assignment |
| ~1266 | `merged.get("_effective_intent", merged.get("intent", {}).get("name", ""))` | **Retained** | Identical expression; but `apply_invalidation(NEW_BOOKING_REQUEST)` between the two assignments could in principle modify `merged` — removing it would require auditing `apply_invalidation`'s full side effects, which is outside the scope of this fix |

No decomposition was introduced in this change.

---

## Regression Test Added

**File:** `core/tests/session/test_merge_entity_time_regression.py` (new file)

**Test class:** `TestEntitiesTimeGuardNoNameError` — 4 test cases:

| Test | Intent | Entities time | luma_slots time | Expected result |
|---|---|---|---|---|
| `test_create_appointment_completes_without_nameerror` | CREATE_APPOINTMENT | `"14:00"` | absent | Completes; `"time"` NOT in slots (guard blocks extraction for CA) |
| `test_non_appointment_intent_extracts_entity_time` | MODIFY_BOOKING | `"15:30"` | absent | Completes; `"time" == "15:30"` in slots (guard allows extraction) |
| `test_unknown_intent_no_entity_time_in_slots` | UNKNOWN | `"09:00"` | absent | Completes; `"time" == "09:00"` in slots |
| `test_luma_time_in_facts_already_prevents_entity_path` | CREATE_APPOINTMENT | `"16:00"` | present (via facts) | Completes; entity path not reached |

The first three tests directly reproduce the failure path (conditions 1 and 2 both hold). Before the fix all three would raise `NameError`. After the fix all four pass.

---

## Tests Executed and Results

### Regression tests (new file)

```
pytest core/tests/session/test_merge_entity_time_regression.py -v
```

```
PASSED  test_create_appointment_completes_without_nameerror
PASSED  test_non_appointment_intent_extracts_entity_time
PASSED  test_unknown_intent_no_entity_time_in_slots
PASSED  test_luma_time_in_facts_already_prevents_entity_path

4 passed in 0.22s
```

### Directly relevant session merge tests

```
pytest core/tests/session/test_service_stickiness.py \
       core/tests/session/test_merge_eligibility.py \
       core/tests/session/test_confirmation_gate.py \
       core/tests/session/test_invalidation.py -v
```

```
46 passed in 0.20s
```

**Total: 50/50 tests passed. Zero failures. Zero regressions.**

---

## No Decomposition Included

This change is limited to:
- Moving one assignment block (11 lines) earlier in the function
- Removing one identical assignment block (11 lines) from its original position
- Adding one test file (71 lines)

No helper extraction, no function decomposition, no signature changes, no package restructuring. The function body and its public API (`merge_luma_with_session`, `merge_session_with_luma_response`) are unchanged in every respect except the ordering of the `merged_intent_name` assignment.
