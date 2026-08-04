# Investigation: Confirmation Revision Bugs

This report traces two production issues in Core's handling of booking revisions after Luma has correctly recognized the user utterance.

No code fixes were made and no tests were run.

## Summary

| Bug | Root cause | First incorrect state | Smallest fix owner |
| --- | --- | --- | --- |
| Bug 1: `switch to 10am` returns `READY` with no response | Confirmation handling treats an actionable time correction during pending confirmation as a generic `ANOTHER_REQUEST`, clears the just-bound time selection, and then suppresses re-entering confirmation on the same turn. | `core.planning.pipeline.stage06_confirmation.resolve_confirmation()` under `if intent_decision_gate_action == ConfirmationGateTurn.ANOTHER_REQUEST:` | `core/planning/pipeline/stage06_confirmation.py` |
| Bug 2: `show availability for flexi` searches Premium | AVAILABILITY extracts Flexi into `facts.service_id` but leaves `service_term` null; NLU post-process then reuses session `resolved_service_id` (Premium) and overwrites Flexi before Core sees the turn. | `nlu.pipeline.NLUPipeline._resolve_service_ambiguity()` → `nlu.catalog.resolve_service()` under `if not service_term:` and `if resolved_service_id:` | `nlu/pipeline.py` and `nlu/catalog.py` |

The bugs are independent code defects. They share a theme: current-turn revision evidence is recognized, then later generic session-preservation/confirmation code overrides the revised turn state.

---

## Bug 1: `switch to 10am`

Conversation:

```text
Book haircut
-> Premium
-> 9am
-> Awaiting confirmation
-> show availability for flexi
-> availability shown
-> 9:30
-> Awaiting confirmation
-> switch to 10am
-> READY
-> no response
```

### Expected final-turn semantics

The final user message is an actionable time revision while a confirmation is pending. Luma's production rules classify correction language in active booking context as `CORRECTION` and extract the exact time as facts / `time_constraint`.

The precise production response is not available from static code, but the relevant accepted shape is:

```json
{
  "intent": {"name": "CORRECTION"},
  "facts": {"times": ["10:00"]},
  "time_constraint": {"mode": "exact", "start": "10:00", "end": "10:00"}
}
```

Any non-confirm/non-reject raw intent carrying exact time evidence follows the same failing confirmation-gate path.

### Execution path

| Stage | Input | Output / mutation | Decision |
| --- | --- | --- | --- |
| HTTP entry | `POST /message`, text `switch to 10am`, persisted session with `confirmation_state=pending`, selected `9:30`, presented availability | `core.api.message.post_message()` loads raw session and calls `ConversationEngine.process_turn()` | HTTP does not interpret the turn. |
| Engine planning | Same text/session | `ConversationEngine._planning()` calls `plan_message()` -> `plan_turn()` | Planning owns NLU/merge/plan. |
| NLU invocation | Text plus Core-built `conversation_context` | `invoke_nlu_for_planning()` calls Luma and preserves `_raw_luma_response` | Luma contract requires only `intent.name`; exact time facts are valid. |
| Intent reconciliation | Raw Luma intent `CORRECTION`; session pending create booking | `reconcile_intent()` sets `raw_luma_intent=CORRECTION`; `planning_intent=CREATE_APPOINTMENT`; `turn_operation=CORRECTION`; `gate_action=ANOTHER_REQUEST` | `classify_confirmation_gate_turn()` returns `ANOTHER_REQUEST` for every pending-confirmation turn except raw `CONFIRM_ACTION` / `REJECT_ACTION`. |
| Working turn | Luma response + session | `build_working_turn()` overwrites public intent to planning intent and keeps `_raw_luma_intent=CORRECTION`, `_turn_operation=CORRECTION` | Correct so far. |
| Slot merge | Session has old bound time; Luma has exact `10:00` time evidence | `merge_luma_with_session()` extracts `time_proposal`, merges session, and calls `_promote_and_bind()` | Correct so far. |
| Temporal bind | Presented availability includes selectable offers; `time_proposal=10:00` / exact `time_constraint` | `try_bind_offered_time_selection()` can bind `10:00`, set `slots.time=10:00`, set `resolved_datetime_range`, and set `time_match_outcome=TIME_MATCH_EXACT` | Correct so far. |
| Revision detection | Payload time proposal vs session slot time | `detect_booking_revision()` reports `revision.time=True`; no service/date invalidation | Correct: a time-only revision is actionable but should not clear availability criteria. |
| Confirmation gate | `gate_action=ANOTHER_REQUEST`, `turn_operation=CORRECTION` | `resolve_confirmation()` consumes confirmation and calls `_clear_time_selection_for_availability_refinement()` | First incorrect state. The branch treats time correction like a generic other request. |
| Slot recompute | `slots_adjusted=True` | Orchestrator reruns `resolve_slot_turn_state()` and `resolve_availability()` | `time_constraint` can still satisfy missing `time`, so the turn can remain slot-complete while the confirmed binding was removed. |
| Decision plan | No missing slots; availability still ready by session/fingerprint; confirmation not pending | `build_decision_plan_from_stages()` sets `status=READY`, `awaiting=None`, and `action=None`: `availability_check_required` is false because availability is ready, and `CONFIRM_APPOINTMENT` is blocked because user confirmation is not satisfied | The plan is now a planning-only READY state with nothing to execute. |
| Execution coordinator | `status=READY`, `action=None` | `ExecutionCoordinator.resolve()` returns `path="skipped"` and `build_planning_response_from_plan(plan)` | No tool runs. |
| Rendering | Skipped execution path | `ConversationEngine._finish_gate()` returns the planning response directly | `ResponseRenderer.render_execution()` only runs on executed paths; planning response has no `text`. |
| Final response | Planning-only outcome | `success=True`, outcome `status=READY`, no top-level `text` | Observed `READY` / no response. |

### Root cause

`core.planning.pipeline.stage06_confirmation.resolve_confirmation()` has an over-broad condition:

```python
if intent_decision_gate_action == ConfirmationGateTurn.ANOTHER_REQUEST:
    consume_confirmation_state(payload, reason="confirmation_superseded")
    confirmation_state = None
    _clear_time_selection_for_availability_refinement(working_turn)
    slots_adjusted = True
```

Then the same function prevents re-entering confirmation on that turn:

```python
if (
    intent_decision_gate_action != ConfirmationGateTurn.ANOTHER_REQUEST
    and not availability_reshow
    and turn_operation not in _AVAILABILITY_OPERATIONS
):
    confirmation_state = _maybe_enter_booking_confirmation_pending(...)
```

This is the first point where expected behavior diverges. The time correction is already recognized and bound before confirmation handling. The confirmation stage then clears the selection and blocks the turn from becoming `AWAITING_CONFIRMATION` again.

### Why rendering is not the root cause

Rendering is absent because the plan became a skipped, planning-only `READY` outcome. `ExecutionCoordinator.resolve()` returns `path="skipped"` when no eligible action is selected, and `ConversationEngine._finish_gate()` returns that response without calling `ResponseRenderer.render_execution()`.

Rendering is only the visible symptom.

---

## Bug 2: `show availability for flexi`

Conversation:

```text
Book haircut
-> Premium
-> Awaiting clarification
-> show availability for flexi
```

Luma correctly recognizes Flexi, but availability is searched using Premium.

### Execution path

| Stage | Input | Output / mutation | Decision |
| --- | --- | --- | --- |
| HTTP entry | `POST /message`, text `show availability for flexi`, session has `slots.service_id=Premium` | `post_message()` loads raw session and calls `ConversationEngine.process_turn()` | HTTP does not alter slots. |
| Core context for NLU | Session has Premium satisfied; active create booking | `build_conversation_context()` attaches `resolved_service_id=Premium` because `service_id` is not in `missing_slots` | Context is correct for slot-filling reuse, but it becomes harmful when the utterance names a new service. |
| NLU Stage 2 (AVAILABILITY) | Text `show availability for flexi` | `AvailabilityGroupExtractor` fills `facts.service_id` with Flexi; does not set `service_term` | Extraction is correct. |
| NLU post-process | `service_term=None`, `facts.service_id=Flexi`, context `resolved_service_id=Premium` | `_resolve_service_ambiguity()` calls `resolve_service()` and writes `facts.service_id=Premium` | First incorrect state: Flexi is overwritten before Core receives the response. |
| Intent reconciliation | Raw availability intent; session intent `CREATE_APPOINTMENT` | `reconcile_intent()` maps to `planning_intent=CREATE_APPOINTMENT`; `turn_operation=AVAILABILITY` | Correct durable-intent remap, but slots are already Premium. |
| Working turn / merge | Luma payload already Premium | `build_working_turn()` and `merge_luma_with_session()` promote/merge Premium | Flexi never reaches Core. |
| Revision detection | `new_service == current_service` (both Premium) | `detect_booking_revision()` reports `service=False` | Not a revision bug; the service change was erased upstream. |
| Decision plan | Effective slots Premium; availability not ready after criteria change | `build_decision_plan_from_stages()` selects `SEARCH_AVAILABILITY` | Correct action for the corrupted slots. |
| Execution coordinator / client | Plan slots Premium | `ExecutionCoordinator.resolve()` passes plan slots to `AvailabilityClient.get_service_availability(service_id=Premium)` | Availability request uses Premium because NLU sent Premium. |

### Root cause

`nlu.pipeline.NLUPipeline._resolve_service_ambiguity()` always resolves service from `service_term`, not from an already-extracted `facts.service_id`:

```python
service_term = slm.get("service_term")  # None for AVAILABILITY
...
resolved = resolve_service(..., service_term=service_term, resolved_service_id=resolved_service_id)
return {**slm, "facts": {**facts, "service_id": resolved["service_id"]}, ...}
```

For AVAILABILITY turns, `service_term` is null while Flexi lives in `facts.service_id`. `nlu.catalog.resolve_service()` then reuses locked context:

```python
if not service_term:
    if resolved_service_id:
        return {"service_id": resolved_service_id, "service_candidates": []}
```

That is the first incorrect state for Bug 2. Core merge, revision, planner, and availability client all operate correctly on the already-corrupted Premium service.

This is not an availability-client bug. The client receives Premium because NLU overwrote Flexi before Core planning began.

### Requested checks

| Check | Finding |
| --- | --- |
| What service did Luma extract? | Flexi in Stage 2 `facts.service_id` before post-process. |
| What reached merge? | Premium only. `_resolve_service_ambiguity()` overwrote Flexi before Core received the Luma response. |
| Did merge reject it? | No rejection path ran; Flexi never arrived. Merge correctly keeps Premium from the Luma payload. |
| Did revision detection ignore it? | No service revision is detected because `new_service` and `current_service` are both Premium after NLU overwrite. |
| Did session override it? | Indirectly, via `resolved_service_id` in NLU context, not via Core merge stickiness on this turn. |
| Did planner choose previous service? | Planner searches using the slots it received; those slots were already Premium. |
| Where did availability obtain Premium? | `AvailabilityClient` reads `plan["slots"]["service_id"]`, which traces back to NLU's overwritten `facts.service_id`. |

---

## Shared or independent?

They are independent immediate bugs:

- Bug 1 is a confirmation-stage control-flow bug.
- Bug 2 is an NLU service-resolution bug for AVAILABILITY turns that extract `facts.service_id` without `service_term`.

They share an architectural pattern: Core recognizes current-turn revision evidence correctly, but a later generic preservation rule treats previous session state as more authoritative than the current turn.

The current-turn data should remain authoritative after revision detection; session preservation should not erase it.

---

## Smallest architectural fix locations

### Bug 1

Smallest owner: `core/planning/pipeline/stage06_confirmation.py`.

The confirmation stage owns pending confirmation authorization and the `ANOTHER_REQUEST` branch. A fix belongs where the code decides whether an actionable correction should:

- consume the old pending confirmation,
- preserve or replace the newly bound time selection,
- and re-enter `AWAITING_CONFIRMATION`.

This should not be fixed in rendering, because rendering only sees the already-skipped READY outcome.

### Bug 2

Smallest owner: `nlu/pipeline.py` and `nlu/catalog.py`.

The stale service is introduced in NLU post-processing, before Core merge. The fix belongs where AVAILABILITY service evidence is resolved:

- honor an explicit `facts.service_id` from the current utterance before reusing `resolved_service_id`, and/or
- emit `service_term` for AVAILABILITY service mentions so `resolve_service()` can distinguish a new service from a date-only follow-up.

This should not be fixed in the availability client, because the client receives Premium through normal plan slots after NLU has already corrupted the service.

