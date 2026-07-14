# Package Cleanup – Phase 1: Dead Scaffolding Removal

## Work Completed

### Files Deleted

| File | Reason |
|---|---|
| `core/orchestration/actions/__init__.py` | Empty package marker; package had zero callers |
| `core/orchestration/actions/booking.py` | Placeholder stub (`execute_booking`); 0 callers anywhere in the codebase |
| `core/orchestration/actions/cancellation.py` | Placeholder stub (`execute_cancellation`); 0 callers |
| `core/orchestration/actions/modification.py` | Placeholder stub (`execute_modification`); 0 callers |
| `core/nlu/__init__.py` | Package marker; package had zero import callers |
| `core/nlu/luma_interpreter.py` | Phase 1 stub (`LumaInterpreter`); only self-references; labeled "Phase 1" throughout |
| `core/orchestration/execution/availability.py` | Placeholder stub (`search_availability` returns hardcoded `[]`); never called by any caller — tests imported but never invoked the function |
| `core/orchestration/execution/booking.py` | Placeholder stub (`execute_booking` returns `"PLACEHOLDER_CODE"`); never called by any caller |
| `core/orchestration/execution/confirmation.py` | Placeholder stub (`confirm_booking`); never called by any caller |

**Total: 9 files deleted across 3 directories.**

`core/orchestration/actions/` and `core/nlu/` directories were removed entirely.

---

### Files Modified

| File | Change |
|---|---|
| `core/tests/execution/test_availability_execution.py` | Removed dead import: `from core.orchestration.execution.availability import search_availability` |
| `core/tests/execution/test_booking_execution.py` | Removed dead import: `from core.orchestration.execution.booking import execute_booking` |
| `core/tests/execution/test_confirmation_execution.py` | Removed dead import: `from core.orchestration.execution.confirmation import confirm_booking` |
| `core/orchestration/execution/__init__.py` | Removed stale docstring listing the three deleted sub-modules |

---

## Evidence Summary

### core/orchestration/actions/

- `grep -r "orchestration\.actions"` → 0 results across the entire codebase
- `grep -r "execute_booking\|execute_cancellation\|execute_modification"` → results only in the action files themselves
- All three action files were pure stubs with no real logic ("Placeholder: actual logic would go here")

### core/nlu/

- `grep -r "from core\.nlu\|import core\.nlu"` → only `logging.getLogger("core.nlu").setLevel(logging.ERROR)` in `orchestrator.py` — a logger namespace string, not a module import
- `grep -r "luma_interpreter\|LumaInterpreter"` → only self-references in `core/nlu/luma_interpreter.py`
- `luma_interpreter.py` was labeled "Phase 1 architectural boundary" with "Phase 2 will move context-building... into this class" — Phase 2 was completed without this class; it was bypassed entirely

### core/orchestration/execution/{availability,booking,confirmation}.py

- `grep -r "search_availability"` → only in `test_availability_execution.py` import line (never called in test body)
- `grep -r "execute_booking"` → only in `test_booking_execution.py` import line (never called in test body)
- `grep -r "confirm_booking"` → only in `test_confirmation_execution.py` import line (never called in test body)
- All three test files called mock clients directly — the wrapper functions were structurally unreachable
- All three wrapper functions returned hardcoded placeholder data, not real execution results

---

## What Was Not Changed

As instructed:
- `ConversationEngine` — untouched
- `TurnPlanner` — untouched
- `WorkflowRouter`, `ActionRunner`, `dispatcher` — untouched
- `core/orchestration/execution/clients/` — untouched (production clients)
- `core/orchestration/execution/dispatcher.py` — untouched
- All session, policy, routing, rendering, planning modules — untouched
- No behaviour changes; no API changes; no architectural changes

---

## Post-Cleanup State

`core/orchestration/execution/` now contains only live modules:

```
core/orchestration/execution/
    __init__.py           (updated docstring)
    dispatcher.py         (production dispatcher — live)
    clients/
        availability_client.py   (production — live)
        booking_client.py        (production — live)
```

---

## Architectural Observations

- The three `orchestration/actions/` stubs were written with the same placeholder pattern as the `orchestration/execution/` stubs — both groups date to the same early scaffolding phase and were never promoted to real implementations.
- `LumaInterpreter` was explicitly a Phase 1 boundary stub. Production NLU calls go through `LumaClient` directly in `TurnPlanner`. The planned Phase 2 migration that would have populated this class was implemented differently.
- The logger suppression `logging.getLogger("core.nlu").setLevel(logging.ERROR)` in `orchestrator.py` (line ~89) continues to work correctly — it suppresses the `core.nlu` logger namespace regardless of whether the package exists.

---

## Recommended Future Improvements

These were observed during Phase 1 and should be tracked separately:

1. **`orchestrator.py` logger suppression** — `logging.getLogger("core.nlu").setLevel(logging.ERROR)` suppresses a namespace that no longer exists. It is harmless but misleading. Could be removed in a separate cleanup pass.

2. **`core/orchestration/execution/__init__.py`** — The docstring previously listed sub-modules that are now deleted. Updated to reflect actual contents. No further action needed.

3. **`core/tests/execution/` test docstrings** — The three test file module docstrings still reference the deleted wrapper functions by name (e.g., "Tests for `core.orchestration.execution.availability.search_availability()`"). These are accurate for what the tests *were* written to test, but the functions are gone. Consider updating to reflect that these tests verify `AvailabilityClient` / `BookingClient` interfaces directly.
