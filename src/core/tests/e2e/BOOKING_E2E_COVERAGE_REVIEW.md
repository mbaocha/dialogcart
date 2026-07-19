# Booking E2E Coverage Review

## Purpose

This document inventories the 30 tests currently collected from
`core/tests/e2e` and provides a reusable prompt for identifying missing
booking-conversation edge cases.

The review scope is user interaction with the Core booking flow. Capabilities,
extensions, RAG, tracing infrastructure, test-framework mechanics, and
standalone session persistence are not treated as booking-interaction coverage.

## Collection summary

Pytest currently collects:

- 15 items from `test_booking.py`
- 7 items from `test_browsing.py`
- 1 item from `test_rag.py`
- 3 items from `test_session.py`
- 4 items from `test_tracing.py`
- 30 items total

Of those 30:

- 20 exercise booking conversation interactions:
  - 13 parameterized booking scenarios
  - 7 availability browsing scenarios
- 10 are supporting or out of scope:
  - 2 conversation-DSL checks
  - 1 RAG/extension check
  - 3 session-persistence checks
  - 4 tracing checks

There are no dedicated capability tests in this 30-item collection.

## Exact 30 collected pytest items

### `test_booking.py` — 15 items

1. `test_conversation_dsl_expect_aliases`
2. `test_conversation_dsl_coerces_turn_shorthand`
3. `test_booking_scenario[happy-path-create-appointment]`
4. `test_booking_scenario[reject-then-revise-time]`
5. `test_booking_scenario[unavailable-time-keeps-booking-flow]`
6. `test_booking_scenario[service-revision-invalidates-availability]`
7. `test_booking_scenario[date-revision-invalidates-availability]`
8. `test_booking_scenario[tomorrow-by-9am-premium-exact]`
9. `test_booking_scenario[tomorrow-by-12pm-premium-yes]`
10. `test_booking_scenario[book-haircut-premium-10am-yes]`
11. `test_booking_scenario[time-match-exact-same-turn]`
12. `test_booking_scenario[time-match-mismatch-conversational]`
13. `test_booking_scenario[empty-availability-no-slots]`
14. `test_booking_scenario[mismatch-then-pick-alternative]`
15. `test_booking_scenario[time-resolution-persists-across-turns]`

### `test_browsing.py` — 7 items

16. `test_browse_pagination_full_api_path_validation`
17. `test_show_more_times_paginates_existing_availability`
18. `test_show_more_at_last_page_says_no_more`
19. `test_previous_page_returns_earlier_availability`
20. `test_pagination_resets_on_service_change`
21. `test_pagination_resets_on_date_change`
22. `test_time_on_page_two_binds_not_page_one_slot`

### `test_rag.py` — 1 item

23. `test_session_messages_appended_after_handler_delegated`

### `test_session.py` — 3 items

24. `test_ready_outcome_persists_service_id_slots`
25. `test_executed_success_outcome_persists_service_id_slots`
26. `test_awaiting_confirmation_outcome_persists_bound_datetime_and_pending`

### `test_tracing.py` — 4 items

27. `test_decision_trace_spine_records`
28. `test_decision_trace_absent_when_disabled`
29. `test_decision_trace_causal_graph_from_session_to_outcome`
30. `test_forensic_trace_records_availability_and_time_resolution`

## In-scope booking interaction coverage

### 1. Happy path: create appointment

Turns:

`book me a haircut` → `premium` → `10am` → `yes`

Coverage:

- service clarification;
- availability search;
- selection from presented availability;
- explicit confirmation;
- booking execution only after confirmation;
- persisted booking identifier;
- no booking before confirmation.

### 2. Reject, revise time, then confirm

Turns:

`book haircut` → `premium` → `10am` → `no` → `11am` → `yes`

Coverage:

- rejection clears pending confirmation;
- service and date survive rejection;
- revised time replaces the rejected time;
- no booking occurs until the final approval.

### 3. Unavailable requested time

Turns:

`book me a haircut` → `premium` → `12pm`

Coverage:

- a time absent from presented availability is rejected;
- no extra availability search occurs;
- unavailable time is not made durable;
- alternatives remain available;
- no booking occurs.

### 4. Service revision invalidates availability

Turns:

`book haircut` → `premium` → `10am` → `rather book flexi haircut`

Coverage:

- service revision while confirmation is pending;
- prior confirmation and bound date/time are cleared;
- stale availability is invalidated;
- a new availability search runs;
- no booking occurs.

### 5. Date revision invalidates availability

Turns:

`book haircut` → `premium` → `10am` → `actually July 11`

Coverage:

- date revision while confirmation is pending;
- prior bound date/time and confirmation are cleared;
- revised date is retained;
- stale availability is invalidated and searched again;
- no booking occurs.

### 6. Temporal request retained until service is selected

Turns:

`book haircut tomorrow by 9am` → `premium`

Coverage:

- date/time proposal survives service clarification;
- service selection triggers availability;
- exact temporal match binds;
- flow advances to confirmation.

### 7. Temporal request followed by confirmation

Turns:

`book me haircut tomorrow by 12pm` → `premium` → `yes`

Coverage:

- temporal information supplied before service;
- exact match and pending confirmation;
- booking only after explicit approval.

### 8. Scripted happy path

Turns:

`book haircut` → `premium` → `10am` → `yes`

Coverage:

- deterministic equivalent of the main creation path;
- availability, durable slots, confirmation gating, and commit.

### 9. Exact time match after service selection

Turns:

`book haircut tomorrow at 10am` → `premium`

Coverage:

- exact temporal resolution when the remaining service arrives;
- one availability search;
- pending confirmation without premature booking.

### 10. Conversational time mismatch

Turns:

`book haircut tomorrow at 9:15am` → `premium`

Coverage:

- requested time falls between offered slots;
- alternatives are presented;
- flow remains in time selection;
- no booking occurs.

### 11. Empty availability

Turns:

`book haircut tomorrow by 9am` → `premium`

Coverage:

- successful search with zero slots;
- no executable booking action;
- temporal proposal is retained;
- user remains in clarification.

### 12. Recover from mismatch by selecting an alternative

Turns:

`book haircut tomorrow at 9:15am` → `premium` → `9:30am`

Coverage:

- mismatch recovery;
- selection of a presented alternative;
- exact binding and pending confirmation;
- no booking before approval.

### 13. Time-resolution state persists across turns

Turns:

`book haircut tomorrow at 9:15am` → `premium`

Coverage:

- date/time proposals persist after mismatch;
- availability result remains available for the next turn.

## In-scope availability browsing coverage

The common setup is:

`book haircut` → `premium` → `actually July 9`

It establishes a booking intent, resolved service/date, cached availability, and
page zero of the presented availability.

### 14. Full browse API path

Adds:

`show me additional times`

Coverage:

- structured next-page operation;
- page zero to page one;
- no new availability search;
- no booking;
- unchanged full availability cache;
- distinct presented page and pagination metadata.

### 15. Show more times

Adds:

`show more times`

Coverage:

- cached availability is reused;
- second page differs from the first;
- booking slots remain unchanged;
- no action, confirmation, or booking.

### 16. Browse beyond the last page

Adds two forward browse turns.

Coverage:

- last page remains stable;
- pagination is marked exhausted;
- no search or booking;
- response explains that no more times exist.

### 17. Return to the previous page

Adds:

`show more times` → `show earlier times`

Coverage:

- page one returns to page zero;
- no availability search;
- no booking.

### 18. Service change resets pagination

Coverage:

- browse to page one;
- revise service;
- availability search runs again;
- page resets to zero;
- no booking occurs.

### 19. Date change resets pagination

Coverage:

- browse to page one;
- revise date;
- availability search runs again;
- page resets to zero;
- no booking occurs.

### 20. Only the current page is selectable

Turns after browsing:

`9am` → `5pm`

Coverage:

- a time visible only on page zero cannot be selected while page one is active;
- a time on the active page binds successfully;
- no extra search occurs;
- flow reaches confirmation without booking.

## Out-of-scope collected items

These are part of the 30-item collection but should not be counted when
assessing booking conversation edge coverage.

### Test-framework checks

- `test_conversation_dsl_expect_aliases`
- `test_conversation_dsl_coerces_turn_shorthand`

They validate the declarative test DSL, not user booking behavior.

### RAG/extension behavior

- `test_session_messages_appended_after_handler_delegated`

It tests `GENERAL_INQUIRY` delegation to the RAG extension and is explicitly
outside this review.

### Standalone session mechanics

- `test_ready_outcome_persists_service_id_slots`
- `test_executed_success_outcome_persists_service_id_slots`
- `test_awaiting_confirmation_outcome_persists_bound_datetime_and_pending`

They patch the engine with fabricated outcomes and primarily verify API/session
projection rather than booking decisions.

### Tracing infrastructure

- `test_decision_trace_spine_records`
- `test_decision_trace_absent_when_disabled`
- `test_decision_trace_causal_graph_from_session_to_outcome`
- `test_forensic_trace_records_availability_and_time_resolution`

They use booking-shaped requests but primarily assert observability behavior.

## Prompt: identify missing booking edge-case runs

Copy the following prompt together with this document:

```text
You are reviewing Core end-to-end pytest coverage for DialogCart booking
conversation interactions.

Objective:
Identify meaningful missing edge-case conversation runs that are not already
covered by the supplied 30-test inventory.

Strict scope:
- Include only user-to-Core booking interactions through the HTTP/conversation
  boundary.
- Include booking creation, slot collection, service/date/time revisions,
  availability search and presentation, offered-time binding, pagination,
  mismatch recovery, confirmation acceptance/rejection/revision,
  interruption/resumption, cancellation language during an active creation
  flow, repeated input, and idempotency where observable as conversation
  behavior.
- Treat session state only as evidence of conversational correctness, not as a
  standalone persistence subject.
- Exclude capabilities, extensions, RAG/FAQ handlers, payments, identity,
  notifications, tracing/observability infrastructure, test-DSL mechanics,
  client-contract unit tests, and standalone session repository/projection
  tests.
- Do not propose tests whose primary purpose is capability or extension
  behavior.
- Do not duplicate an existing test merely by changing wording, service name,
  date, or time unless that variation exercises a materially different state
  transition or ambiguity.

Inputs:
1. The exact 30 collected pytest items in this document.
2. The 20 in-scope booking interaction runs and their existing assertions.
3. These Core booking invariants:
   - Core session is the sole owner of durable booking state.
   - NLU emits per-turn facts and must not fabricate booking slots absent from
     the current utterance.
   - Temporal proposals become durable only when bound to currently presented
     availability or explicitly confirmed.
   - Booking execution requires explicit user confirmation.
   - A successful commit consumes confirmation state and persists a booking ID.
   - A changed availability search parameter invalidates prior availability.
   - Browsing reuses cached availability and never executes
     SEARCH_AVAILABILITY.
   - Only currently presented availability may be selected.
   - Pagination changes presentation state, not booking truth.

Analysis procedure:
A. Build a state-transition coverage model from the supplied tests:
   clarification → availability → time selection/mismatch → confirmation →
   commit, including revision and browsing branches.
B. Identify untested transitions, boundary conditions, interruption paths,
   stale-state hazards, ambiguous inputs, repeated inputs, and unsafe execution
   opportunities.
C. For every proposed gap, cite the closest existing test and explain precisely
   why it does not cover the gap.
D. Reject low-value wording permutations and implementation-only checks.
E. Prioritize cases that could cause an incorrect booking, premature booking,
   stale availability use, lost user revision, duplicate commit, cross-flow
   contamination, or a stuck booking flow.

Required output:
1. Coverage summary by booking conversation phase.
2. Ranked list of missing scenarios: Critical, High, Medium, Low.
3. For each proposed scenario:
   - concise scenario ID;
   - risk being prevented;
   - exact user-turn sequence;
   - expected status, stage, action, and awaiting value after each relevant
     turn;
   - expected durable slot, proposal, and confirmation changes;
   - expected availability-search and booking-call counts;
   - key positive and negative assertions;
   - closest existing test and the exact uncovered distinction;
   - recommended fixture style: scripted NLU or real-NLU interaction.
4. A "not recommended" section listing apparent gaps rejected as duplicates,
   capability/extension concerns, or infrastructure-only tests.
5. A final minimal suite containing only the highest-value, non-overlapping
   booking interaction runs.

Do not write implementation code. Do not propose capability, extension, RAG,
tracing, payment, or standalone session-mechanics tests. Base every conclusion
on the supplied inventory and invariants, and mark assumptions explicitly.
```

