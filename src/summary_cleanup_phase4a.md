# Package Cleanup – Phase 4A: Remove Remaining Dead Modules

## Files Deleted

| File | Bytes | Reason |
|---|---|---|
| `core/orchestration/contracts/__init__.py` | 89 | Package declaration for dead-only package |
| `core/orchestration/contracts/luma_contracts.py` | 1 659 | Duplicate of `nlu/luma_contracts.py`; zero callers |
| `core/orchestration/clients/availability_client.py` | 2 421 | Stale copy; superseded by `execution/clients/`; zero callers |
| `core/orchestration/clients/booking_client.py` | 7 134 | Stale copy; superseded by `execution/clients/`; zero callers |
| `core/orchestration/clients/payment_client.py` | 1 643 | Stale copy; superseded by `execution/clients/`; zero callers |
| `core/orchestration/clients/staff_client.py` | 1 156 | Stale copy; superseded by `execution/clients/`; zero callers |

---

## Evidence for Deletion

### `core/orchestration/contracts/luma_contracts.py`

**Zero production callers confirmed.**

The function `assert_luma_contract` exists in two places:
- `core/orchestration/nlu/luma_contracts.py` — **live** implementation
- `core/orchestration/contracts/luma_contracts.py` — **dead** duplicate (this file)

All callers import from the `nlu` path:

| Caller | Import source |
|---|---|
| `orchestrator.py:35` | `from core.orchestration.nlu import assert_luma_contract` |
| `turn_planner.py:26` | `from core.orchestration.nlu import assert_luma_contract` |
| `tests/orchestration/contracts/test_luma_contracts.py:19` | `from core.orchestration.nlu.luma_contracts import assert_luma_contract` |

Grep for `orchestration\.contracts` and `orchestration/contracts` across all `.py` files: zero matches.
No dynamic imports (`importlib`, `__import__`) reference this path.

**`core/orchestration/contracts/__init__.py`** was a 4-line package declaration with no symbols and no callers. Deleted alongside `luma_contracts.py`.

**`core/orchestration/contracts/` directory** is retained — it contains `AVAILABILITY_INTERACTION_CONTRACT.md`, an architectural contract document referenced in `CLAUDE.md`. The directory is no longer a Python package (no `__init__.py`).

---

### `core/orchestration/clients/{availability,booking,payment,staff}_client.py`

**Zero production and test callers confirmed for all four files.**

Verification steps:
1. `from core.orchestration.clients import` — matches only `CatalogClient`, `CustomerClient`, `OrganizationClient` in any file
2. `from core.orchestration.clients.(availability|booking|payment|staff)` — zero matches
3. `core.orchestration.clients.(AvailabilityClient|BookingClient|PaymentClient|StaffClient)` — zero matches
4. Dynamic import patterns (`importlib.*orchestration`, `__import__.*orchestration`) — zero matches

The `clients/__init__.py` itself imports only the three live context clients and already contains an explanatory note:

> "Note: Execution clients (booking, payment, availability, staff) have been moved to `core.orchestration.execution.clients`."

This note remains accurate after deletion: it correctly directs developers to the canonical execution clients.

The canonical implementations are in `core/orchestration/execution/clients/`:
- `availability_client.py` — with tracing, used by `dispatcher.py`
- `booking_client.py` — with tracing, used by `dispatcher.py`
- `payment_client.py` — used by `dispatcher.py`
- `staff_client.py` — used by `dispatcher.py`

The stale copies in `orchestration/clients/` used the older `base_client.py` (from `orchestration/clients/`) while the canonical copies use the execution `base_client.py` (from `orchestration/execution/clients/`). Neither `base_client.py` is affected by this cleanup.

---

## Package Updates

### `core/orchestration/clients/__init__.py` — No changes required

Already exports only the three live context clients. The explanatory note is accurate and retained.

### `core/orchestration/__init__.py` — No changes required

The package docstring lists "clients: Context clients (catalog, customer, organization)" which remains accurate. The stale execution client files are gone and were never mentioned by name in this docstring.

Note: the `orchestration/__init__.py` still lists "actions: Action handlers for booking operations" in its docstring — the `actions/` sub-package was deleted in Phase 1. This is pre-existing stale documentation outside Phase 4A scope.

---

## Behavioural Impact Assessment

**Production behaviour: unchanged.**

No code path executed at runtime imported from either deleted location:
- `core.orchestration.contracts` was never imported at runtime
- `core.orchestration.clients.{availability,booking,payment,staff}_client` was never imported at runtime

The execution clients in `core.orchestration.execution.clients` are the sole path through which availability search, booking creation, payment, and staff operations execute. Those files are untouched.

---

## Remaining Architectural Debt

The following items were identified during this investigation and remain for future cleanup:

1. **`core/orchestration/__init__.py` stale docstring entry** — "actions: Action handlers for booking operations" references the `actions/` package deleted in Phase 1. Low risk; documentation-only.

2. **`core/orchestration/persistence/durable_intents.py`** — Policy wrapper for `core.policy.intent_policy.get_intent_durable`. The name "persistence" is misleading (no storage involved). `is_durable_intent` is also independently defined in `core/policy/intent_policy.py`. 15+ callers use the `persistence/durable_intents.py` path. Consolidation into `core/policy/` requires a compatibility re-export phase.

3. **`core/orchestration/api/turn_state.py`** — NLU/planning concern located in the HTTP API package. Single production caller: `luma_response_processor.py`. Trivial to move to `orchestration/nlu/`; held back by convention risk.

4. **`core/routing/intents/base_intents.py`** — Policy declarations (`CORE_BASE_INTENTS`, `is_core_intent`) located in the routing package. Should be in `core/policy/`. One production caller: `intent_resolution.py`. Low complexity.

5. **`core/routing/workflows/`** — Workflow extensibility registry (`Workflow` Protocol + `WorkflowRegistry`) in the routing package. Should be in `core/workflows/` alongside the concrete implementations. Medium complexity.

6. **`core/routing/execution/`** — `config.py` (env var reader) and `test_backend.py` (test infrastructure) inside a routing sub-package. Neither has routing concerns. Test-only callers. Low risk to relocate.
