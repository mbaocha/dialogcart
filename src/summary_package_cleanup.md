# Package Ownership Investigation Report

## Scope

This report assesses whether the current package structure under `core/` coherently reflects the production architecture after the ConversationEngine migration. The objective is to classify each package by its ownership status and identify structural mismatches.

---

## 1. Package Ownership

### `core/engine/` — Primary Architecture

**Why it exists:** Created during the ConversationEngine migration to house the production orchestration owner.

**Architectural responsibility:** Owns the complete turn lifecycle after the HTTP layer hands off. `ConversationEngine.process_turn()` is the single entrypoint for all orchestration. `outcome_builder.py` is a neutral utility module extracted to break a circular dependency between `orchestrator.py` and `turn_planner.py`.

**Validity:** Fully valid. This package correctly reflects its ownership.

**Files:**
- `conversation_engine.py` — production orchestration entrypoint
- `outcome_builder.py` — neutral utility (outcome dict construction), correctly co-located since it was extracted specifically to serve this boundary

---

### `core/orchestration/` — Mixed Responsibility (primary concern of this report)

**Why it exists:** Historically owned the entire orchestration lifecycle. Now that `ConversationEngine` owns orchestration, this package has fractured into multiple sub-concerns with no unified identity.

**Current contents:** HTTP API, NLU wrappers, execution dispatcher + clients, caches, planning utilities, session I/O, compatibility wrappers, and business logic utilities — spanning seven distinct responsibilities.

**Validity:** The package boundary is no longer coherent. It is a historical accumulation, not a designed boundary. Full analysis in §4.

---

### `core/planning/` — Primary Architecture

**Why it exists:** Houses the pure planning computation: turn planner, plan builder, intent resolution, business facts, missing slot derivation.

**Architectural responsibility:** Receives Luma output and session state, produces a typed decision plan. Pure — no execution side effects.

**Validity:** Valid. The sub-package structure is sound:
- `planning/orchestration/` — planner, plan builder, intent resolution, missing slots, time policy
- `planning/facts/` — business fact registry
- `planning/policy/` — policy adapters (see §3 for a mismatch)

---

### `core/execution/` — Primary Architecture (thin)

**Why it exists:** Provides `ActionRunner`, the named boundary between the engine and the execution dispatcher. Created during the ConversationEngine migration.

**Architectural responsibility:** Single named dispatch boundary from the engine to `dispatcher.execute()`.

**Validity:** Valid as a boundary. Currently only contains `ActionRunner` — intentionally thin.

---

### `core/rendering/` — Primary Architecture

**Why it exists:** All LLM text generation belongs here.

**Architectural responsibility:** Transforms decisions and execution results into natural language via `ResponseRenderer`, `availability_renderer`, `booking_confirmation_renderer`, and `llm_renderer`.

**Validity:** Valid. Clean boundary. `response_renderer.py` is the public face; the other renderers are its implementation.

---

### `core/workflows/` — Primary Architecture

**Why it exists:** Domain post-processing boundaries, created during the ConversationEngine migration.

**Architectural responsibility:** `AvailabilityWorkflow` — post-search processing (fingerprint, time-match, pagination, presentation cache). `BookingWorkflow` — post-commit slot propagation. `WorkflowRouter` — maps client names to domain routes.

**Validity:** Valid. Clean ownership.

---

### `core/session/` — Primary Architecture

**Why it exists:** Owns the schema and lifecycle of persisted session state.

**Architectural responsibility:** Session merge (`merge.py`), persistence projection (`persist.py`), invalidation (`invalidation.py`), schema helpers (`schema.py`), confirmation gate (`confirmation_gate.py`), slot utilities (`effective_slots.py`, `missing_slots.py`), appointment extensions, projection facade (`session_projector.py`).

**Validity:** Valid. However, there is a parallel `core/orchestration/session/` that is a legacy namespace (see §4).

---

### `core/policy/` — Primary Architecture

**Why it exists:** Single loader for `intent_policy.yaml` — the authoritative source of intent sequencing, slot requirements, and durability flags.

**Architectural responsibility:** `intent_policy.py` loads, caches, and exposes query functions over `core/config/intent_policy.yaml`.

**Validity:** Valid. Widely imported by `planning/`, `session/`, and `engine/`.

---

### `core/tracing/` — Supporting Infrastructure

**Why it exists:** Decision Trace framework — the primary debugging tool for orchestration behaviour.

**Architectural responsibility:** `decision_trace.py`, `invariant_trace.py`, `spine.py`, `stage_checks.py`, domain-specific emitters (`availability.py`, `browse.py`, `confirmation.py`, `merge.py`, `fingerprint.py`, etc.).

**Validity:** Valid. Self-contained.

---

### `core/routing/` — Mixed Responsibility

**Why it exists:** Originally housed all "intent → action name" and "clarification reason → template key" routing tables. Now also contains the older workflow registry that competes with `core/workflows/`.

**Architectural responsibility:**
- `intent_router.py` — intent name → action name mapping (used by `plan_builder.py`)
- `clarification_router.py` — clarification reason → template key (used by `plan_builder.py`)
- `handler_router.py` — intent → handler name resolution (used by `turn_planner.py`)
- `action_router.py` — action name → handler function mapping (planning-era dispatch table; appears bypassed by the policy-driven dispatcher)
- `routing/workflows/` — older `Workflow` base class and registry (`get_workflow`, `register_workflow`), used by `orchestrator.py:get_workflow()` for `after_execute` hooks
- `routing/intents/base_intents.py` — `is_core_intent()`, `CORE_BASE_INTENTS` constant

**Validity:** Partially valid. The routing tables consumed by the planner (`intent_router`, `clarification_router`, `handler_router`) are actively used. The `routing/workflows/` registry is separate from and parallel to `core/workflows/` (see §3).

---

### `core/nlu/` — Migration Scaffold

**Why it exists:** Created as a Phase 1 architectural boundary for NLU, intended to eventually replace `core/orchestration/nlu/`.

**Architectural responsibility:** `LumaInterpreter` — thin wrapper over `LumaClient`. Intended to consolidate context-building and error recovery from `turn_planner.py`.

**Validity:** This package has not advanced beyond Phase 1. `LumaInterpreter` is defined but has zero active callers outside `core/nlu/luma_interpreter.py` itself (confirmed: no `from core.nlu` imports found in production code). The real NLU work still lives in `core/orchestration/nlu/`. This is an incomplete migration scaffold.

---

### `core/config/` — Supporting Infrastructure

**Why it exists:** Houses YAML policy files and configuration loaders.

**Contents:** `intent_policy.yaml`, `capabilities_loader.py`, `org_resolver.py`.

**Validity:** Valid utility package. `org_resolver.py` is an unusual placement — it resolves `organization_id` from environment, which is an infrastructure concern.

---

## 2. Architectural Alignment

| Package | Classification | Evidence |
|---|---|---|
| `core/engine/` | Primary architecture | Production entrypoint; owns turn lifecycle |
| `core/planning/` | Primary architecture | Pure planning, no side effects; widely consumed |
| `core/execution/` | Primary architecture | Single dispatch boundary from engine to dispatcher |
| `core/rendering/` | Primary architecture | All LLM text flows through here |
| `core/workflows/` | Primary architecture | Domain post-processing; clean boundaries |
| `core/session/` | Primary architecture | Schema, merge, persist, gate; widely consumed |
| `core/policy/` | Primary architecture | Single YAML source of truth; widely consumed |
| `core/tracing/` | Supporting infrastructure | Debugging framework; not on critical path |
| `core/routing/` | Mixed responsibility | Active routing tables + parallel workflow registry |
| `core/config/` | Supporting infrastructure | YAML configs + config loaders |
| `core/nlu/` | Migration scaffold | Phase 1 stub; no active callers |
| `core/orchestration/` | Mixed responsibility | Seven distinct concerns in one package |

---

## 3. Package Responsibilities — Mismatches

### `core/planning/policy/` vs `core/policy/`

**Mismatch:** Two separate policy packages exist. `core/policy/` is the authoritative `intent_policy.yaml` loader, used by planning, session, engine, and tracing. `core/planning/policy/` contains older `action_policy.py` and `stage_policy.py` that read from legacy YAML files.

**Evidence:** `core/planning/policy/action_policy.py` reads from `intent_policy.yaml` as a fallback ("migration path from legacy planning configs"). `slot_contract.py` (deprecated per its own docstring) calls `load_planning_policy()`. `stage_policy.py` maps missing slots to dialog instructions — a planning concern but currently housed in a sub-package of planning rather than in `core/policy/`.

**Observation:** `core/planning/policy/` appears to be an older policy layer predating `core/policy/intent_policy.py`. Some of its consumers are deprecated; others continue to use it alongside the newer policy loader.

---

### `core/routing/workflows/` vs `core/workflows/`

**Mismatch:** Two workflow registries exist.

`core/routing/workflows/` provides `Workflow` base class, `register_workflow()`, `get_workflow()`, `has_workflow()`. Used by `orchestrator.py` for `after_execute` hooks.

`core/workflows/` provides `AvailabilityWorkflow`, `BookingWorkflow`, `WorkflowRouter` — the production workflow boundaries introduced during the ConversationEngine migration.

**Evidence:** `orchestrator.py` imports `get_workflow` from `core.routing.workflows`. Tests import from `core.routing.workflows` for registration. `core/workflows/` is consumed by `ConversationEngine` and has no connection to the routing registry.

**Observation:** These serve different purposes — `routing/workflows/` is an extension hook for post-execution side effects; `workflows/` is a domain processing boundary in the engine path. The naming collision creates confusion.

---

### `core/orchestration/clients/` vs `core/orchestration/execution/clients/`

**Mismatch:** Two client packages exist. The production path (`message.py`, `action_runner.py`, `dispatcher.py`, `AvailabilityWorkflow`, `BookingWorkflow`) imports from `core.orchestration.execution.clients`. The older `core/orchestration/clients/` package is imported by `turn_planner.py`, `catalog_cache`, `org_domain_cache`, `catalog_resolver`, and `chat.py`.

**Evidence from line counts:**
- `orchestration/clients/availability_client.py` — 86 lines (older, thinner)
- `orchestration/execution/clients/availability_client.py` — 160 lines (newer, more complete)
- `orchestration/clients/booking_client.py` — 258 lines
- `orchestration/execution/clients/booking_client.py` — 258 lines (identical size)

**Observation:** `orchestration/clients/` contains `CatalogClient`, `OrganizationClient`, `CustomerClient`, `StaffClient`, `PaymentClient` — NLU/planning-layer clients that do not have counterparts in `execution/clients/`. `execution/clients/` contains `AvailabilityClient`, `BookingClient`, `PaymentClient`, `StaffClient` — execution-layer clients. The split is partially intentional (planning clients vs execution clients) but the `AvailabilityClient` and `BookingClient` duplicates are a structural anomaly.

---

### `core/orchestration/session/` vs `core/session/`

**Mismatch:** `core/orchestration/session/` is a pass-through namespace. Its `__init__.py` re-exports `get_session`, `save_session`, `clear_session` from `session_manager.py`, which itself delegates to Redis. `core/session/session_manager.py` is a Phase 1 facade that internally imports from `core/orchestration/session/session_manager.py`.

**Evidence:** `core/session/session_manager.py` docstring: "Phase 1: thin facade over core/orchestration/session/session_manager.py." `message.py` imports `get_session`, `save_session`, `clear_session` from `core.orchestration.session`. Multiple test files also import from `core.orchestration.session`.

**Observation:** `core/session/` is the intended home for session logic, but `core/orchestration/session/` is the actual storage implementation and the primary import target. Session I/O is split across three locations: `core/orchestration/session/session_manager.py` (real implementation), `core/orchestration/session/__init__.py` (re-export), and `core/session/session_manager.py` (facade over the re-export). This is a three-level pass-through for a single function.

---

### `core/orchestration/api/session_merge.py`

**Mismatch:** This file is now entirely a re-export shim. Its docstring says "backward-compatible re-exports; implementation lives in core.session.*". The actual implementations moved to `core/session/merge.py`, `core/session/persist.py`, `core/session/effective_slots.py`. Yet `turn_planner.py` and multiple tests still import from `core.orchestration.api.session_merge`, and `message.py` re-exports `build_session_state_from_outcome` from it.

**Observation:** `session_merge.py` is a pure compatibility shim for a completed migration.

---

### `core/orchestration/api/turn_state.py` and `slot_contract.py`

`turn_state.py` — internally used by `luma_response_processor.py`. Not imported from outside `orchestration/`. Appears to be an older turn-state abstraction that predates the current planning architecture.

`slot_contract.py` — deprecated per its own docstring: "DEPRECATED: This module is being phased out in favor of the new planning modules." Still imported by `core/session/effective_slots.py` and `core/session/merge.py`.

---

## 4. `core/orchestration/` — Detailed File Classification

| File / Subpackage | Classification | Evidence |
|---|---|---|
| `orchestrator.py` | Compatibility wrapper | `handle_message()` = session loading + delegation; `plan_message()` = planning entry (active); re-exports via `noqa: F401` |
| `api/message.py` | Correctly located | HTTP boundary; production entrypoint |
| `api/main.py` | Correctly located | FastAPI app factory |
| `api/capability_boundary.py` | Correctly located | Capability runner invocation; HTTP-layer concern |
| `api/session_merge.py` | Compatibility wrapper | Pure re-export shim; implementation in `core/session/` |
| `api/turn_state.py` | Transitional | Older turn-state abstraction; only consumed by `luma_response_processor.py` |
| `api/slot_contract.py` | Legacy (deprecated) | Self-declared deprecated; still has callers in `core/session/` |
| `nlu/__init__.py` | Correctly located | Re-exports Luma client and NLU utilities used by `turn_planner.py` |
| `nlu/luma_client.py` | Correctly located | Active NLU HTTP client |
| `nlu/luma_response_processor.py` | Correctly located | Active — interprets Luma responses; used by `turn_planner.py` |
| `nlu/conversation_memory.py` | Correctly located | Active — conversation history for NLU context |
| `nlu/luma_contracts.py` | Candidate for relocation | Duplicates `orchestration/contracts/luma_contracts.py` |
| `execution/dispatcher.py` | Correctly located | Active execution dispatch; consumed by `ActionRunner` and `AvailabilityWorkflow` |
| `execution/clients/` | Correctly located | Active execution clients; production path imports from here |
| `execution/availability.py` | Legacy package | Only imported by tests; not on production path (dispatcher routes directly to clients) |
| `execution/booking.py` | Legacy package | Only imported by tests; same reason |
| `execution/confirmation.py` | Legacy package | Only imported by tests; same reason |
| `clients/` (CatalogClient, OrgClient, etc.) | Correctly located | Planning-layer clients; active callers in `turn_planner.py`, caches |
| `clients/availability_client.py` | Misplaced | Thinner version of `execution/clients/availability_client.py`; production path uses the latter |
| `clients/booking_client.py` | Misplaced | Same line count as `execution/clients/booking_client.py`; unclear which is authoritative |
| `persistence/durable_intents.py` | Candidate for relocation | Not a persistence concern; is a policy lookup. Widely imported by `session/`, `planning/`, `tracing/`. Logically belongs in `core/policy/` or `core/session/` |
| `session/session_manager.py` | Correctly located (for now) | Real Redis implementation; `core/orchestration/session/__init__.py` is its re-export namespace |
| `session/__init__.py` | Pass-through | Re-exports `get_session/save_session/clear_session` from `session_manager.py` |
| `session_ops.py` | Compatibility wrapper | Re-exported from `orchestrator.py`; implementation is neutral utility |
| `availability_fingerprint.py` | Misplaced | Availability domain logic; logically belongs in `core/workflows/availability/` or `core/session/` |
| `availability_browse.py` | Misplaced | Availability domain logic; same |
| `availability_pagination.py` | Misplaced | Availability domain logic; same |
| `temporal_proposal.py` | Misplaced | Planning-domain utility (temporal slot resolution); consumed by `business_fact_registry.py`, `plan_builder.py`, `time_resolution.py` |
| `time_resolution.py` | Misplaced | Planning-domain utility; consumed by engine and planning |
| `catalog_resolver.py` | Correctly located | Execution-time catalog ID resolution; consumed by `dispatcher.py` |
| `luma_facts_adapter.py` | Misplaced | NLU output adaptation; belongs in `core/orchestration/nlu/` |
| `cache/` | Correctly located | Catalog and org-domain caches; infrastructure |
| `errors.py` | Correctly located | Exception types; widely imported |
| `contracts/luma_contracts.py` | Candidate for relocation | Duplicates `nlu/luma_contracts.py`; one of the two should be canonical |
| `actions/booking.py` | Legacy package | No production callers found |
| `actions/cancellation.py` | Legacy package | No production callers found |
| `actions/modification.py` | Legacy package | No production callers found |

---

## 5. Package Dependency Map

```
core/orchestration/api/
    message.py  (HTTP entrypoint)
         │
         ▼
core/engine/
    conversation_engine.py  (ORCHESTRATION OWNER)
         │
         ├──────────────────────────────────────────────────────────┐
         ▼                                                          ▼
core/orchestration/          (via plan_message)           core/execution/
    orchestrator.py                                           action_runner.py  ──► core/orchestration/execution/
         │                                                                              dispatcher.py
         ▼                                                                              clients/
core/planning/
    turn_planner.py
         │
         ├───────────────────┐
         ▼                   ▼
core/policy/         core/session/
    intent_policy.py     merge.py
                         persist.py
         │               confirmation_gate.py
         ▼
core/orchestration/persistence/
    durable_intents.py  ◄── (read from core/policy/)


core/orchestration/
    availability_fingerprint.py  ◄── core/planning/facts/business_fact_registry.py
    temporal_proposal.py          ◄── core/planning/facts/business_fact_registry.py
    time_resolution.py            ◄── core/engine/, core/orchestration/

core/workflows/
    availability/workflow.py  ◄── core/engine/
    booking/workflow.py       ◄── core/engine/
    router.py                 ◄── core/engine/

core/rendering/
    response_renderer.py  ◄── core/engine/, core/planning/

core/tracing/  ◄── (cross-cutting; consumed by engine, planning, session, orchestration)

core/routing/
    intent_router.py       ◄── core/planning/
    clarification_router.py ◄── core/planning/
    handler_router.py      ◄── core/planning/
    workflows/             ◄── core/orchestration/orchestrator.py only
```

**Pass-through layers identified:**

| Pass-through | Real location |
|---|---|
| `core/orchestration/session/__init__.py` | `core/orchestration/session/session_manager.py` |
| `core/session/session_manager.py` | Wraps the above |
| `core/orchestration/api/session_merge.py` | `core/session/{merge,persist,effective_slots}.py` |
| `core/orchestration/orchestrator.py` | `ConversationEngine.process_turn()` for execution; `plan_turn()` for planning |

---

## 6. Compatibility Inventory

| Symbol | Location | Why it still exists | Active callers | Necessary? |
|---|---|---|---|---|
| `handle_message()` | `orchestrator.py:264` | Pre-migration entrypoint; tests and external callers imported it | Tests, `chat.py`, external integrations | Yes — test suite depends on it |
| `ConversationEngine.handle_turn()` | `conversation_engine.py:809` | Original stub API exposed `handle_turn()` | Unknown external callers | Likely yes until callers confirmed |
| `ConversationEngine.plan_turn()` | `conversation_engine.py:822` | Exposes planning via engine interface | Direct callers of engine | Minimal; `plan_message()` is the direct entry |
| `core/orchestration/api/session_merge.py` | `api/session_merge.py` | Callers imported `build_session_state_from_outcome` from this path before it moved | `turn_planner.py`, tests, `message.py` | Yes — active callers remain |
| `core/session/session_manager.py` (facade) | `session/session_manager.py` | Phase 1 facade over `orchestration/session/session_manager.py` | No active direct callers found | Questionable — no direct imports found |
| `core/orchestration/api/slot_contract.py` | `api/slot_contract.py` | Self-declared deprecated; bridges old slot logic to new planner | `core/session/effective_slots.py`, `core/session/merge.py` | Yes — callers remain |
| Re-exports in `orchestrator.py` (noqa: F401) | `orchestrator.py:64–79` | Symbols moved to neutral modules; existing importers unchanged | Any file importing from `core.orchestration.orchestrator` | Yes — backward compat namespace |
| `plan_message()` in `orchestrator.py` | `orchestrator.py:357` | Planning-only entry; called by `ConversationEngine.process_turn()` | `conversation_engine.py` | Yes — active on production path |

---

## 7. Historical Layers

### Layer 1 — Pre-policy action routing (`core/orchestration/actions/`, `core/orchestration/execution/{availability,booking,confirmation}.py`)

Three stub files in `orchestration/actions/` (`booking.py`, `cancellation.py`, `modification.py`) contain `execute_*` functions returning empty dicts with placeholder comments. No production callers. These appear to be scaffolding from an early design before `dispatcher.py` was built.

Similarly, `orchestration/execution/availability.py`, `booking.py`, `confirmation.py` are thin wrappers with no production callers — only tests import them directly. The production path routes through `dispatcher.py` → execution clients, not through these files.

### Layer 2 — Pre-`core/policy` policy files (`core/planning/policy/`)

`action_policy.py` reads from `intent_policy.yaml` with a "migration path from legacy" comment. `stage_policy.py` maps missing slots to dialog instructions. These predate `core/policy/intent_policy.py` and are partially superseded by it. Some callers have migrated; others still use the older interface.

### Layer 3 — Scattered business-domain utilities in `core/orchestration/`

`availability_fingerprint.py`, `availability_browse.py`, `availability_pagination.py`, `temporal_proposal.py`, `time_resolution.py`, `luma_facts_adapter.py` are domain utilities that grew inside `core/orchestration/` when it was the monolithic owner. Post-migration, they logically belong in the domain packages that consume them (`workflows/availability/` for fingerprint/browse/pagination; `planning/` for temporal/time-resolution; `orchestration/nlu/` for luma_facts_adapter).

### Layer 4 — Duplicate client namespace (`core/orchestration/clients/` vs `core/orchestration/execution/clients/`)

`orchestration/clients/` was the original client home. `execution/clients/` was added when the execution sub-package was structured. The execution-layer clients (`AvailabilityClient`, `BookingClient`) now exist in both locations. The production path uses `execution/clients/`; planning-layer clients (`CatalogClient`, `OrgClient`) only live in `orchestration/clients/`.

### Layer 5 — `core/nlu/` unstarted migration

`core/nlu/LumaInterpreter` was created as a migration scaffold to eventually own NLU interpretation. It has never progressed beyond a stub. The real work remains in `core/orchestration/nlu/`.

### Layer 6 — Session I/O split

Session I/O implementation is in `core/orchestration/session/session_manager.py`. `core/session/session_manager.py` is a Phase 1 facade over it. No active callers use the facade directly — they all import from `core.orchestration.session` or `core.session` which both reach the same implementation.

---

## 8. Candidate Package Cleanup

### `core/orchestration/actions/` — Low Risk

**Reason:** Three stub files with no production callers. Scaffold from an early design predating `dispatcher.py`.  
**Evidence:** `grep` finds no imports of `core.orchestration.actions` anywhere.  
**Scope:** 3 files, ~115 lines total. No callers to update.

---

### `core/nlu/` — Low Risk

**Reason:** Phase 1 migration scaffold that never advanced. `LumaInterpreter` has no active callers. The real NLU work is in `core/orchestration/nlu/`.  
**Evidence:** No `from core.nlu` imports found in production code. Self-described as Phase 1 stub awaiting Phase 2.  
**Scope:** 2 files. No production callers to update.

---

### `core/orchestration/api/session_merge.py` — Low Risk

**Reason:** Pure re-export shim. All implementations moved to `core/session/`. The shim exists only for callers that haven't updated their import path.  
**Evidence:** Docstring: "backward-compatible re-exports; implementation lives in core.session.*". Callers include `turn_planner.py`, tests, and `message.py` (which re-exports it again).  
**Scope:** Re-exporting only. Callers must update import paths before removal. Tests and `turn_planner.py` are affected.

---

### `core/orchestration/execution/availability.py`, `booking.py`, `confirmation.py` — Low Risk

**Reason:** Only imported by tests; not on any production path. Thin wrappers around clients that the dispatcher calls directly.  
**Evidence:** Only `test_availability_execution.py`, `test_booking_execution.py`, `test_confirmation_execution.py` import these.  
**Scope:** 3 files, ~115 lines. Test files must be updated.

---

### `core/orchestration/persistence/durable_intents.py` — Medium Risk

**Reason:** Misplaced conceptually — `is_durable_intent()` is a policy lookup, not a persistence concern. Widely imported by `session/`, `planning/`, `tracing/`, `orchestration/nlu/`.  
**Evidence:** 11 import sites across `session/`, `planning/`, `orchestration/nlu/`, `tracing/`, and tests. `durable_intents.py` internally calls `core.policy.intent_policy.get_intent_durable`.  
**Scope:** Medium — 11 active import sites must update paths.

---

### `core/orchestration/api/slot_contract.py` — Medium Risk

**Reason:** Self-declared deprecated. Still has active callers in `core/session/`.  
**Evidence:** Docstring: "DEPRECATED: This module is being phased out." Callers: `effective_slots.py`, `merge.py`.  
**Scope:** Requires updating 2 session files plus the shim itself. Behavioural risk depends on whether `promote_slots_for_intent` and `filter_slots_by_domain` have complete replacements.

---

### Duplicate `AvailabilityClient` and `BookingClient` — Medium Risk

**Reason:** Two copies of execution clients exist. Production uses `execution/clients/`; planning uses `orchestration/clients/` (for `BookingClient` only, in `turn_planner.py`). The `orchestration/clients/availability_client.py` appears unused on the production path.  
**Evidence:** Line counts: `orchestration/clients/availability_client.py` = 86 lines vs `execution/clients/availability_client.py` = 160 lines. `orchestration/clients/booking_client.py` = `execution/clients/booking_client.py` = 258 lines.  
**Scope:** Must verify `orchestration/clients/booking_client.py` and `execution/clients/booking_client.py` are equivalent before consolidation. The `availability_client.py` discrepancy (86 vs 160 lines) suggests the `orchestration/clients/` version may be missing capabilities.

---

### `core/orchestration/session/` and `core/session/session_manager.py` (three-level pass-through) — Medium Risk

**Reason:** Session I/O implementation (`orchestration/session/session_manager.py`) is accessed through two re-export layers. `core/session/session_manager.py` wraps `core/orchestration/session/` which wraps the real implementation.  
**Evidence:** `core/session/session_manager.py` docstring: "Phase 1: thin facade over core/orchestration/session/session_manager.py." No active direct callers of `core.session.session_manager` found in production code.  
**Scope:** Requires migrating callers of `core.orchestration.session` to a stable `core.session` API. High read-impact (many callers) but low logic change risk.

---

### `core/routing/workflows/` vs `core/workflows/` naming collision — Medium Risk

**Reason:** Two systems named "workflows" with no visible relationship. `core/routing/workflows/` is the extension hook (`after_execute`) registry; `core/workflows/` is the domain processing boundary in the engine path. The naming creates confusion for new engineers.  
**Evidence:** `orchestrator.py` imports `get_workflow` from `core.routing.workflows`; `ConversationEngine` imports from `core.workflows`. These are separate systems.  
**Scope:** Not a code correctness issue; a naming clarity issue. Renaming `routing/workflows/` to something like `routing/hooks/` would remove the ambiguity.

---

### `core/orchestration/` as a unified package — High Risk

**Reason:** The package currently spans HTTP API, NLU wrappers, execution, caches, planning utilities, session I/O, availability domain logic, and compatibility wrappers. There is no single coherent architectural responsibility.  
**Evidence:** 40+ files across 10 sub-packages, spanning 7 distinct architectural concerns.  
**Scope:** High — this is the most impactful structural issue and would require a multi-phase refactor touching nearly every other package.

---

## 9. Target Architecture

The package structure that best matches today's production architecture:

```
core/
    api/                         HTTP boundary (message.py, main.py, capability_boundary.py)
    engine/                      Orchestration owner (ConversationEngine, outcome_builder)
    planning/                    Pure planning (turn_planner, plan_builder, intent_resolution, facts)
    nlu/                         NLU interface (LumaClient, luma_response_processor, conversation_memory)
    execution/                   Execution boundary (ActionRunner, dispatcher, clients)
    workflows/                   Domain post-processing (AvailabilityWorkflow, BookingWorkflow, WorkflowRouter)
    session/                     Session lifecycle (merge, persist, schema, gate, invalidation, projector)
    rendering/                   LLM text generation (ResponseRenderer, availability, booking, llm)
    policy/                      Intent policy loader (intent_policy.py → intent_policy.yaml)
    routing/                     Decision tables (intent→action, clarification→template, intent→handler)
    hooks/                       Extension hooks (post-execute workflow registry; rename from routing/workflows/)
    tracing/                     Debugging framework (decision_trace, invariant_trace, spine)
    config/                      YAML files + config loaders
    infrastructure/              Cross-cutting: session I/O, caches, error types, base clients
```

In this model:
- `core/orchestration/` is dissolved into its constituent responsibilities.
- `core/orchestration/nlu/` moves to `core/nlu/` (consolidating the two NLU locations).
- `core/orchestration/execution/` moves to `core/execution/` (consolidating the two execution locations).
- `core/orchestration/session/` merges into `core/session/` or `core/infrastructure/`.
- Availability domain utilities (`fingerprint`, `browse`, `pagination`, `temporal_proposal`, `time_resolution`) move to `core/workflows/availability/` or `core/planning/`.
- `core/orchestration/api/` remains as `core/api/`.
- `core/orchestration/persistence/durable_intents.py` moves to `core/policy/`.

---

## 10. Cleanup Roadmap

Ordered by value (impact per unit of migration complexity):

| # | Package / Symbol | Reason | Benefit | Complexity | Risk |
|---|---|---|---|---|---|
| 1 | `core/orchestration/actions/` | No callers; dead scaffold | Removes noise; clarifies architecture | Trivial | Low |
| 2 | `core/nlu/` (stub) | Unstarted migration; no callers | Removes false architectural signal | Trivial | Low |
| 3 | `execution/{availability,booking,confirmation}.py` | Only test callers; not on production path | Removes duplicate dispatch paths | Low — update 3 test files | Low |
| 4 | `api/session_merge.py` (re-export shim) | Pure shim; implementation already moved | Removes indirection; clarifies session ownership | Medium — update `turn_planner.py` + test imports | Low |
| 5 | `orchestration/persistence/durable_intents.py` | Policy lookup misplaced in persistence package | Aligns with `core/policy/` ownership | Medium — 11 import sites | Medium |
| 6 | `api/slot_contract.py` (deprecated) | Self-declared deprecated; callers remain | Eliminates deprecated surface | Medium — verify replacements in 2 callers | Medium |
| 7 | `core/orchestration/api/turn_state.py` | Older abstraction; only used internally by `luma_response_processor.py` | Reduces API package surface | Low — single consumer | Low |
| 8 | Duplicate availability/booking clients | Two copies of execution clients create ambiguity | Single authoritative client location | Medium — line-count delta requires review | Medium |
| 9 | `routing/workflows/` rename to `routing/hooks/` | Naming collision with `core/workflows/` | Removes architectural confusion for new engineers | Low — rename only | Low |
| 10 | `core/orchestration/session/` merge into `core/session/` | Three-level pass-through for session I/O | Clean session ownership in `core/session/` | Medium — many callers use `core.orchestration.session` | Medium |
| 11 | `core/orchestration/` dissolution | Package no longer has coherent identity | Would align package structure with architecture | High — spans entire codebase | High |

---

```
Package investigation complete.

Report:
summary_package_cleanup.md

Architecturally coherent packages:
core/engine/, core/planning/, core/execution/, core/rendering/,
core/workflows/, core/session/, core/policy/, core/tracing/

Compatibility packages:
core/orchestration/orchestrator.py (handle_message wrapper),
core/orchestration/api/session_merge.py (re-export shim),
core/session/session_manager.py (Phase 1 facade),
core/orchestration/session/__init__.py (re-export namespace)

Legacy package boundaries:
core/orchestration/ (dissolved responsibilities — 7 distinct concerns in one package),
core/orchestration/actions/ (no callers — pre-dispatcher scaffold),
core/orchestration/execution/{availability,booking,confirmation}.py (test-only, non-production path),
core/nlu/ (unstarted migration scaffold — no active callers),
core/planning/policy/ (predates core/policy/; partially superseded)

Recommended cleanup candidates:
1. core/orchestration/actions/ — no callers (Low risk)
2. core/nlu/ stub — no callers (Low risk)
3. core/orchestration/execution/{availability,booking,confirmation}.py — test-only (Low risk)
4. core/orchestration/api/session_merge.py shim — update caller imports (Low risk)
5. core/orchestration/persistence/durable_intents.py — relocate to core/policy/ (Medium risk)
```
