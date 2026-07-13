# Session Architecture Notes

Deferred questions that do not need to be resolved until a concrete maintenance
issue arises. Do not act on these without a clear, observed problem.

---

## Branch 3 store-read priority in `apply_create_appointment_extensions()`

**File:** `core/session/appointment_extensions.py`
**Function:** `apply_create_appointment_extensions()`, else-branch (~line 304)

### What the code does

Branch 3 fires when a CREATE_APPOINTMENT turn is not a fresh search and not
a browse. It reads `last_execution_result`, `presented_availability`, and
`availability_presentation` from the session store first, then falls back to
`previous_session_state`.

### The open question

For the primary case that hits Branch 3 — a subsequent non-search turn while a
booking is in progress — `previous_session_state` and the store contain identical
values: both reflect the end-of-turn `save_session()` from the previous search
turn.

The store is checked first because it can carry mid-turn writes from
`AvailabilityWorkflow.process_search_result()` (via `_persist_to_session()`).
But for non-search turns, `process_search_result()` never runs, so the store
holds no fresher data than `previous_session_state`.

The question: **would swapping the priority (`previous_session_state` first,
store second) in Branch 3 reduce the implicit store-coupling without
introducing any new transport mechanism?**

### Why it was not resolved

Swapping the priority is safe for the normal case but the store-first order was
not accidental — it guards against `previous_session_state` being filtered to
None by the API layer status check before `handle_message()` is called. In that
edge case (status not in NEEDS_CLARIFICATION / AWAITING_CAPABILITY), the store
retains the availability state that `previous_session_state` would be missing.

Confirming whether that edge case actually occurs in production with
CREATE_APPOINTMENT and a READY or other status requires end-to-end tracing, not
static analysis. The priority swap is not worth the risk without that evidence.

### Condition for revisiting

Revisit only if one of the following is observed:

- A test or production incident shows the store read returning stale data while
  `previous_session_state` had the correct value.
- A refactor of the API-layer session filter changes when `previous_session_state`
  can be None, making the guard moot.
- The `_persist_to_session()` mechanism is removed or replaced, eliminating the
  mid-turn write that makes the store relevant at all.

Do not pursue a broader session-ownership refactor (SessionMutationSet,
AvailabilityArtifacts struct, single-durable-write) until a concrete maintenance
issue — not a theoretical one — justifies the scope.
