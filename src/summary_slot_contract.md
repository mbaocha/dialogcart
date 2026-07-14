# Investigation – slot_contract.py Architecture Review

## 1. Function Inventory

`core/orchestration/api/slot_contract.py` contains six named exports.

### Re-exports (no unique logic)

| Function | Source | Status |
|---|---|---|
| `compute_missing_slots` | `core.planning.orchestration.missing_slots` | Re-export only |
| `get_planning_required_slots_for_intent` | `core.planning.orchestration.missing_slots` | Re-export only |

### Local implementations

| Function | Lines | Description |
|---|---|---|
| `get_required_slots_for_intent(intent_name)` | 28–40 | Legacy wrapper around `load_planning_policy()` → `intent_policy.get(intent_name, {}).get("required_slots", [])` |
| `filter_slots_by_domain(slots, intent_name, planning_only=False)` | 43–140 | Domain slot isolation — strips slots invalid for the intent's domain (service vs reservation). Skips filtering when `planning_only=True`. |
| `filter_collected_slots_for_intent(collected_slots, old_intent, new_intent)` | 143–215 | Filters accumulated slots on intent change; drops slots not in the new intent's slot universe. |
| `promote_slots_for_intent(raw_slots, intent_name, context)` | 218–312 | Additive slot promotion — derives new slots from existing ones (e.g. `date_range` → `start_date`/`end_date`) without removing any input slots. |

---

## 2. Caller Inventory

### Production callers

| Function | Caller | Import path used | Call sites |
|---|---|---|---|
| `compute_missing_slots` | `turn_planner.py` | `core.planning.orchestration.missing_slots` (direct) | Lines 445–446, 569, 701, 1522–1530 |
| `get_planning_required_slots_for_intent` | `turn_state.py` | `core.planning.orchestration.missing_slots` (direct) | Line 194–195 |
| `get_planning_required_slots_for_intent` | `luma_response_processor.py` | `core.planning.orchestration.missing_slots` (direct) | Lines 460, 777, 1228, 1399 |
| `get_planning_required_slots_for_intent` | `merge.py` | `core.planning.orchestration.missing_slots` (direct) | Line 1891 |
| `get_planning_required_slots_for_intent` | `persist.py` | `core.planning.orchestration.missing_slots` (direct) | Lines 675, 692 |
| `get_planning_required_slots_for_intent` | `stage_checks.py` | `core.planning.orchestration.missing_slots` (direct) | Line 404 |
| `get_required_slots_for_intent` | `effective_slots.py` | `core.orchestration.api.slot_contract` **(via shim)** | Lines 41–43, 57 |
| `filter_slots_by_domain` | `effective_slots.py` | `core.orchestration.api.slot_contract` **(via shim)** | Lines 41–42, 49 |
| `filter_slots_by_domain` | `merge.py` | `core.orchestration.api.slot_contract` **(via shim)** | Lines 1713–1715 (comment: "Still in slot_contract") |
| `filter_collected_slots_for_intent` | `merge.py` | `core.orchestration.api.slot_contract` **(via shim)** | Lines 1215–1216, 1285 |
| `promote_slots_for_intent` | `effective_slots.py` | `core.orchestration.api.slot_contract` **(via shim)** | Lines 218–220 |
| `promote_slots_for_intent` | `merge.py` | `core.orchestration.api.slot_contract` **(via shim)** | Lines 1217, 1588 |

### Test callers

**Zero test files** import from `slot_contract.py` directly. The one test that calls `compute_missing_slots` (`core/tests/planning/test_awaiting_slot_planning.py:12`) imports from `core.planning.orchestration.missing_slots` directly.

### Dynamic callers

None. Grep for `"slot_contract"` (string), `importlib`, and `__import__` returns zero results.

---

## 3. Behavioural Ownership

| Function | Correct architectural owner | Actual current owner | Misplaced? |
|---|---|---|---|
| `compute_missing_slots` | Planning (`core.planning.orchestration`) | Planning (canonical module) | No — shim only, canonical is correct |
| `get_planning_required_slots_for_intent` | Planning (`core.planning.orchestration`) | Planning (canonical module) | No — shim only, canonical is correct |
| `get_required_slots_for_intent` | Policy (`core.policy.intent_policy` via `core.planning.orchestration.missing_slots`) | API layer (slot_contract) | **Yes** |
| `filter_slots_by_domain` | Session (`core.session`) | API layer (slot_contract) | **Yes** |
| `filter_collected_slots_for_intent` | Session (`core.session`) | API layer (slot_contract) | **Yes** |
| `promote_slots_for_intent` | Session (`core.session`) | API layer (slot_contract) | **Yes** |

All three unique-logic functions are called exclusively by `core/session/effective_slots.py` and `core/session/merge.py`, yet they live in `core/orchestration/api/`. This is an inversion: session-layer logic depends upward on an orchestration-API-layer module.

---

## 4. Duplicate Analysis

### `get_required_slots_for_intent` vs `get_planning_required_slots_for_intent`

These two functions serve the same purpose. They diverge in implementation path:

| Aspect | `get_required_slots_for_intent` (slot_contract) | `get_planning_required_slots_for_intent` (canonical) |
|---|---|---|
| Primary source | `load_planning_policy()` → legacy format | `get_planning_required_slots()` → `intent_policy.yaml` direct |
| Fallback | None | Falls back to `load_planning_policy()` on exception |
| Return order | Unsorted (preserves YAML order) | `sorted()` |
| Error handling | Returns `[]` on bad `required_slots` type | Has exception fallback |
| Accepts context params | No | Yes (`collected_slots`, `modification_context`) |

**Both paths ultimately read the same data**: `_load_unified_policy()[intent_name]["planning"]["required_slots"]` — `load_planning_policy()` is itself marked `DEPRECATED` and now delegates to `_load_unified_policy()` internally.

**Behavioural difference at call sites**: `effective_slots.py` converts the result to a `set` immediately (`set(get_required_slots_for_intent(effective_intent))`), making the sort-order difference irrelevant. The canonical version's fallback path and error handling are strictly better.

### `filter_slots_by_domain`, `filter_collected_slots_for_intent`, `promote_slots_for_intent`

No duplicate implementations exist anywhere in the codebase. These are unique business logic bodies.

Evidence:
- `grep -r "filter_slots_by_domain"` → only in `slot_contract.py` (definition) and `effective_slots.py`, `merge.py` (callers)
- `grep -r "filter_collected_slots_for_intent"` → only in `slot_contract.py` (definition) and `merge.py` (caller)
- `grep -r "promote_slots_for_intent"` → only in `slot_contract.py` (definition), `effective_slots.py`, `merge.py` (callers)

---

## 5. Canonical Replacements

| Function | Current shim path | Canonical replacement |
|---|---|---|
| `compute_missing_slots` | `core.orchestration.api.slot_contract` | `core.planning.orchestration.missing_slots.compute_missing_slots` (already used by all callers) |
| `get_planning_required_slots_for_intent` | `core.orchestration.api.slot_contract` | `core.planning.orchestration.missing_slots.get_planning_required_slots_for_intent` (already used by all callers) |
| `get_required_slots_for_intent` | `core.orchestration.api.slot_contract` | `core.planning.orchestration.missing_slots.get_planning_required_slots_for_intent` (1 call site update in `effective_slots.py`) |
| `filter_slots_by_domain` | `core.orchestration.api.slot_contract` | New: `core.session.slot_domain.filter_slots_by_domain` |
| `filter_collected_slots_for_intent` | `core.orchestration.api.slot_contract` | New: `core.session.slot_domain.filter_collected_slots_for_intent` |
| `promote_slots_for_intent` | `core.orchestration.api.slot_contract` | New: `core.session.slot_domain.promote_slots_for_intent` |

---

## 6. Migration Complexity

| Step | Complexity | Justification |
|---|---|---|
| Remove dead re-exports (`compute_missing_slots`, `get_planning_required_slots_for_intent`) | **Trivial** | Zero callers via shim. Delete 4 lines from slot_contract.py. |
| Replace `get_required_slots_for_intent` in `effective_slots.py` | **Low** | 1 import site, 1 call site. Behaviorally equivalent substitution (same data, callers use `set()` so sort order irrelevant). |
| Create `core/session/slot_domain.py` | **Low** | Copy 3 function bodies verbatim from slot_contract.py. No logic changes. |
| Update `effective_slots.py` (2 import sites, 2 call sites) | **Low** | Mechanical path substitution. No logic changes. |
| Update `merge.py` (3 import sites, 3 call sites) | **Low** | Mechanical path substitution. No logic changes. |
| Delete `slot_contract.py` | **Trivial** | After all callers migrated, zero remaining references. |

**Overall migration complexity: Low**

The only non-trivial risk is the `get_required_slots_for_intent` → `get_planning_required_slots_for_intent` substitution. This is safe because:
- Same YAML source for both paths
- `effective_slots.py` wraps the result in `set()` immediately — unsorted vs sorted output is irrelevant
- The canonical version is strictly more robust (has fallback, better error handling)

---

## 7. Recommended End State

**Delete `slot_contract.py` entirely.**

Move the three unique-logic functions to a new `core/session/slot_domain.py`. This corrects the ownership inversion: session-layer logic that operates on session slots belongs in the session package, not in the orchestration API layer.

The two dead re-exports (`compute_missing_slots`, `get_planning_required_slots_for_intent`) require no migration — all callers already import from the canonical module.

---

## 8. Safe Implementation Plan

### Step 1 — Create `core/session/slot_domain.py`

Copy the three function bodies verbatim from `slot_contract.py`:

```
core/session/slot_domain.py
  filter_slots_by_domain(slots, intent_name, planning_only=False)
  filter_collected_slots_for_intent(collected_slots, old_intent, new_intent)
  promote_slots_for_intent(raw_slots, intent_name, context)
```

Module docstring: `"""Domain-specific slot manipulation for session merge and effective-slot computation."""`

No logic changes. Copy the function bodies character-for-character to preserve identical behaviour.

### Step 2 — Update `core/session/effective_slots.py`

**Change 1** — In the lazy import at line ~41:
```python
# Before
from core.orchestration.api.slot_contract import (
    filter_slots_by_domain,
    get_required_slots_for_intent,
)

# After
from core.session.slot_domain import filter_slots_by_domain
from core.planning.orchestration.missing_slots import get_planning_required_slots_for_intent
```

**Change 2** — At line ~57, update the call site:
```python
# Before
required_slots_set = set(get_required_slots_for_intent(effective_intent))

# After
required_slots_set = set(get_planning_required_slots_for_intent(effective_intent))
```

**Change 3** — In the lazy import at line ~218:
```python
# Before
from core.orchestration.api.slot_contract import promote_slots_for_intent

# After
from core.session.slot_domain import promote_slots_for_intent
```

No other changes to `effective_slots.py`.

### Step 3 — Update `core/session/merge.py`

**Change 1** — In the lazy import at line ~1215:
```python
# Before
from core.orchestration.api.slot_contract import (
    filter_collected_slots_for_intent,
    promote_slots_for_intent,
)

# After
from core.session.slot_domain import (
    filter_collected_slots_for_intent,
    promote_slots_for_intent,
)
```

**Change 2** — In the lazy import at line ~1713:
```python
# Before
# Still in slot_contract
from core.orchestration.api.slot_contract import filter_slots_by_domain

# After
from core.session.slot_domain import filter_slots_by_domain
```

Remove the "Still in slot_contract" comment — it no longer applies.

No other changes to `merge.py`.

### Step 4 — Delete `core/orchestration/api/slot_contract.py`

Verify zero remaining callers before deletion:
```bash
grep -r "slot_contract" --include="*.py" src/
# Expected: zero results
```

### Verification

After all four steps, the following must hold:
- No file imports from `core.orchestration.api.slot_contract`
- `core/session/effective_slots.py` imports `filter_slots_by_domain` and `promote_slots_for_intent` from `core.session.slot_domain`
- `core/session/effective_slots.py` uses `get_planning_required_slots_for_intent` from `core.planning.orchestration.missing_slots`
- `core/session/merge.py` imports all three domain functions from `core.session.slot_domain`
- `core/session/slot_domain.py` exists with all three function bodies

---

## Summary Table

| Function | Type | Callers via shim | Recommendation |
|---|---|---|---|
| `compute_missing_slots` | Dead re-export | 0 | Delete with the file |
| `get_planning_required_slots_for_intent` | Dead re-export | 0 | Delete with the file |
| `get_required_slots_for_intent` | Legacy wrapper | 1 (`effective_slots.py`) | Replace with canonical; delete |
| `filter_slots_by_domain` | Unique logic | 2 (`effective_slots.py`, `merge.py`) | Move to `core/session/slot_domain.py` |
| `filter_collected_slots_for_intent` | Unique logic | 1 (`merge.py`) | Move to `core/session/slot_domain.py` |
| `promote_slots_for_intent` | Unique logic | 2 (`effective_slots.py`, `merge.py`) | Move to `core/session/slot_domain.py` |
