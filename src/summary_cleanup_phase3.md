# Package Cleanup – Phase 3: slot_contract.py Removal

## Files Created

| File | Purpose |
|---|---|
| `core/session/slot_operations.py` | New session-owned module; receives the three unique-logic functions from slot_contract.py |

`slot_operations.py` contains:
- `filter_slots_by_domain(slots, intent_name, planning_only=False)`
- `filter_collected_slots_for_intent(collected_slots, old_intent, new_intent)`
- `promote_slots_for_intent(raw_slots, intent_name, context)`

All three function bodies are copied verbatim from `slot_contract.py`. No logic changes.

---

## Files Modified

### `core/session/effective_slots.py`

Three changes:

| Location | Before | After |
|---|---|---|
| Lazy import in `_compute_effective_collected_slots_internal` (~line 41) | `from core.orchestration.api.slot_contract import (filter_slots_by_domain, get_required_slots_for_intent,)` | `from core.session.slot_operations import filter_slots_by_domain` + `from core.planning.orchestration.missing_slots import get_planning_required_slots_for_intent` |
| Call site (~line 57) | `set(get_required_slots_for_intent(effective_intent))` | `set(get_planning_required_slots_for_intent(effective_intent))` |
| Lazy import in `_compute_effective_collected_slots` (~line 218) | `from core.orchestration.api.slot_contract import promote_slots_for_intent` | `from core.session.slot_operations import promote_slots_for_intent` |

### `core/session/merge.py`

Two changes:

| Location | Before | After |
|---|---|---|
| Lazy import block (~line 1215) | `# Import central slot contract functions` + `from core.orchestration.api.slot_contract import (filter_collected_slots_for_intent, promote_slots_for_intent,)` | `from core.session.slot_operations import (filter_collected_slots_for_intent, promote_slots_for_intent,)` |
| Lazy import (~line 1713) | `# Still in slot_contract` + `from core.orchestration.api.slot_contract import filter_slots_by_domain` | `from core.session.slot_operations import filter_slots_by_domain` |

The "Import central slot contract functions" header comment and the "Still in slot_contract" inline comment were both removed — both were stale references to the deleted module.

---

## File Deleted

| File | Reason |
|---|---|
| `core/orchestration/api/slot_contract.py` | All callers migrated; zero remaining references confirmed by grep |

---

## Import Migration Summary

| Function | Old import path | New import path | Callers updated |
|---|---|---|---|
| `filter_slots_by_domain` | `core.orchestration.api.slot_contract` | `core.session.slot_operations` | `effective_slots.py`, `merge.py` |
| `filter_collected_slots_for_intent` | `core.orchestration.api.slot_contract` | `core.session.slot_operations` | `merge.py` |
| `promote_slots_for_intent` | `core.orchestration.api.slot_contract` | `core.session.slot_operations` | `effective_slots.py`, `merge.py` |
| `get_required_slots_for_intent` | `core.orchestration.api.slot_contract` (local legacy wrapper) | `get_planning_required_slots_for_intent` from `core.planning.orchestration.missing_slots` | `effective_slots.py` |
| `compute_missing_slots` (re-export) | `core.orchestration.api.slot_contract` (dead — zero callers via shim) | No change needed | — |
| `get_planning_required_slots_for_intent` (re-export) | `core.orchestration.api.slot_contract` (dead — zero callers via shim) | No change needed | — |

---

## Behavioural Equivalence Assessment

### `filter_slots_by_domain`, `filter_collected_slots_for_intent`, `promote_slots_for_intent`

Function bodies copied verbatim. Behaviour is identical by construction. The only difference is the module path used in import statements.

### `get_required_slots_for_intent` → `get_planning_required_slots_for_intent`

Both functions ultimately read from the same source: `_load_unified_policy()[intent_name]["planning"]["required_slots"]` in `intent_policy.yaml`.

Differences:
- The canonical function sorts the result; the legacy wrapper did not.
- The canonical function tries `core.policy.intent_policy.get_planning_required_slots` first, with a fallback to `load_planning_policy()`. The legacy wrapper used only the `load_planning_policy()` fallback path.

Neither difference affects behaviour at the call site: `effective_slots.py` immediately wraps the result in `set(...)`, making sort order irrelevant. The canonical function's primary path (`intent_policy.yaml` direct) is the correct path per the architectural constitution ("intent_policy.yaml is the only source of truth"). The substitution is strictly an improvement.

### Verification

Post-change grep confirms zero remaining references to `slot_contract`:
```
grep -rn "slot_contract" --include="*.py" src/
# Returns: exit code 1 (no matches)
```

---

## Remaining Architectural Debt

1. **`core/orchestration/api/` package is now smaller but still mixed.** After removing `session_merge.py` (Phase 2) and `slot_contract.py` (Phase 3), the remaining contents of `core/orchestration/api/` are:
   - `capability_boundary.py` — legitimate API-layer boundary (owned)
   - `main.py` — FastAPI router registration (owned)
   - `message.py` — HTTP entry point (owned)
   - `slot_contract.py` — now deleted
   - `session_merge.py` — now deleted
   - `turn_state.py` — turn state finalization; callers within orchestration layer. May be a candidate for future review.

2. **`core/orchestration/api/slot_contract.py` self-declared "DEPRECATED" for planning-policy boundary violations.** The `slot_operations.py` module retains hardcoded intent-name strings (`"CREATE_APPOINTMENT"`, `"CREATE_RESERVATION"`, `"MODIFY_BOOKING"`, `"CANCEL_BOOKING"`) and hardcoded slot-name lists in `filter_slots_by_domain` and `filter_collected_slots_for_intent`. These lists are not derived from `intent_policy.yaml`. Per the architectural constitution ("intent_policy.yaml is the only source of truth"), these hardcoded tables are technical debt. Resolving them would require extending `intent_policy.yaml` with domain slot declarations and promotion rules — a separate, larger task.

3. **`core/session/slot_operations.py` docstring-level debt.** The three functions retain references to specific intent names inline. A future "policy-driven slot domain" task should replace these tables with YAML-driven lookups. Phase 3 deliberately did not do this — it was a move, not a rewrite.

4. **`core/orchestration/nlu/luma_response_processor.py`** still uses `get_planning_required_slots_for_intent` aliased as `get_required_slots_for_intent` in four call sites (lines 460, 1228, 1399). This aliasing is for local readability — it's not a shim — but it adds mild confusion. Could be cleaned up in a future naming pass.
