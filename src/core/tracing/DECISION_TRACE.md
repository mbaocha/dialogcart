# Decision Trace Framework — Architecture (v1.1)

**Status: Complete (platform capability).** Shipped under `core/tracing/` (v1.1 schema). Use Decision Trace as the primary tool for explaining orchestration behaviour; consult raw logs only for low-level operational detail.

Canonical design and developer guide for DialogCart Core orchestration debugging.

Extends the invariant tracing layer (`invariant_trace.py`). Goal: one structured trace per request that answers *why* each outcome happened, without reading raw logs.

---

## 1. Goals and Non-Goals

### Goals

- **Immutable execution record** — append-only; never rewrite history.
- **Evidence → Decision → Mutation → Outcome** — three distinct record types.
- **DAG, not tree** — shared upstream evidence; multiple decisions may depend on the same facts.
- **Stable reason codes** — machine-testable `reason_code` plus human `reason_text`.
- **Rich candidate evaluation** — every rejection explains *what* blocked it.
- **Input consumption audit** — evaluated vs ignored inputs per decision.
- **Dual summary** — concise prose + machine-readable Why Chain.
- **Debug-only** — production responses unchanged when tracing is off.

### Non-Goals

- Replacing application logging.
- Tracing NLU (Luma) internals — Core records what it *consumed*, not how Luma produced it.
- Mutable trace nodes or retroactive annotation.

---

## 2. Core Concepts

### 2.1 Three record types

| Type | Role | Mutable after emit? | Example |
|------|------|---------------------|---------|
| **Evidence** | Facts observed by a subsystem | No | `stored_fingerprint=abc`, `page_index=1` |
| **Decision** | Conclusion drawn from evidence | No | `PLAN_ACTION` winner=`null` |
| **Mutation** | State change caused by a decision | No (append event) | `page_index: 1 → 0` |

Evidence does not imply a conclusion. Decisions reference evidence by id. Mutations reference the decision that authorized them.

```
Evidence ──depends──► Decision ──causes──► Mutation
     │                    │
     └────shared──────────┴──► Decision (another branch)
```

### 2.2 Immutable execution record

The trace is an **append-only event log** materialized as a frozen graph at finalize time.

Rules:

1. **Nodes are never updated after `emit()` returns.** No patching `winner`, `reason`, or `inputs` on an existing node.
2. **Corrections are new nodes.** If a subsystem revises its view, it emits a superseding decision that references the prior node id in `supersedes_id` (optional).
3. **Mutations are events**, not fields merged into decision nodes. Each mutation is its own record linked to `decision_id`.
4. **Finalize freezes** the graph: `nodes`, `mutations`, `edges` become read-only; summary is computed once.

This mirrors audit-log semantics: the trace is a record of what Core concluded *at each point in the pipeline*, not a live mutable view.

### 2.3 DAG structure

The trace is a **directed acyclic graph (DAG)**, not a strict tree.

Edge kinds:

| Edge | From | To | Meaning |
|------|------|-----|---------|
| `child` | Decision or Evidence | Decision | Pipeline ordering / decomposition |
| `depends_on` | Decision | Evidence or Decision | Logical dependency (may be shared) |
| `causes` | Decision | Mutation | Authorized state change |

Shared evidence example: `evidence.session.fingerprint` is referenced by both `decision.fingerprint.trust` and `decision.planner.select_action` without duplication.

```
evidence.merge.slots
        ├─depends_on─┐
        │            ▼
        │     decision.fingerprint.trust
        │            │
evidence.session.cache ─depends_on─┤
                                   ▼
                          decision.planner.select_action
                                   │causes
                                   ▼
                          mutation.presentation.page_index
```

**Cycle prevention:** `emit()` rejects edges that would create a cycle. Pipeline order is naturally acyclic.

---

## 3. Activation (Debug-Only)

Tracing is **off by default**. Enable when **any** of:

| Mechanism | Example |
|-----------|---------|
| Environment | `DIALOGCART_TRACE_DECISIONS=1` (default view: **summary**) |
| Query param | `POST /message?trace=summary` · `?trace=reasoning` · `?trace=forensic` |
| Header | `X-Debug-Decision-Trace: true` (default view: **summary**) |

Legacy `?trace=decision` maps to **forensic** (full graph).

When disabled: all emit calls are no-ops; `MessageResponse` omits trace fields.

When enabled, the API always builds the full forensic trace internally and returns a **projection** for the requested view:

| View | `trace_view` | `decision_trace` | `decision_trace_text` |
|------|--------------|------------------|------------------------|
| summary (default) | `summary` | Compact structured projection | ~10–20 line text |
| reasoning | `reasoning` | Filtered evidence + decisions (no edges/persistence) | ~50–150 line text |
| forensic | `forensic` | Full v1.1 graph + session diff + stage timings | Full text export |

All views include **session diff** (changed fields only) and **timing**.

During migration, `invariant_trace` may dual-emit; target state nests invariants under decision nodes and removes the top-level field.

---

## 4. Record Schemas

### 4.1 Shared enums

**Subsystem:** `api` | `session` | `planning` | `orchestration` | `execution`

**Record kind:** `evidence` | `decision` | `mutation`

### 4.2 Evidence

Facts observed. No `winner`. No mutations.

```json
{
  "id": "evidence.session.presentation",
  "kind": "evidence",
  "subsystem": "session",
  "evidence_type": "SESSION_SNAPSHOT",
  "observed_at_stage": "pagination",
  "facts": {
    "availability_presentation.page_index": 1,
    "presented_availability.search_date": "2026-07-10",
    "last_execution_result.status": "success"
  },
  "source": "session_state",
  "child_ids": []
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Stable id, e.g. `evidence.fingerprint.current` |
| `kind` | yes | `"evidence"` |
| `subsystem` | yes | Owner |
| `evidence_type` | yes | Namespaced type, e.g. `FINGERPRINT_COMPUTED`, `LUMA_OPERATION` |
| `facts` | yes | Bounded key-value observations (redacted, size-capped) |
| `source` | no | Provenance: `session_state`, `luma_response`, `computed`, etc. |
| `observed_at_stage` | no | Pipeline stage label for orientation |
| `child_ids` | no | Child evidence (decomposition only) |

### 4.3 Decision

Conclusion drawn from evidence.

```json
{
  "id": "decision.planner.select_action",
  "kind": "decision",
  "subsystem": "planning",
  "decision_type": "PLAN_ACTION",
  "winner": null,
  "reason_code": "NO_STEP_REQUIRES_SATISFIED",
  "reason_text": "No execution step eligible; user confirmation pending",
  "candidates": [ "..." ],
  "inputs_evaluated": { "..." },
  "inputs_ignored": { "..." },
  "depends_on": ["evidence.facts.business", "evidence.fingerprint.trust"],
  "child_ids": ["decision.planner.status"],
  "invariants": [],
  "skipped": false,
  "supersedes_id": null
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Stable id |
| `kind` | yes | `"decision"` |
| `subsystem` | yes | Owner |
| `decision_type` | yes | e.g. `PLAN_ACTION`, `BIND_TIME`, `BROWSE_DETECT` |
| `winner` | yes | Selected outcome (`null` is valid) |
| `reason_code` | yes | Stable code for tests/tooling (see §5) |
| `reason_text` | yes | Human-readable explanation |
| `candidates` | no | Evaluated alternatives (see §4.5) |
| `inputs_evaluated` | no | Inputs that influenced the decision |
| `inputs_ignored` | no | Inputs present but explicitly not used |
| `depends_on` | no | Upstream evidence/decision ids (DAG edges) |
| `child_ids` | no | Downstream decision decomposition |
| `invariants` | no | Attached invariant results |
| `skipped` | no | Not applicable this turn |
| `supersedes_id` | no | Prior decision this replaces (append-only correction) |

### 4.4 Mutation (event)

State change. Always a separate record — never embedded in a decision node.

```json
{
  "id": "mutation.pagination.page_index",
  "kind": "mutation",
  "subsystem": "orchestration",
  "decision_id": "decision.pagination.handle_turn",
  "field": "availability_presentation.page_index",
  "previous": 1,
  "new": 0,
  "reason_code": "BROWSE_PREVIOUS",
  "reason_text": "User browsed to previous page",
  "presentation_only": true,
  "sequence": 4
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Unique mutation event id |
| `kind` | yes | `"mutation"` |
| `subsystem` | yes | Owner |
| `decision_id` | yes | Authorizing decision |
| `field` | yes | Dot-path field name |
| `previous` | yes | Value before (use `null` for absent) |
| `new` | yes | Value after |
| `reason_code` | yes | Stable code |
| `reason_text` | yes | Human explanation |
| `presentation_only` | no | True when mutation does not affect durable booking truth |
| `sequence` | yes | Monotonic per-turn ordering |

### 4.5 Candidate evaluation

Every candidate in a competitive decision must explain acceptance or rejection.

```json
{
  "id": "SEARCH_AVAILABILITY",
  "matched": false,
  "reason_code": "REQUIREMENT_UNSATISFIED",
  "reason_text": "availability_check_required is false",
  "blocking_requirements": ["availability_check_required"],
  "missing_requirements": [],
  "failed_predicates": [
    {
      "predicate": "flags.availability_check_required == true",
      "actual": false,
      "reason_code": "AVAILABILITY_ALREADY_READY"
    }
  ],
  "satisfied_requirements": ["service_id"],
  "missing_slots": []
}
```

| Field | When | Description |
|-------|------|-------------|
| `matched` | always | Whether this candidate won |
| `reason_code` | always | Why accepted or top-level rejection |
| `reason_text` | always | Human explanation |
| `blocking_requirements` | rejected | Policy `requires` tokens that evaluated false |
| `missing_requirements` | rejected | `requires` tokens not yet evaluable (missing evidence) |
| `failed_predicates` | rejected | Structured predicate failures |
| `satisfied_requirements` | optional | What passed (aids diff debugging) |
| `missing_slots` | rejected | Step `required_slots` not in collected set |

**Rule:** `matched: false` without at least one of `blocking_requirements`, `missing_requirements`, or `failed_predicates` is a framework violation (assert in dev/tests).

For policy step selection, `decide_from_policy_steps()` builds candidates by evaluating each step in `intent_policy.yaml` order against slots and `build_policy_execution_flags()`.

### 4.6 Invariant attachment

Invariants attach to the **decision** they validate, not a parallel flat list.

```json
{
  "invariant_id": "pagination.no_search_on_browse",
  "invariant_ok": true,
  "message": ""
}
```

---

## 5. Reason Code Registry

Reason codes are **stable strings** — safe for E2E assertions and tooling. `reason_text` may vary; `reason_code` must not change without a schema version bump.

### 5.1 Naming convention

`{DOMAIN}_{OUTCOME}` or `{DOMAIN}_{DETAIL}` — uppercase snake_case.

Examples:

| Code | Domain | Meaning |
|------|--------|---------|
| `FINGERPRINT_MATCH` | fingerprint | Stored matches current |
| `FINGERPRINT_MISMATCH` | fingerprint | Search params changed |
| `BOUND_DATETIME_PRESENT` | availability | `has_bound_booking_datetime` true |
| `AVAILABILITY_ALREADY_READY` | facts | `availability_check_required` false |
| `REQUIREMENT_UNSATISFIED` | policy | A `requires` token failed |
| `SLOTS_INCOMPLETE` | policy | `required_slots` not subset of collected |
| `CONFIRMATION_PENDING` | confirmation | Gate blocks commit |
| `BROWSE_PREVIOUS` | pagination | Page index decremented |
| `BROWSE_NEXT` | pagination | Page index incremented |
| `BIND_EXACT_TIME_MATCH` | binding | Offer matched user time |
| `BIND_NO_PRESENTED_OFFERS` | binding | No selectable set |
| `BIND_NO_USER_TIME` | binding | No exact time proposal |
| `INPUT_IGNORED_NOT_APPLICABLE` | inputs | Field present but out of scope |
| `INPUT_IGNORED_SUPERSEDED` | inputs | Another input took precedence |
| `NO_STEP_REQUIRES_SATISFIED` | planner | No policy step ready |
| `STEP_SELECTED` | planner | Policy step won |
| `MERGE_SKIPPED_NO_DURABLE_FLOW` | merge | `should_merge_session_context` false |

### 5.2 Registry location (implementation)

`core/tracing/reason_codes.py` — constants + optional validation helper `assert_known_reason_code()`.

Subsystems may add codes per PR; registry is append-only.

---

## 6. Input Consumption Model

Every decision records which inputs were **evaluated** vs **ignored**.

### 6.1 `inputs_evaluated`

Fields that materially affected the conclusion. Include key values (bounded).

```json
{
  "confirmation_state": "pending",
  "availability_ready": true,
  "flags.availability_check_required": false,
  "intent": "CREATE_APPOINTMENT"
}
```

### 6.2 `inputs_ignored`

Fields present in context but explicitly not used — **with reason**.

```json
{
  "availability_presentation.page_index": {
    "reason_code": "INPUT_IGNORED_NOT_APPLICABLE",
    "reason_text": "page_index affects presentation only; not used for plan action selection"
  },
  "slots.time": {
    "reason_code": "INPUT_IGNORED_SUPERSEDED",
    "reason_text": "time_proposal used instead of durable slots.time for binding"
  }
}
```

### 6.3 Framework helper

```python
def partition_inputs(
    available: dict[str, Any],
    evaluated_keys: set[str],
    ignored: dict[str, tuple[str, str]],  # key -> (reason_code, reason_text)
) -> tuple[dict, dict]: ...
```

This makes "why didn't `page_index` affect planning?" immediately visible in the trace.

---

## 7. Top-Level Trace Schema

```json
{
  "version": "1.1",
  "turn": {
    "user_id": "u1",
    "text": "5pm works",
    "transaction_id": "..."
  },
  "root_id": "decision.turn.outcome",
  "records": [],
  "edges": [],
  "summary": {
    "outcome": {},
    "why_text": [],
    "why_chain": []
  },
  "first_failed_invariant": null,
  "timing_ms": 0
}
```

### 7.1 `records`

Ordered append log containing all Evidence, Decision, and Mutation records. Lookup by `id`.

### 7.2 `edges`

Explicit edge list for DAG navigation (redundant with embedded `depends_on`/`child_ids` but aids graph tools):

```json
{ "from": "evidence.session.fingerprint", "to": "decision.fingerprint.trust", "kind": "depends_on" }
```

### 7.3 JSON Schema

Implementation ships `core/tracing/schemas/decision_trace_v1_1.json` derived from this document.

---

## 8. Summary Generator

Finalize produces **two** summary artifacts.

### 8.1 `why_text` — human-readable

Ordered bullet list, max ~8 items. Built from the **winning path** through the DAG:

1. Start at `decision.turn.outcome`.
2. Walk `depends_on` edges to salient ancestors (planner, binding, fingerprint).
3. Collect `reason_text` from decisions on the path.
4. Add `notable` negatives (e.g. "No SEARCH_AVAILABILITY executed") from rejected candidates on the path.

Example:

```json
"why_text": [
  "Slot binding matched 17:00 from presented availability.",
  "Availability cache remained valid (fingerprint unchanged).",
  "Planner selected no execution action because confirmation is pending.",
  "No SEARCH_AVAILABILITY executed."
]
```

### 8.2 `why_chain` — machine-readable

Linked list of typed steps: `evidence → decision → mutation → outcome`.

```json
"why_chain": [
  {
    "step": "evidence",
    "id": "evidence.merge.time_proposal",
    "evidence_type": "NLU_TIME_PROPOSAL",
    "facts": { "mode": "exact", "value": "17:00" }
  },
  {
    "step": "decision",
    "id": "decision.merge.bind_time",
    "decision_type": "BIND_TIME",
    "winner": "BIND_EXACT_TIME_MATCH",
    "reason_code": "BIND_EXACT_TIME_MATCH"
  },
  {
    "step": "mutation",
    "id": "mutation.binding.slots_time",
    "field": "slots.time",
    "previous": null,
    "new": "17:00",
    "reason_code": "BIND_EXACT_TIME_MATCH"
  },
  {
    "step": "evidence",
    "id": "evidence.fingerprint.trust",
    "evidence_type": "FINGERPRINT_COMPARISON",
    "facts": { "stored": "abc", "current": "abc", "match": true }
  },
  {
    "step": "decision",
    "id": "decision.planner.select_action",
    "decision_type": "PLAN_ACTION",
    "winner": null,
    "reason_code": "NO_STEP_REQUIRES_SATISFIED"
  },
  {
    "step": "decision",
    "id": "decision.turn.outcome",
    "decision_type": "TURN_OUTCOME",
    "winner": { "status": "AWAITING_CONFIRMATION", "action": null },
    "reason_code": "CONFIRMATION_PENDING"
  }
]
```

**Why Chain rules:**

- Only include nodes on the **critical path** to the outcome (not the full DAG).
- Order is causal: evidence before the decision it supports; mutations immediately after authorizing decision.
- E2E tests may assert on `why_chain[].reason_code` sequence.

---

## 9. Subsystem API

Module: `core/tracing/decision_trace.py`

### 9.1 Lifecycle

```python
class TurnTrace:
    @staticmethod
    def begin(*, user_id: str, text: str, transaction_id: str = "") -> TurnTrace | None

    @staticmethod
    def current() -> TurnTrace | None

    def scope(self, parent_id: str) -> TraceScope  # context manager

    def finalize(self) -> dict[str, Any]  # frozen graph + summary
```

### 9.2 Emit evidence (append-only)

```python
def emit_evidence(
    evidence_type: str,
    *,
    subsystem: str,
    facts: dict[str, Any],
    node_id: str | None = None,
    source: str = "",
    parent_id: str | None = None,
) -> str: ...
```

### 9.3 Emit decision (append-only)

```python
def decide(
    decision_type: str,
    *,
    subsystem: str,
    winner: Any,
    reason_code: str,
    reason_text: str,
    node_id: str | None = None,
    parent_id: str | None = None,
    depends_on: Sequence[str] = (),
    candidates: Sequence[Candidate] | None = None,
    inputs_evaluated: dict[str, Any] | None = None,
    inputs_ignored: dict[str, IgnoredInput] | None = None,
    invariants: Sequence[InvariantResult] | None = None,
    skipped: bool = False,
) -> str: ...
```

### 9.4 Emit mutation (append-only event)

```python
def emit_mutation(
    decision_id: str,
    *,
    subsystem: str,
    field: str,
    previous: Any,
    new: Any,
    reason_code: str,
    reason_text: str,
    presentation_only: bool = False,
) -> str: ...
```

### 9.5 Policy helper

```python
def decide_from_policy_steps(
    *,
    intent_name: str,
    steps: Sequence[dict],
    flags: dict[str, Any],
    slots: dict[str, Any],
    selected: dict | None,
    depends_on: Sequence[str] = (),
) -> str: ...
```

Builds full `candidates` with `blocking_requirements`, `missing_requirements`, `failed_predicates`, `missing_slots` per step.

### 9.6 Invariant bridge

```python
def trace_decision_stage(
    node_id: str,
    decision_type: str,
    *,
    subsystem: str,
    winner: Any,
    reason_code: str,
    reason_text: str,
    check_fn: Callable[[], Sequence[InvariantResult]],
    depends_on: Sequence[str] = (),
    **kwargs,
) -> None: ...
```

Runs checks, emits one decision with `invariants` attached. During migration, dual-emits legacy `trace_stage()`.

### 9.7 Immutability enforcement

- `TurnTrace._records` is a list append only; no dict keyed mutation of prior entries.
- `finalize()` returns `types.MappingProxyType` or deep-frozen copies.
- Debug builds: `emit_mutation` asserts `decision_id` refers to an existing decision emitted earlier in the turn.

---

## 10. Decision Inventory (DAG-Oriented)

Each row lists **evidence emitted**, **decision concluded**, and **mutations caused**. Multiple decisions may `depends_on` the same evidence id.

### 10.1 API / Turn envelope

| Evidence | Decision | Mutations |
|----------|----------|-----------|
| `evidence.api.request` | `decision.api.trace_enabled` | — |
| `evidence.session.loaded` | `decision.api.session_filter` | — |
| `evidence.outcome.payload` | `decision.turn.outcome` (root) | — |

### 10.2 Session load

| Evidence | Decision | Mutations |
|----------|----------|-----------|
| `evidence.session.snapshot` | `decision.session.load_source` | — |
| `evidence.session.payment_facts` | `decision.session.payment_facts_guard` | — |

### 10.3 Intent resolution

| Evidence | Decision | Mutations |
|----------|----------|-----------|
| `evidence.luma.intent`, `evidence.session.intent` | `decision.intent.resolve_effective` | — |
| `evidence.intent.domain_signals` | `decision.intent.session_reset` | `mutation.session.reset_*` |
| `evidence.policy.durability` | `decision.intent.non_durable_short_circuit` | — |

### 10.4 Confirmation gate

| Evidence | Decision | Mutations |
|----------|----------|-----------|
| `evidence.confirmation.state` | `decision.confirmation.gate_open` | — |
| `evidence.luma.raw_intent`, `evidence.revision.facts` | `decision.confirmation.classify_turn` | — |
| — | `decision.confirmation.reject_path` | `mutation.slots.time`, `mutation.confirmation_state` |
| `evidence.revision.fields` | `decision.confirmation.revise_invalidation` | availability artifact mutations |
| `evidence.planning.completeness` | `decision.confirmation.enter_pending` | `mutation.confirmation_state` |

### 10.5 Session merge

| Evidence | Decision | Mutations |
|----------|----------|-----------|
| `evidence.session.durable_flow` | `decision.merge.eligibility` | — |
| `evidence.merge.intent_pair` | `decision.merge.intent_immutability` | `mutation.merged.intent` |
| `evidence.merge.slot_diff` | `decision.merge.slot_additive` | per-slot mutations |
| `evidence.luma.operation` | `decision.browse.detect` | transient `availability_browse` |
| `evidence.time_proposal`, `evidence.presented.offers` | `decision.merge.bind_time` | `mutation.slots.date`, `mutation.slots.time` |
| `evidence.invalidation.trigger` | `decision.merge.invalidation` | per registry rule |

**Shared evidence:** `evidence.presented.offers` is referenced by `decision.merge.bind_time` and `decision.pagination.handle_turn`.

### 10.6 Business facts

| Evidence | Decision | Mutations |
|----------|----------|-----------|
| `evidence.facts.inputs` | `decision.facts.derive_all` | — |
| (child) | `decision.facts.availability_ready` | — |
| (child) | `decision.facts.availability_check_required` | — |
| (child) | `decision.facts.time_selection_ready` | — |
| (child) | `decision.facts.user_confirmation_required` | — |

### 10.7 Fingerprint / availability trust

| Evidence | Decision | Mutations |
|----------|----------|-----------|
| `evidence.fingerprint.slots` | `evidence.fingerprint.computed` | — |
| `evidence.fingerprint.stored`, `evidence.fingerprint.computed` | `decision.fingerprint.trust` | — |

**Shared:** `decision.fingerprint.trust` is `depends_on` for `decision.planner.select_action` and `decision.facts.availability_ready`.

### 10.8 Planner

| Evidence | Decision | Mutations |
|----------|----------|-----------|
| `evidence.planning.missing_slots` | `decision.planner.status` | — |
| `evidence.policy.steps`, `evidence.facts.business` | `decision.planner.select_action` | `mutation.plan.action` (logical; plan is outcome not session) |
| `evidence.capability.conditions` | `decision.planner.capability_block` | — |

### 10.9 Browse / pagination

| Evidence | Decision | Mutations |
|----------|----------|-----------|
| `evidence.browse.signal` | `decision.browse.resolve_direction` | — |
| `evidence.session.cache`, `evidence.browse.signal` | `decision.pagination.handle_turn` | — |
| `evidence.presentation.pages` | `decision.pagination.page_target` | `mutation.presentation.page_index` |

**Ignored input example:** `decision.planner.select_action.inputs_ignored` includes `availability_presentation.page_index` with `INPUT_IGNORED_NOT_APPLICABLE` when pagination short-circuit did not run.

### 10.10 Execution routing

| Evidence | Decision | Mutations |
|----------|----------|-----------|
| `evidence.plan.action` | `decision.execution.eligibility` | — |
| `evidence.execution.clients` | `decision.execution.client_resolve` | — |
| `evidence.execution.result` | `decision.execution.dispatch` | `mutation.session.last_execution_result`, etc. |

### 10.11 Persistence

| Evidence | Decision | Mutations |
|----------|----------|-----------|
| `evidence.outcome.status` | `decision.persist.build_session` | session field mutations |
| `evidence.policy.durability` | `decision.persist.intent_durability` | — |
| — | `decision.persist.strip_transient` | remove browse keys |
| — | `decision.persist.save` | — |
| `evidence.persist.round_trip` | `decision.persist.reload_verify` | — |

---

## 11. Integration with Invariant Framework

| Concern | Current (`invariant_trace.py`) | Target |
|---------|-------------------------------|--------|
| Structure | Linear `stages[]` | DAG `records[]` + `edges[]` |
| Invariants | Per-stage `StageRecord` | `Decision.invariants[]` |
| State changes | `allowed_mutations` metadata | `Mutation` events with `decision_id` |
| Failure | `first_failed_invariant` | Unchanged; promoted to trace root |
| E2E helpers | `format_invariant_summary` | `format_decision_summary` (text + chain) |

### Migration phases

1. **Dual emit** — `trace_decision_stage()` + legacy `trace_stage()`.
2. **API** — `decision_trace` field; `invariant_trace` optional.
3. **Nest legacy** — `decision_trace._legacy_invariant_stages`.
4. **Remove** top-level `invariant_trace`.

`stage_checks.py` remains the invariant logic source; only attachment moves to decisions.

---

## 12. Rollout Plan

| Phase | Scope | Status |
|-------|-------|--------|
| **0 — Foundation** | `decision_trace.py`, reason codes, schema, immutability guards, API wire-up | **Complete** |
| **1 — Spine** | `handle_message`, root outcome, execution eligibility, persist save/reload | **Complete** |
| **2 — Planner** | `select_next_execution_step` with full candidate predicates | **Complete** |
| **3 — Availability** | Fingerprint evidence + trust decision; browse/pagination mutations | **Complete** |
| **4 — Session** | Merge, bind_time, confirmation gate, invalidation | **Complete** |
| **5 — Invariant bridge** | Replace `trace_stage` at all call sites; invariants on decisions | Deferred — pursue only if operational gaps require it |
| **6 — E2E/DX** | `trace_helpers`, `format_decision_summary`, CLI, Mermaid, chain assertions | **Complete** |
| **7 — Cleanup** | Remove dual-emit; single `decision_trace` | Deferred — pursue only if dual-emit causes confusion in practice |

Phases 5 and 7 remain optional. Do not implement them proactively; let real-world debugging drive whether they are needed.

---

## 13. Design Constraints

1. **Append-only** — no in-place node updates; corrections via `supersedes_id`.
2. **Bounded payloads** — redact PII; cap string length; hash large blobs.
3. **No tracing side effects** — emitters never mutate session/plan state.
4. **Deterministic ids** — stable for tests (`decision.planner.select_action`).
5. **Rejected candidates must explain** — framework asserts predicate detail in dev.
6. **Presentation isolation** — `presentation_only: true` on non-durable mutations.
7. **Ownership** — subsystems emit only their own evidence/decisions/mutations.

---

## 14. Example: Full Trace Fragment

See §8 for summary output. Graph structure for a bind-time → confirmation turn:

```
evidence.merge.time_proposal
evidence.presented.offers
         \            /
          ▼          ▼
    decision.merge.bind_time ──causes──► mutation.slots.time
                                        mutation.slots.date
evidence.fingerprint.stored ──┐
evidence.fingerprint.current ┴──► decision.fingerprint.trust
                                           │
evidence.facts.business ───────────────────┼──► decision.planner.select_action
                                           │         (candidates: SEARCH*, CONFIRM*)
                                           ▼
                                  decision.planner.status
                                           │
                                           ▼
                                  decision.turn.outcome
```

---

## 15. Developer Experience

### 15.1 Enable tracing

Set any one of:

```bash
export DIALOGCART_TRACE_DECISIONS=1
```

```http
POST /api/message?trace=decision
X-Debug-Decision-Trace: true
```

In pytest:

```bash
pytest core/tests/e2e --trace-decisions
pytest core/tests/e2e/test_decision_trace_spine.py --show-decision-trace
```

Add to `.env` for tracing **and** explainability output on passing tests:

```env
DIALOGCART_TRACE_DECISIONS=1
DIALOGCART_TRACE_SHOW=1
```

When enabled, `MessageResponse.decision_trace` contains the full graph. With `DIALOGCART_TRACE_SHOW=1` or `--show-decision-trace`, pytest prints the formatted summary after each test. Failures also append the summary automatically.

### 15.2 Read `why_text`

`summary.why_text` is an ordered list of human sentences built from the winning path through the DAG (max ~8 items). Read top-to-bottom as the narrative of the turn:

1. Binding / merge conclusions (e.g. "Slot binding matched 17:00 from presented availability.")
2. Cache / fingerprint trust (e.g. "Availability cache remained valid.")
3. Planner routing (e.g. "Planner selected no execution action because confirmation is pending.")
4. Notable negatives (e.g. "No SEARCH_AVAILABILITY executed.")

Use `format_decision_summary(trace)` for the same content in test output or logs.

### 15.3 Inspect `why_chain`

`summary.why_chain` is machine-readable. Each step has `step` (`evidence` | `decision` | `mutation` | `outcome`), stable `id`, and type-specific fields including `reason_code` on decisions.

Assert in tests:

```python
codes = [
    step["reason_code"]
    for step in trace["summary"]["why_chain"]
    if step.get("step") == "decision"
]
assert "PAGINATION_HANDLED" in codes
assert "PAGINATION_SHORT_CIRCUIT" in codes
```

The formatter also prints a condensed `reason_code` arrow chain under **Why chain (reason_code)**.

### 15.4 CLI and saved responses

Save a response JSON (from E2E, curl, or integration test) and run:

```bash
python -m core.tracing.decision_trace_cli response.json
python -m core.tracing.decision_trace_cli response.json --category presentation
python -m core.tracing.decision_trace_cli response.json --mermaid
```

Filter categories: `routing`, `inference`, `presentation`, `persistence`.

### 15.5 Mermaid graph

`decision_trace_to_mermaid(trace)` emits a flowchart:

- Solid arrows (`-->`) = `depends_on` (evidence feeds decisions)
- Dashed arrows (`-.->`) = `causes` (decision authorizes mutation)

Paste into any Mermaid renderer to visualize the causal graph for a failing turn.

### 15.6 Common debugging examples

#### Why did SEARCH_AVAILABILITY run?

Look at `decision.planner.select_action`:

- **Winner** `SEARCH_AVAILABILITY` with `STEP_SELECTED` — planner chose search; check `decision.facts.derive_all` children for which prerequisite checks passed.
- **Rejected** on pagination turns — expect `decision.execution.eligibility` with `PAGINATION_SHORT_CIRCUIT` and `SEARCH_AVAILABILITY` in rejected candidates on `decision.planner.select_action`.

#### Why was pagination skipped?

Check `decision.pagination.handle_turn`:

- `PAGINATION_SKIPPED` — no browse signal (`decision.browse.resolve_direction` → `BROWSE_NOT_DETECTED`).
- `PAGINATION_HANDLED` — browse detected; follow `decision.pagination.page_target` for page index / exhausted branch.

#### Why did time binding fail?

Check `decision.merge.bind_time`:

- `BIND_TIME_MISMATCH` — user time not on the **currently presented** page (e.g. `9am` while page 2 is shown).
- `BIND_EXACT_TIME_MATCH` — offer on the active page matched (e.g. `5pm` on page 2).
- Inspect `evidence.presented.offers` facts for `page_index` and offer starts.

#### Why did confirmation start?

Check `decision.confirmation.gate_open` and `decision.confirmation.enter_pending`:

- Gate open when planner status is `AWAITING_CONFIRMATION` and slots are complete.
- `decision.planner.status` should show `AWAITING_CONFIRMATION` after successful bind.

### 15.7 E2E failure output

On assertion failure (with tracing enabled), pytest and `BookingConversation` append:

- Outcome fields from `summary.outcome`
- `why_text` bullets
- `why_chain` reason_code sequence
- `first_failed_invariant` (if any)
- Key rejected candidates from planner, pagination, and binding nodes

---

## 16. Operational Stewardship

The Decision Trace initiative is **closed**. The framework is a completed platform capability—not an active roadmap item.

### Success criterion

A developer investigating an orchestration issue should be able to explain the system's behaviour **primarily from the Decision Trace**, consulting raw logs only for low-level operational details (upstream HTTP failures, renderer fallbacks, infrastructure errors).

### Monitoring (next few weeks)

When debugging production or E2E issues:

1. Enable tracing (`DIALOGCART_TRACE_DECISIONS=1` or `?trace=decision`).
2. Read `summary.why_text` and the relevant decision nodes first.
3. **Record gaps** — questions the trace cannot answer (missing node, unclear `reason_text`, absent candidate rejection).
4. Log gaps in team notes or an issue; do not implement trace extensions until a genuine gap is confirmed.

### Enhancement policy

- **Do not** add new trace features proactively.
- **Do** emit Decision Trace records when adding new orchestration decisions (see engineering standards in `core/AGENTS.md`).
- **Do** extend the model only when operational experience reveals a repeatable blind spot.
- Prefer richer `reason_text`, stable `reason_code`s, and candidate predicates over new framework machinery.

### Superseded instrumentation

Bracket-prefixed debug logs (`[SESSION_RESET_WRITER]`, `[AVAILABILITY_PAGINATION]`, `[FINGERPRINT_*]`, `[TIME_SELECTION_*]`, etc.) were temporary aids during rollout. They have been removed or downgraded to `debug`. Use Decision Trace instead.

---

## Appendix A: Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | — | Initial design (tree, single reason field) |
| 1.1 | — | Immutable record, evidence/decision/mutation split, DAG, reason codes, rich candidates, input audit, Why Chain |
| 1.1 (complete) | 2026-07 | Full rollout (planner, availability, session, DX); initiative closed; operational stewardship |
