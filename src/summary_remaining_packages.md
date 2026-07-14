# Investigation – Remaining Top-Level Package Review

## Packages Under Review

- `core/orchestration/` — 48 Python files across 8 sub-packages + root
- `core/routing/` — 11 Python files across 4 sub-packages + root

---

## `core/orchestration/` — Package Inventory

### Sub-package: `api/`

| File | Responsibility | Owner | Correctly Located? |
|---|---|---|---|
| `main.py` | FastAPI router registration | HTTP boundary | Yes |
| `message.py` | HTTP entry point; session load, ConversationEngine delegation, capability boundary | HTTP boundary | Yes |
| `capability_boundary.py` | Applies capability results (payment, noop) to orchestration outcome | HTTP boundary | Yes |
| `turn_state.py` | `TurnState`/`DecisionReason` types + `finalize_turn_state()` | NLU/Planning | **No** |

`turn_state.py` is misplaced. Its single production caller is `luma_response_processor.py` in `core/orchestration/nlu/`. It imports from `core.planning.policy.action_policy`. It is an NLU/planning helper, not an API module.

### Sub-package: `clients/` (Context clients)

| File | Responsibility | Active callers | Correctly Located? |
|---|---|---|---|
| `base_client.py` | Shared HTTP base class | All context clients, extensions | Yes |
| `catalog_client.py` | Fetch tenant catalog (services, resources) | orchestrator, turn_planner, cache, catalog_resolver | Yes |
| `customer_client.py` | Fetch customer record | turn_planner | Yes |
| `organization_client.py` | Fetch org domain + config | orchestrator, turn_planner, cache | Yes |
| `availability_client.py` | Legacy availability client | **Zero** | **No — stale** |
| `booking_client.py` | Legacy booking client | **Zero** | **No — stale** |
| `payment_client.py` | Legacy payment client | **Zero** | **No — stale** |
| `staff_client.py` | Legacy staff client | **Zero** | **No — stale** |
| `test.py` | Test client fixtures (2345 lines) | Tests only | Questionable location |

The `__init__.py` explicitly documents the split: "Execution clients have been moved to `core.orchestration.execution.clients`." The four stale files (`availability_client.py`, `booking_client.py`, `payment_client.py`, `staff_client.py`) are dead: no production or test code imports from `core.orchestration.clients.{availability,booking,payment,staff}_client`. The execution-client versions in `execution/clients/` are the live implementations.

### Sub-package: `execution/`

| File | Responsibility | Owner | Correctly Located? |
|---|---|---|---|
| `dispatcher.py` | Routes execution steps to clients by `client_name` | Execution dispatch | Yes |
| `clients/availability_client.py` | Availability search execution client (with tracing) | Execution | Yes |
| `clients/booking_client.py` | Booking creation/confirmation execution client | Execution | Yes |
| `clients/payment_client.py` | Payment execution client | Execution | Yes |
| `clients/staff_client.py` | Staff lookup execution client | Execution | Yes |
| `clients/base_client.py` | Execution client base class (httpx, UpstreamError) | Execution | Yes |

Coherent and correctly owned. The execution clients are called by `message.py`, `workflows/`, and tests.

### Sub-package: `nlu/`

| File | Responsibility | Owner | Correctly Located? |
|---|---|---|---|
| `luma_client.py` | HTTP client to the Luma service | NLU integration | Yes |
| `luma_response_processor.py` | Interprets Luma response into Core decisions | NLU integration | Yes |
| `luma_contracts.py` | `assert_luma_contract()` — validates Luma response schema | NLU integration | Yes |
| `conversation_memory.py` | Builds conversation history for NLU context | NLU integration | Yes |

Coherent and correctly owned. `luma_contracts.py` is the **live** contract validator — re-exported from `nlu/__init__.py` and imported by `orchestrator.py`.

### Sub-package: `cache/`

| File | Responsibility | Owner | Correctly Located? |
|---|---|---|---|
| `catalog_cache.py` | Module-level catalog cache for context building | Orchestration | Yes |
| `org_domain_cache.py` | Module-level org+domain cache | Orchestration | Yes |

Coherent, correctly owned.

### Sub-package: `persistence/`

| File | Responsibility | Owner | Correctly Located? |
|---|---|---|---|
| `durable_intents.py` | `is_durable_intent()` + `filter_slots_for_intent()` | Policy | **No** |

`durable_intents.py` is misplaced. Its implementation simply delegates to `core.policy.intent_policy.get_intent_durable()`. The function `is_durable_intent` is also independently defined in `core/policy/intent_policy.py` (duplicate). The name "persistence" is misleading — neither function reads or writes to any store; both are pure policy classifications. Despite its misplaced location, it has 15+ callers across session, planning, and tracing packages. The persistence sub-package name contributes to naming confusion.

### Sub-package: `session/`

| File | Responsibility | Owner | Correctly Located? |
|---|---|---|---|
| `session_manager.py` | Redis-backed session store (`get_session`, `save_session`, `clear_session`) | Infrastructure I/O | Yes |

This is correctly distinct from `core/session/` (business logic layer). `core/orchestration/session/` is infrastructure: the Redis adapter. `core/session/` is the session domain: merge, persist, schema, invalidation. The distinction is sound.

### Sub-package: `contracts/`

| File | Responsibility | Owner | Correctly Located? |
|---|---|---|---|
| `luma_contracts.py` | `assert_luma_contract()` — duplicate of `nlu/luma_contracts.py` | — | **No — dead** |

`contracts/luma_contracts.py` has **zero callers** in production. The one test that calls `assert_luma_contract` (`test_luma_contracts.py`) imports it from `core.orchestration.nlu.luma_contracts`, not from `core.orchestration.contracts`. The `contracts/` sub-package exists for this single unused file.

### Root-level modules

| File | Responsibility | Owner | Correctly Located? |
|---|---|---|---|
| `orchestrator.py` | planning + compatibility layer; `handle_message`, `plan_message` | Orchestration | Yes |
| `availability_browse.py` | Transient browse signal normalization from Luma operations | Availability | Yes |
| `availability_fingerprint.py` | Fingerprint computation for search-criteria dedup | Availability | Yes |
| `availability_pagination.py` | Browse turn short-circuit (`try_handle_availability_browse_turn`) | Availability | Yes |
| `catalog_resolver.py` | SKU → catalog item ID resolution before execution calls | Execution prep | Yes |
| `errors.py` | Shared exception types (`UpstreamError`, `ContractViolation`, etc.) | Infrastructure | Yes |
| `luma_facts_adapter.py` | NLU facts → slot dict conversion; called by session/merge, turn_planner, temporal_proposal | NLU/Session boundary | Borderline |
| `session_ops.py` | `_persist_to_session()` — write single key to session store | Orchestration utility | Yes |
| `temporal_proposal.py` | Date/time proposal resolution for search and availability binding | Availability | Yes |
| `time_resolution.py` | Post-search exact time matching against returned offers | Availability | Yes |

`luma_facts_adapter.py` is borderline: it adapts NLU output (Luma facts) into slot format consumed by the session layer. Its callers are split between NLU (`luma_response_processor.py`, `temporal_proposal.py`) and session (`merge.py`, `effective_slots.py`) and planning (`turn_planner.py`). It belongs at the NLU/session boundary — neither package entirely owns it, and the orchestration root is a reasonable home.

---

## `core/orchestration/` — Architectural Assessment

**Verdict: Integration hub — not a single coherent architectural boundary.**

The package encompasses five distinct concerns:
1. **HTTP boundary** (`api/`, minus `turn_state.py`) — correctly owned
2. **NLU integration** (`nlu/`) — correctly owned, coherent sub-package
3. **Execution dispatch** (`execution/`) — correctly owned, coherent sub-package
4. **Context clients** (`clients/` — catalog, customer, org only) — correctly owned
5. **Availability orchestration** (root: fingerprint, pagination, browse, temporal, time_resolution) — correctly owned
6. **Infrastructure** (`session/`, `cache/`, `errors.py`, `session_ops.py`) — correctly owned
7. **Stale/misplaced** (`persistence/durable_intents.py`, `api/turn_state.py`, `contracts/`, four dead client files)

The package cannot be characterized as a single architectural boundary, but the diverse contents are mostly stable and correctly located within their sub-packages. The misplaced items are debts, not incoherence of the package as a whole.

---

## `core/routing/` — Package Inventory

| File | Responsibility | Owner | Correctly Located? |
|---|---|---|---|
| `clarification_router.py` | Clarification reason → template key (pure lookup, YAML-backed) | Routing | Yes |
| `intent_router.py` | Intent name → action name (`INTENT_ACTIONS` dict); `get_action_name()` | Routing | Yes |
| `action_router.py` | Action name → handler function mapping | Routing | Yes |
| `handler_router.py` | Intent name → handler name (from `intent_handlers.yaml`) | Routing | Yes |
| `intents/base_intents.py` | `CORE_BASE_INTENTS` set + `is_core_intent()` | Policy | **No** |
| `workflows/workflow.py` | `Workflow` Protocol + `WorkflowRegistry` + get/register/has_workflow | Extensibility | **No** |
| `workflows/examples/payment_prompt_workflow.py` | Example workflow implementation | Extensibility example | **No** |
| `execution/config.py` | Execution mode env var reader (`get_execution_mode()`) | Configuration | **No** |
| `execution/test_backend.py` | Deterministic test execution backend | Test infrastructure | **No** |

### Caller summary

| Module | Production callers | Test callers |
|---|---|---|
| `clarification_router` / `get_template_key` | `luma_response_processor.py` (2 sites), `plan_builder.py` | 0 direct |
| `intent_router` / `get_action_name` | `luma_response_processor.py`, `plan_builder.py` | 0 direct |
| `action_router` | 0 | 0 |
| `handler_router` / `resolve_handler` | `turn_planner.py` | `test_handler_router.py` |
| `intents/base_intents` | `intent_resolution.py` | `test_base_intents.py`, `test_non_core_intent_passthrough.py` |
| `workflows/` | `orchestrator.py` (`get_workflow`) | `test_workflow_*.py` |
| `execution/config` | 0 | `test_test_backend.py` |
| `execution/test_backend` | 0 | `test_test_backend.py` |

---

## `core/routing/` — Architectural Assessment

**Verdict: Core is coherent (pure lookup tables); sub-packages are misplaced.**

The four root-level routers (`clarification_router`, `intent_router`, `action_router`, `handler_router`) form a coherent identity: pure signal-to-identifier mapping tables with no side effects and no external calls. The `__init__.py` description is accurate for these four files.

The three sub-packages do not belong:

- **`intents/`** declares which intents are core-owned — that is **policy**, not routing. `is_core_intent()` is called by `intent_resolution.py` in the planning layer. The correct home is `core/policy/`.

- **`workflows/`** is a **workflow extensibility registry** — the `Workflow` Protocol and `WorkflowRegistry`. The concrete implementations already live in `core/workflows/` (availability, booking). The registry belongs there too. Routing does not own extensibility hooks.

- **`execution/`** contains execution mode configuration (`config.py`) and a test execution backend (`test_backend.py`). Configuration belongs in `core/config/`. The test backend belongs in test infrastructure. Neither has a routing concern.

`action_router.py` is a special case: it maps action names to handler functions but has **zero callers** in any production or test code. It may be a preparatory registration point awaiting future use, but currently it is unreferenced.

---

## Identified Misplacements

| File | Current location | Correct location | Callers affected |
|---|---|---|---|
| `orchestration/api/turn_state.py` | `core/orchestration/api/` | `core/orchestration/nlu/` | `luma_response_processor.py` |
| `orchestration/persistence/durable_intents.py` | `core/orchestration/persistence/` | `core/policy/` (consolidate with duplicate in `intent_policy.py`) | 15+ files in session, planning, tracing |
| `orchestration/contracts/luma_contracts.py` | `core/orchestration/contracts/` | Delete — dead file | 0 |
| `orchestration/clients/availability_client.py` | `core/orchestration/clients/` | Delete — stale (moved to `execution/clients/`) | 0 |
| `orchestration/clients/booking_client.py` | `core/orchestration/clients/` | Delete — stale (moved to `execution/clients/`) | 0 |
| `orchestration/clients/payment_client.py` | `core/orchestration/clients/` | Delete — stale (moved to `execution/clients/`) | 0 |
| `orchestration/clients/staff_client.py` | `core/orchestration/clients/` | Delete — stale (moved to `execution/clients/`) | 0 |
| `routing/intents/base_intents.py` | `core/routing/intents/` | `core/policy/` | `intent_resolution.py`, 3 test files |
| `routing/workflows/` | `core/routing/workflows/` | `core/workflows/` (protocol + registry alongside implementations) | `orchestrator.py`, 3 test files |
| `routing/execution/config.py` | `core/routing/execution/` | `core/config/` | `test_test_backend.py` only |
| `routing/execution/test_backend.py` | `core/routing/execution/` | `core/tests/` (test infrastructure) | `test_test_backend.py` only |
| `orchestration/persistence/` (entire sub-package) | `core/orchestration/persistence/` | Delete after consolidation | — |
| `orchestration/contracts/` (entire sub-package) | `core/orchestration/contracts/` | Delete after dead file removed | — |

---

## Recommended End State

### `core/orchestration/` — Remain as-is with targeted removals

The package should not be renamed or split. Its diverse contents reflect genuine orchestration integration responsibilities that span NLU, execution, and HTTP. Restructuring would be high-risk with low architectural benefit.

**Recommended targeted removals (low-risk, zero-caller items):**
1. Delete `core/orchestration/contracts/luma_contracts.py` — dead file, zero callers
2. Delete `core/orchestration/contracts/__init__.py` and the `contracts/` sub-package
3. Delete the four stale client files: `clients/availability_client.py`, `clients/booking_client.py`, `clients/payment_client.py`, `clients/staff_client.py`

**Recommended targeted moves (medium-risk, many callers):**
4. `api/turn_state.py` → `orchestration/nlu/turn_state.py`: 1 production caller, trivial import update
5. `persistence/durable_intents.py` → consolidate into `core/policy/intent_policy.py`: 15+ callers, needs a compatibility re-export phase

### `core/routing/` — Keep core files; relocate sub-packages over time

The routing package has a sound core identity. The lookup tables should remain. The sub-packages should relocate:

| Sub-package | Target | Complexity |
|---|---|---|
| `intents/base_intents.py` → `core/policy/base_intents.py` | Policy boundary | Low (1 production caller) |
| `workflows/` → `core/workflows/` (merge registry with implementations) | Workflow ownership | Medium (import chain update across orchestrator + tests) |
| `execution/config.py` → `core/config/execution_config.py` | Configuration | Low (test-only caller) |
| `execution/test_backend.py` → `core/tests/execution/` | Test infrastructure | Low (test-only caller) |

After these moves, `routing/` becomes a clean, single-purpose package: pure signal-to-identifier lookup tables (`clarification_router`, `intent_router`, `action_router`, `handler_router`).

---

## Migration Complexity and Risk

| Item | Complexity | Risk | Justification |
|---|---|---|---|
| Delete `contracts/luma_contracts.py` + dir | **Trivial** | None | Zero callers confirmed |
| Delete 4 stale `clients/` files | **Trivial** | None | Zero callers confirmed |
| Move `api/turn_state.py` → `nlu/turn_state.py` | **Low** | Low | 1 production caller, 0 tests direct |
| Move `intents/base_intents.py` → `core/policy/` | **Low** | Low | 1 production caller, 3 test files |
| Move `execution/config.py` → `core/config/` | **Low** | Low | Test-only caller |
| Move `execution/test_backend.py` → `core/tests/` | **Low** | Low | Test-only caller |
| Consolidate `durable_intents.py` → `core/policy/` | **Medium** | Medium | 15+ callers; needs compatibility re-export or bulk migration |
| Move `workflows/` → `core/workflows/` | **Medium** | Medium | `orchestrator.py` + 3 test files; protocol + registry must move together |

---

## Architectural Debt Summary

Listed by impact:

1. **`is_durable_intent` defined in two places**: `core/orchestration/persistence/durable_intents.py` wraps `core.policy.intent_policy.get_intent_durable()`. The canonical version is `core.policy.intent_policy.is_durable_intent()`. Two definitions exist; 15+ callers use the wrapper in `persistence/`. The wrapper is a compatibility indirection over itself.

2. **Two client directories**: `orchestration/clients/` retains four dead execution clients after the migration to `execution/clients/`. The `__init__.py` explicitly says they moved, but the files remain.

3. **`action_router.py` is unreferenced**: Zero production and test callers. Defines `ACTION_HANDLERS` mapping but nothing invokes it. May represent intent for future use, or may be fully superseded by `dispatcher.py`.

4. **`orchestration/contracts/` holds one dead file**: `luma_contracts.py` has zero callers; the live version is in `nlu/`.

5. **`routing/execution/` is doubly misplaced**: Configuration and test infrastructure inside a routing sub-package; neither module has routing responsibilities.
