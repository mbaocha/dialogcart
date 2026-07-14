# Investigation – Session Merge Decomposition

## Source Under Investigation

`core/session/merge.py` — 2 059 lines total  
`merge_luma_with_session()` — lines 73–2 055 (1 987 lines, one function)

The function is read in full before any decomposition is proposed. All line numbers below are stable references to the current file.

---

## Stage Breakdown

The function contains 17 identifiable logical stages. The STEP numbering in comments is non-sequential and reflects organic growth (STEP 3.5 appears twice, STEP 3.4.1 appears after STEP 3.6, etc.). The table below uses the actual execution order.

| # | Stage name | Lines | Comment label in code |
|---|---|---|---|
| 0 | Preamble — snapshot session state for logging | 98–114 | *(none)* |
| 1 | Parse session intent fields | 119–167 | `STEP 1` |
| 1a | Early Luma slot pre-check (for UNKNOWN override) | 169–208 | `STEP 1.5` |
| 2 | Enforce intent authority on `merged` | 210–297 | *(intent assignment block)* |
| 3 | Merge facts (`session_facts ← luma_facts`) | 299–315 | `STEP 2` |
| 4 | Rehydrate confirmation state | 142–150 | *(inside STEP 1 block)* |
| 5 | Extract raw Luma slots from facts | 317–453 | `STEP 3` |
| 6 | Carry forward `time_constraint` from session | 423–434 | `STEP 2.5` |
| 7 | Extract semantic slots from trace/stages/entities | 595–916 | *(extraction chain)* |
| 8 | Additive slot merge (session + Luma) | 934–1 036 | `STEP 3` |
| 9 | Extract dates from context into merged_slots | 1 077–1 125 | `[FIX77]` |
| 10 | Merge temporal proposals | 1 144–1 158 | *(proposal block)* |
| 11 | Handle intent change — filter + invalidate | 1 222–1 321 | `STEP 3.6` |
| 12 | Detect modification context | 1 323–1 376 | `STEP 3.4.1` |
| 13 | Detect informational turn → possible early return | 1 380–1 512 | `STEP 3.5` |
| 14 | Resolve effective intent for planning | 1 514–1 551 | *(intent fallback)* |
| 15 | Merge session context metadata (date_roles etc.) | 1 559–1 581 | *(context merge)* |
| 16 | Promote slots (`promote_slots_for_intent`) | 1 583–1 621 | `STEP 4.1` |
| 17 | Datetime binding for CREATE_APPOINTMENT | 1 623–1 702 | *(binding block)* |
| 18 | Domain filtering + strip unconfirmed temporal slots | 1 704–1 730 | `STEP 4.1.5` |
| 19 | Compute `missing_slots` via planner | 1 736–1 999 | `STEP 4.2` |
| 20 | Compute `_effective_collected_slots` | 2 001–2 007 | *(effective slots)* |
| 21 | Assert intent match vs session | 2 014–2 036 | *(assertion)* |
| 22 | Emit decision trace | 2 038–2 053 | *(trace block)* |

---

## Responsibility Map

Each stage has a single owner concern. The table shows what each stage reads, writes to `merged`, and produces as local state.

| Stage | Responsibility | Reads from session | Writes to merged | Local output |
|---|---|---|---|---|
| 0 | Snapshot preamble | `user_id`, `slots`, `missing_slots` | *(none)* | `user_id`, `initial_session_slots`, `session_missing_slots` |
| 1 | Parse session intent fields | `intent_name` / `intent` | *(none)* | `session_intent`, `session_intent_name` |
| 1a | Early slot pre-check | (same as 1) | *(none)* | `has_extracted_slots` (used only for one conditional) |
| 2 | Enforce intent authority | *(input from stage 1)* | `intent.name`, `_effective_intent` | *(none)* |
| 3 | Facts merge | `facts` | `facts` | `merged_facts` |
| 4 | Rehydrate confirmation | `confirmation_state` | `confirmation_state` | *(none)* |
| 5 | Raw Luma slot extraction | *(none — reads merged)* | `_raw_luma_slots` | `raw_luma_slots` |
| 6 | Carry forward time_constraint | `time_constraint` | `time_constraint` | *(none)* |
| 7 | Semantic slot extraction | *(none — reads merged)* | *(none — mutates luma_slots)* | extended `luma_slots` |
| 8 | Additive slot merge | `slots` | `slots` | `merged_slots` |
| 9 | Context date extraction | *(none — reads merged.context)* | *(none — mutates merged_slots)* | *(none)* |
| 10 | Proposal merge | `date_proposal`, `time_proposal` | `date_proposal`, `time_proposal` | *(none)* |
| 11 | Intent change handling | `slots`, `context` | `slots`, `_force_recompute_missing_slots` | `merged_slots`, `intent_changed` |
| 12 | Modification context detection | `_modification_context` | `_modification_context` | `modification_context` |
| 13 | Informational turn / early return | `status`, `slots` | `slots`, `missing_slots`, `_effective_collected_slots` | `is_informational_intent`, `has_actionable_this_turn` |
| 14 | Resolve effective intent for planning | *(none — reads merged)* | `_effective_intent` | `effective_intent` |
| 15 | Context metadata merge | `context` | `context` | `context` |
| 16 | Slot promotion | *(none — reads merged_slots)* | `slots` | `promoted_slots` |
| 17 | Datetime binding (CREATE_APPOINTMENT only) | `confirmation_state` | `slots`, `resolved_datetime_range`, `time_match_outcome` | `promoted_slots`, `datetime_bound_this_turn` |
| 18 | Domain filtering + strip temporal | *(none — reads session_state)* | `slots` | `durable_slots_for_persist` |
| 19 | Missing slots computation | `slots`, proposals | `missing_slots` | `missing_slots` |
| 20 | Effective collected slots | *(none — reads durable_slots)* | `_effective_collected_slots` | *(none)* |
| 21 | Intent match assertion | `intent_name`, `status` | *(none)* | *(none)* |
| 22 | Trace emission | *(none)* | *(none)* | *(none)* |

---

## Shared-State Analysis

### The `merged` dict is the single mutable carrier

All stages read from and write to `merged`. It is the primary channel for inter-stage state. This is by design — `merged` is the Luma response copy that accumulates session-merged state before being handed to `process_luma_response`. Stages do not need to pass it as a return value; mutation is the contract.

### Critical read-after-write chains

These are the ordering constraints that a decomposition must preserve:

| Written by stage | Written key | First consumed by stage |
|---|---|---|
| 2 | `_effective_intent` | 5 (slot extraction uses it for intent-aware promotion) |
| 3 | `facts` | 5 (facts extraction reads merged facts) |
| 4 | `confirmation_state` | 17 (binding reads confirmation) |
| 5 | `_raw_luma_slots` | 12 (modification detection reads raw slots) |
| 6 | `time_constraint` | 19 (missing slots expand uses it) |
| 7 | extends `luma_slots` | 8 (additive merge reads luma_slots) |
| 8 | `slots` (merged_slots) | 10, 11 |
| 10 | `date_proposal`, `time_proposal` | 13 (informational turn resolves proposals), 17 (binding), 19 |
| 11 | `slots` (filtered) | 16, 18, 19 |
| 12 | `_modification_context` | 16 (promotion fallback reads it) |
| 13 | **returns early** | — |
| 16 | `slots` (promoted) | 17, 18 |
| 17 | `slots` (bound), `datetime_bound_this_turn` | 18 |
| 18 | `slots` (durable) | 19, 20 |
| 19 | `missing_slots` | 21 assertion, return |
| 20 | `_effective_collected_slots` | return |

### Local variables that cross stage boundaries (parameter-passing candidates)

When stages are extracted into functions, these are the values that must be passed explicitly:

| Variable | Set by stage | Consumed by stages |
|---|---|---|
| `user_id` | 0 | logging in 0, 22 |
| `initial_session_slots` | 0 | 22 (trace) |
| `session_intent` | 1 | 11 (`session_intent_name` re-derived from it) |
| `session_intent_name` | 1 | 2, 11, 13, 14, 21 |
| `session_status` | 1 | 21 |
| `luma_intent_name` | 1 | 2, 7 |
| `raw_luma_slots` / `luma_slots` | 5 | 7 (extended), 8 (merged), 12 (modification detection) |
| `merged_slots` | 8 | 9, 10, 11, 13, 14, 16 |
| `effective_intent` | 14 | 15, 16, 17, 18, 19, 20, 21 |
| `context` | 15 | 16 (promotion) |
| `promoted_slots` | 16 | 17, 18 |
| `datetime_bound_this_turn` | 17 | 18 (strip temporal) |
| `durable_slots_for_persist` | 18 | 19, 20 |
| `missing_slots` | 19 | 21, `merged["missing_slots"]` |
| `is_informational_intent` | 13 | 14 (non-core intent override) |
| `has_actionable_this_turn` | 13 | 14 (same) |
| `modification_context` | 12 | 16.1 (fallback read) |
| `intent_changed` | 11 | 19 (is_intent_change_recomputation flag) |

---

## Code Quality Observations (No Changes Required — For Reference)

The following observations are relevant to decomposition but require no behavioural change:

1. **`luma_slots` and `raw_luma_slots` alias each other** (line 452: `luma_slots = raw_luma_slots`). One name is redundant. The alias exists because stage 5 builds `raw_luma_slots` and stage 7 extends it, after which both names refer to the same dict.

2. **Semantic data extraction is duplicated**. Stages 7a (lines 700–738) and 7b (lines 783–888) both iterate `semantic_data` with nearly identical `date_refs` / `date_roles` logic. The second block was added for a "projection" path. Any extraction into a helper should not silently deduplicate — both passes must be preserved until deliberate deduplication is approved.

3. **`merged_intent_name` is re-read from `merged` at five different points** (lines 656, 679–686, 1060–1064, 1130–1137, 2019–2022). After stage 2 writes `_effective_intent`, downstream stages re-derive it each time. A local variable kept in sync would reduce the risk of staleness.

4. **`_extract_date_from_luma_response` is a nested function** (lines 465–593, 129 lines). It has no dependency on outer-scope local variables (it receives `luma_resp` explicitly). It is a module-level private function in disguise and is the cheapest extraction in the file.

5. **STEP numbering is non-sequential** in comments: the order of STEP labels is 1 → 1.5 → 2 → 3 → 2.5 → 3 → 3.5 → 3.6 → 3.4.1 → 3.5 (again) → 4 → 4.1 → 3.4.1 (again) → 4.1.5 → 4.2 → 4.1.1. This reflects patch-on-patch growth and is corrected by the stage table above.

---

## Proposed Helper Functions

Seven cohesive stages minimize parameter passing while remaining individually testable. Each stage mutates `merged` in place; parameters are only those values not already on `merged`.

---

### Helper 1 — `_merge_intent_and_confirmation(merged, session_state)`

**Combines stages:** 0-preamble, 1, 1a, 2, 4  
**Lines covered:** 98–297  
**Extracted lines:** ~200  

```python
def _merge_intent_and_confirmation(
    merged: Dict[str, Any],
    session_state: Dict[str, Any],
) -> tuple[str, str, str]:
    """
    Parse session intent, rehydrate confirmation state, enforce intent authority.

    Returns: (session_intent_name, session_status, luma_intent_name)
    These three values are consumed by downstream stages; everything else
    is written directly to merged.
    """
```

**Writes to merged:** `intent.name`, `_effective_intent`, `confirmation_state`  
**Returns:** `(session_intent_name, session_status, luma_intent_name)`

The return tuple is small — only three strings. The early slot pre-check (stage 1a) does not need extraction; it produces only `has_extracted_slots` which is used only within its local scope to determine `effective_intent_for_slot_check`.

**Risk: MEDIUM** — the intent authority enforcement block (lines 238–297) contains an `AssertionError` raise and a `logger.warning`. The logic has four branches depending on `existing_intent_name`, `session_intent_name`, `luma_intent_name`, and `is_durable_intent()`. Test coverage must exist for all four branches before extraction.

---

### Helper 2 — `_extract_luma_slots(merged, session_intent_name, luma_intent_name, session_state)`

**Combines stages:** 3 (facts merge), 5 (raw slot extraction), 6 (time_constraint carry-forward), 7 (semantic extraction)  
**Lines covered:** 299–916  
**Extracted lines:** ~620  

```python
def _extract_luma_slots(
    merged: Dict[str, Any],
    session_intent_name: str,
    luma_intent_name: str,
    session_state: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merge facts, extract raw Luma slots, carry forward time_constraint,
    and project semantic fields into slots.

    Returns: raw_luma_slots dict (also stored in merged["_raw_luma_slots"])
    """
```

**Writes to merged:** `facts`, `_raw_luma_slots`, `time_constraint` (conditional)  
**Returns:** `raw_luma_slots`

**Note on duplication**: The two semantic extraction passes (lines 700–738 and 783–888) must both be preserved in this helper. The first pass fires on `facts_obj` paths; the second fires on the `semantic_data` projected from trace/stages. Do not deduplicate until the distinction is deliberately understood.

The nested function `_extract_date_from_luma_response` (lines 465–593) should be **lifted to module level** as a first step, making it a private module function `_extract_date_from_luma_response(luma_resp)`. This is independent of the larger extraction and has zero risk.

**Risk: MEDIUM-HIGH** — the raw slot extraction block contains service_id raw/canonical normalization logic (lines 359–416) with many conditional branches. The semantic extraction block is 300 lines with two partially duplicated passes. The `_extract_date_from_luma_response` lift is LOW risk; the rest is MEDIUM-HIGH.

---

### Helper 3 — `_merge_slots(merged, session_state, raw_luma_slots, session_intent_name)`

**Combines stages:** 8 (additive merge), 9 (context dates), 10 (proposals), 11 (intent change handling)  
**Lines covered:** 934–1 321  
**Extracted lines:** ~390  

```python
def _merge_slots(
    merged: Dict[str, Any],
    session_state: Dict[str, Any],
    raw_luma_slots: Dict[str, Any],
    session_intent_name: str,
) -> tuple[Dict[str, Any], bool]:
    """
    Merge session slots with Luma slots additively, extract context dates,
    merge temporal proposals, and apply intent-change slot filtering.

    Returns: (merged_slots, intent_changed)
    """
```

**Writes to merged:** `slots`, `date_proposal`, `time_proposal`, `_force_recompute_missing_slots`, `context` (date_roles cleared on intent change)  
**Returns:** `(merged_slots, intent_changed)`

The `intent_changed` flag is needed downstream by stage 19 to know whether to log the "recomputed from new intent contract" message.

**Risk: MEDIUM** — the additive merge loop (lines 968–983) is straightforward. The service_id preservation logic (lines 985–1 024) has non-obvious conditions (candidates, invalidation calls). The intent-change filtering (stage 11) calls `filter_collected_slots_for_intent()` and calls into `apply_invalidation()` for ambiguous service — these side effects must be preserved exactly.

---

### Helper 4 — `_detect_modification_and_informational(merged, session_state, merged_slots, raw_luma_slots, session_intent_name, planning_only)`

**Combines stages:** 12 (modification context), 13 (informational turn detection)  
**Lines covered:** 1 323–1 512  
**Extracted lines:** ~190  

```python
def _detect_modification_and_informational(
    merged: Dict[str, Any],
    session_state: Dict[str, Any],
    merged_slots: Dict[str, Any],
    raw_luma_slots: Dict[str, Any],
    session_intent_name: str,
    planning_only: bool,
) -> Optional[Dict[str, Any]]:
    """
    Detect MODIFY_* modification context and write to merged.
    Detect informational turns; if detected, build and return a fully-merged
    response directly (bypassing promotion/planning).

    Returns: fully-merged response (if informational early-return), or None
             (if planning should continue normally).
    """
```

**Writes to merged:** `_modification_context`, `slots`, `missing_slots`, `_effective_collected_slots` (informational path only)  
**Returns:** `merged` if informational turn (caller returns it immediately), `None` otherwise

The early-return idiom transforms from `return _finalize_merged_luma_response(merged, luma_response)` to returning the merged dict, with the coordinator calling `_finalize_merged_luma_response` on it.

**Risk: MEDIUM** — The informational turn block (stage 13) is 130 lines and calls `expand_slots_for_planning`, `plan_intent`, and `resolve_session_proposals`. These must all be invoked in the same order. The `is_informational_intent`, `has_active_planning`, `has_actionable_this_turn`, and `is_modify_intent` flags are computed locally and consumed locally; they do not need to be returned.

The `is_informational_intent` and `has_actionable_this_turn` flags **do** flow forward to stage 14 (effective intent resolution). The helper must either return them alongside the `Optional[merged]`, or they must be re-derived in stage 14 from the written `merged` state. The cleaner option is to return them as a `NamedTuple` or a plain tuple:

```python
Returns: (early_response: Optional[Dict], is_informational: bool, has_actionable: bool)
```

---

### Helper 5 — `_promote_and_bind(merged, session_state, merged_slots, effective_intent, planning_only)`

**Combines stages:** 14 (effective intent), 15 (context merge), 16 (promotion), 17 (datetime binding), 18 (domain filter)  
**Lines covered:** 1 514–1 730  
**Extracted lines:** ~217  

```python
def _promote_and_bind(
    merged: Dict[str, Any],
    session_state: Dict[str, Any],
    merged_slots: Dict[str, Any],
    session_intent_name: str,
    is_informational_intent: bool,
    has_actionable_this_turn: bool,
    planning_only: bool,
) -> tuple[str, Dict[str, Any]]:
    """
    Resolve effective intent, merge context metadata, promote slots,
    bind offered datetime selection (CREATE_APPOINTMENT), apply domain filtering.

    Returns: (effective_intent, durable_slots_for_persist)
    """
```

**Writes to merged:** `_effective_intent`, `context`, `slots`, `resolved_datetime_range`, `time_match_outcome`, `_intentionally_dropped_slots` (via invalidation)  
**Returns:** `(effective_intent, durable_slots)`

`datetime_bound_this_turn` is internal to this helper — it is produced by stage 17 and consumed by stage 18. It does not need to be returned.

**Risk: MEDIUM** — Stage 17 (datetime binding) is the most complex block in this grouping. It calls `try_bind_offered_time_selection`, `detect_booking_revision`, `apply_post_bind_time_resolution`, and four `apply_invalidation` calls. All are only active for `CREATE_APPOINTMENT`. The logic is self-contained but has many branches.

---

### Helper 6 — `_compute_missing_slots(merged, session_state, effective_intent, durable_slots, luma_response_time_constraint, planning_only)`

**Stage:** 19 only  
**Lines covered:** 1 736–1 999  
**Extracted lines:** ~264  

```python
def _compute_missing_slots(
    merged: Dict[str, Any],
    session_state: Dict[str, Any],
    effective_intent: str,
    durable_slots: Dict[str, Any],
    luma_response_time_constraint: Any,
    planning_only: bool,
) -> List[str]:
    """
    Compute missing_slots from durable slots via planner policy.
    Applies MODIFY_BOOKING issue derivation, normalizes, and runs all invariant checks.

    Returns: missing_slots list (caller writes to merged["missing_slots"])
    """
```

**Does not write to merged** (caller writes `merged["missing_slots"] = result`). This keeps the helper pure-functional for its computation role.

The internal `HARD_INVARIANT CHECK` block (lines 1790–1855) uses `os.getenv("PYTEST_CURRENT_TEST")`. It reads `merged.get("_raw_luma_slots")` — this is a write from stage 5, so `merged` must still be passed.

The call to `_normalize_modify_booking_missing_slots` (line 1946) imports from `luma_response_processor.py`. This creates a circular-ish dependency (merge.py → luma_response_processor.py). It is pre-existing and should not be changed by this decomposition.

`luma_response_time_constraint` is the original `luma_response.get("time_constraint")` passed at line 1875. The helper receives this specific value to avoid needing the full original `luma_response` dict.

**Risk: HIGH** — This is the most assertion-dense block in the function. The `assert False` at line 1978 (slot satisfaction violation) must be preserved exactly. The MODIFY_BOOKING issue derivation (lines 1905–1930) is an edge-case branch that is easily broken by re-ordering. The invariant check that references `_CREATE_APPOINTMENT_TEMPORAL_SLOT_KEYS` (lines 1817–1841) must receive the right `missing_keys` set.

---

### Helper 7 — `_finalize_effective_slots_and_trace(merged, session_state, effective_intent, durable_slots, initial_session_slots, planning_only)`

**Combines stages:** 20 (effective collected slots), 21 (intent assertion), 22 (trace)  
**Lines covered:** 2 001–2 053  
**Extracted lines:** ~53  

```python
def _finalize_effective_slots_and_trace(
    merged: Dict[str, Any],
    session_state: Dict[str, Any],
    effective_intent: str,
    durable_slots: Dict[str, Any],
    initial_session_slots: Dict[str, Any],
    planning_only: bool,
) -> None:
    """
    Compute _effective_collected_slots, assert intent match, emit merge trace.
    """
```

**Writes to merged:** `_effective_collected_slots`  
**Risk: LOW** — The intent assertion (stage 21) will raise `AssertionError` if the invariant fails. The trace emission (stage 22) is wrapped in `try/except ImportError`. Both are safe to extract verbatim.

---

## Proposed Coordinator Function

After extraction, `merge_luma_with_session` becomes a ~40-line coordinator:

```python
def merge_luma_with_session(
    luma_response: Dict[str, Any],
    session_state: Dict[str, Any],
    planning_only: bool = False,
) -> Dict[str, Any]:
    user_id = session_state.get("user_id", "unknown") if session_state else "unknown"
    initial_session_slots = dict(session_state.get("slots", {}) or {})
    merged = luma_response.copy()

    # Stage 1: Intent continuity + confirmation rehydration
    session_intent_name, session_status, luma_intent_name = \
        _merge_intent_and_confirmation(merged, session_state)

    # Stage 2: Extract Luma slots (facts → raw → semantic)
    raw_luma_slots = _extract_luma_slots(
        merged, session_intent_name, luma_intent_name, session_state
    )

    # Stage 3: Additive slot merge + proposals + intent-change filter
    merged_slots, intent_changed = _merge_slots(
        merged, session_state, raw_luma_slots, session_intent_name
    )

    # Stage 4: Modification context + informational turn (possible early return)
    early_response, is_informational, has_actionable = \
        _detect_modification_and_informational(
            merged, session_state, merged_slots, raw_luma_slots,
            session_intent_name, planning_only
        )
    if early_response is not None:
        return _finalize_merged_luma_response(early_response, luma_response)

    # Stage 5: Promote + bind + domain filter
    effective_intent, durable_slots = _promote_and_bind(
        merged, session_state, merged_slots, session_intent_name,
        is_informational, has_actionable, planning_only
    )

    # Stage 6: Missing slots (pure computation)
    missing_slots = _compute_missing_slots(
        merged, session_state, effective_intent, durable_slots,
        luma_response.get("time_constraint"), planning_only
    )
    merged["missing_slots"] = missing_slots

    # Stage 7: Effective collected slots + intent assertion + trace
    _finalize_effective_slots_and_trace(
        merged, session_state, effective_intent, durable_slots,
        initial_session_slots, planning_only
    )

    return _finalize_merged_luma_response(merged, luma_response)
```

This is a 40-line function that is readable without scrolling. Each stage name documents the contract; each helper function is independently testable.

---

## Dependency Graph

```
luma_response, session_state, planning_only
         │
         ▼
┌─────────────────────────────────────────┐
│ _merge_intent_and_confirmation          │
│ writes: merged.intent, _effective_intent│
│         merged.confirmation_state       │
│ returns: session_intent_name,           │
│          session_status,                │
│          luma_intent_name               │
└────────────────┬────────────────────────┘
                 │ session_intent_name, luma_intent_name
                 ▼
┌─────────────────────────────────────────┐
│ _extract_luma_slots                     │
│ writes: merged.facts, _raw_luma_slots,  │
│         merged.time_constraint          │
│ returns: raw_luma_slots                 │
└────────────────┬────────────────────────┘
                 │ raw_luma_slots, session_intent_name
                 ▼
┌─────────────────────────────────────────┐
│ _merge_slots                            │
│ writes: merged.slots, date/time_proposal│
│         _force_recompute_missing_slots  │
│ returns: merged_slots, intent_changed   │
└────────────────┬────────────────────────┘
                 │ merged_slots, raw_luma_slots, session_intent_name
                 ▼
┌─────────────────────────────────────────┐
│ _detect_modification_and_informational  │
│ writes: _modification_context,          │
│         [early: slots, missing_slots,   │
│          _effective_collected_slots]    │
│ returns: early_response|None,           │
│          is_informational, has_actionable│
└───────────┬─────────────────────────────┘
            │ (if early_response: return)
            │ is_informational, has_actionable
            ▼
┌─────────────────────────────────────────┐
│ _promote_and_bind                       │
│ writes: _effective_intent, context,     │
│         slots (promoted, bound, domain- │
│         filtered), resolved_datetime_   │
│         range, time_match_outcome       │
│ returns: effective_intent, durable_slots│
└────────────────┬────────────────────────┘
                 │ effective_intent, durable_slots
                 ▼
┌─────────────────────────────────────────┐
│ _compute_missing_slots                  │
│ reads: merged._raw_luma_slots,          │
│        merged.date/time_proposal        │
│ returns: missing_slots                  │
└────────────────┬────────────────────────┘
                 │ missing_slots → merged["missing_slots"]
                 ▼
┌─────────────────────────────────────────┐
│ _finalize_effective_slots_and_trace     │
│ writes: _effective_collected_slots      │
│ (also asserts, emits trace)             │
└─────────────────────────────────────────┘
                 │
                 ▼
     _finalize_merged_luma_response(merged, luma_response)
```

---

## Recommended Extraction Order

Ordered from lowest to highest risk. Each step is independently committable and independently testable. No step depends on a prior step being complete.

### Step 1 — Lift `_extract_date_from_luma_response` to module level

**Risk: TRIVIAL**  
The nested function (lines 465–593) has no closure over outer-scope variables. It receives `luma_resp` and returns `Optional[str]`. Move it above `merge_luma_with_session` and delete the `def` inside the function body. Update the single call at line 596. No behaviour change.

**Test signal**: Any test that exercises date extraction from Luma semantic paths will confirm this is safe.

---

### Step 2 — Extract `_finalize_effective_slots_and_trace` (stages 20, 21, 22)

**Risk: LOW**  
53 lines, no inter-stage deps, no early return. The intent assertion (stage 21) is active in tests — existing tests will catch any mistake immediately. The trace emission is wrapped in `try/except ImportError`.

---

### Step 3 — Extract `_detect_modification_context` from stage 12

**Risk: LOW**  
54 lines. The MODIFY_BOOKING / MODIFY_RESERVATION detection reads `raw_luma_slots` and `merged_intent_name` and writes `merged["_modification_context"]`. It does not interact with any other stage's local variables. Extract the block (lines 1323–1376) as a sub-function called inside `_detect_modification_and_informational` (step 5 below). The session fallback (lines 1372–1376) is included.

---

### Step 4 — Extract `_merge_facts` (stage 3) and `_carry_forward_time_constraint` (stage 6)

**Risk: LOW**  
Both are 10–15-line blocks with one simple dict operation each. They are adjacent to the raw slot extraction but have no cross-dependency. Extract as private helpers, call from `_extract_luma_slots` (step 7).

---

### Step 5 — Extract `_detect_modification_and_informational` (stages 12 + 13)

**Risk: MEDIUM**  
190 lines. The early-return in stage 13 becomes `return (merged, is_informational, has_actionable)` in the helper; the coordinator receives the tuple and returns if `early_response is not None`. The informational turn path calls `expand_slots_for_planning`, `plan_intent`, and two proposal functions — all currently imported lazily inside the block. These imports become module-level or top-of-function imports in the helper.

The flags `is_informational_intent`, `has_active_planning`, `current_turn_has_new_slots`, `has_actionable_this_turn`, `is_modify_intent` are all computed locally and consumed locally (except `is_informational` and `has_actionable` which flow to stage 14). Return them as part of the result tuple.

---

### Step 6 — Extract `_compute_missing_slots` (stage 19)

**Risk: HIGH**  
264 lines. Must be extracted before the full coordinator rewrite (step 8) because this is the hardest block to get right and benefits most from isolation. The `assert False` (line 1978) and the two `assert isinstance/is not None` (lines 1950–1955) must be preserved verbatim. The HARD_INVARIANT block (lines 1790–1855) reads `merged.get("_raw_luma_slots")` — this is already written to `merged` by stage 5, so passing `merged` is sufficient.

The `luma_response.get("time_constraint")` value (line 1875) must be passed as a parameter because the original `luma_response` is not available after extraction (only `merged` is passed). Capture it in the coordinator: `luma_response_time_constraint = luma_response.get("time_constraint")`.

Test this helper in isolation with hand-crafted `merged` dicts representing each branch (MODIFY_BOOKING issues path, intent-change recomputation, CREATE_APPOINTMENT temporal proposal coverage).

---

### Step 7 — Extract `_extract_luma_slots` (stages 3, 5, 6, 7)

**Risk: MEDIUM-HIGH**  
620 lines. This is the largest helper. Extract in two sub-steps:
- 7a: Move the duplicated semantic extraction blocks (lines 700–738 and 783–888) into a sub-function `_project_semantic_slots(semantic_data, luma_slots, merged_intent_name)` that is called twice with the same arguments (preserving both passes).
- 7b: Move the service_id raw/canonical block (lines 359–416) into `_normalize_service_id(raw_luma_slots, raw_luma_response, nested_slots, promoted_slots_from_facts)`.
- 7c: Assemble `_extract_luma_slots` from these sub-functions.

---

### Step 8 — Extract `_merge_slots` (stages 8, 9, 10, 11)

**Risk: MEDIUM**  
390 lines. The invalidation calls (`apply_invalidation`) inside the service_id block and the intent-change block must remain at the same position relative to the slot merge loop. The context date extraction (stage 9, lines 1077–1125) runs after the additive merge but before intent-change filtering — this ordering is load-bearing and must be preserved.

---

### Step 9 — Extract `_promote_and_bind` (stages 14, 15, 16, 17, 18)

**Risk: MEDIUM**  
217 lines. Stage 17 (datetime binding) is only active for CREATE_APPOINTMENT and can be broken into a `_bind_datetime_for_appointment(merged, session_state, promoted_slots)` sub-function. Extract that first, then wrap into `_promote_and_bind`.

---

### Step 10 — Extract `_merge_intent_and_confirmation` (stages 0, 1, 1a, 2, 4)

**Risk: MEDIUM**  
200 lines. Save this for last because the intent enforcement block (lines 238–297) contains the `AssertionError` raise and is the guard that ensures orchestrator-set intent is preserved. It is the hardest block to verify in isolation. Write a dedicated test for each of the four branches (durable session + UNKNOWN luma, durable session + concrete luma mismatch, non-durable session, no session intent) before extracting.

---

## Risk Assessment Summary

| Helper | Extracted lines | Risk | Primary risk driver |
|---|---|---|---|
| Lift `_extract_date_from_luma_response` | 129 | **Trivial** | No deps; already fully encapsulated |
| `_finalize_effective_slots_and_trace` | 53 | **Low** | Assertion will catch any mistake |
| `_detect_modification_context` (sub-fn) | 54 | **Low** | Isolated, no early return |
| `_merge_facts` + `_carry_forward_time_constraint` | 25 | **Low** | Single dict operations |
| `_detect_modification_and_informational` | 190 | **Medium** | Early-return conversion, multiple imports |
| `_compute_missing_slots` | 264 | **High** | `assert False`, invariant checks, MODIFY_BOOKING edge case |
| `_extract_luma_slots` | 620 | **Medium-High** | Duplicated semantic blocks, service_id normalization |
| `_merge_slots` | 390 | **Medium** | Invalidation ordering, intent-change filter |
| `_promote_and_bind` | 217 | **Medium** | Datetime binding, multiple invalidation calls |
| `_merge_intent_and_confirmation` | 200 | **Medium** | AssertionError raise, four-branch intent enforcement |

**Total extracted**: ~2 142 lines across 10 helpers (exceeds 1 987 because some lines are shared between step groupings and sub-functions).  
**Coordinator after extraction**: ~40 lines.

---

## Constraints Confirmed

- All code remains within `core/session/merge.py`.
- No behaviour changes.
- No package restructuring.
- No module moves.
- The existing public API (`merge_luma_with_session`, `should_merge_session_context`, `_finalize_merged_luma_response`) is unchanged.
- The backward-compatibility alias at line 2058 (`merge_session_with_luma_response`) is preserved.
