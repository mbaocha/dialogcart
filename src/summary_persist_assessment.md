# Investigation – `persist.py` Refactor Worthiness

## Source Under Investigation

`core/session/persist.py` — 923 lines  
Primary function: `build_session_state_from_outcome()` — 802 lines (L121–922)

**Method:** static analysis only. No code changes. No tests. No behavioural redesign proposals.

**Comparison baseline:** post-Phase-2 `merge_luma_with_session()` in `core/session/merge.py` (coordinator ~143 lines; module still ~2 096 lines with extracted helpers).

**Note on prior hotspot report:** `summary_architecture_hotspots.md` ranked persist as Priority 2 / “accidental” with “10 concerns in one function.” That assessment is **partially stale**: substantial persistence logic has already been extracted into sibling modules. This investigation re-measures the *current* state.

---

## 1. Responsibility analysis

### What already lives outside `build_session_state_from_outcome`

| Module | Role | Size |
|---|---|---|
| `intent_persist.py` | Durable intent resolution, READY/EXECUTED clear gates, final intent | 197 lines / 4 funcs |
| `missing_slots.py` | Recompute / resolve missing_slots for persist | 150 lines / 3 funcs |
| `appointment_extensions.py` | CREATE_APPOINTMENT extras (availability browse, confirmation pending, booking_id) | 492 lines / 11 funcs |
| `schema.py` | Guards, serializable facts, debug flags | 65 lines |
| In-file helpers | Service candidates extract/apply; EXECUTED outcome normalize | ~77 lines |

Persistence **domain decisions** (intent durability, missing-slot recomputation, appointment extensions) are already decomposed. What remains in the 802-line function is largely **assembly and bookkeeping**.

### Stages still inside `build_session_state_from_outcome`

Ordered pipeline (approximate line spans):

| Stage | Lines (approx) | Kind |
|---|---|---|
| Empty-Luma preserve / outcome guards | 149–172 | Guard / early return |
| EXECUTED normalize + entry diagnostics | 173–194 | Normalize + log |
| Facts merge (previous ⊕ outcome) | 195–221 | Assembly |
| Slots resolve + fallbacks | 222–266 | Assembly |
| Intent resolve + temporal strip + lifecycle clear | 267–294 | **Delegated** + gate |
| Missing slots resolve | 295–304 | **Delegated** |
| Status map | 305–317 | Assembly |
| Context `date_roles` | 318–336 | Assembly |
| `awaiting_slot` / `slot_attempts` load | 337–361 | Carry-forward |
| `last_filled_slot` metadata | 362–383 | Metadata |
| Clear awaiting_slot / satisfied attempts | 384–462 | Bookkeeping (~80 lines) |
| Final intent + `active_capability` | 463–483 | **Delegated** + small rule |
| Build durable vs ephemeral session dict | 486–555 | Assembly core |
| Proposals / constraints / candidates / guards | 556–575 | Assembly + **delegated** |
| SESSION_WRITE log | 576–599 | Observability |
| Duplicate `date_constraint` write | 600–604 | Redundant (already set above) |
| Appointment extensions | 605–614 | **Delegated** |
| Before-persist trace | 615–642 | Observability |
| Modification context + context attach | 643–658 | Assembly |
| Debug snapshot + invariant checks | 659–770 | Test/debug only (~112 lines) |
| Slot-attempts increment + fact mirror | 771–882 | Bookkeeping + heavy debug (~112 lines) |
| Conversation carry + strip transient browse | 883–897 | Assembly |
| `trace_stage(check_persistence)` | 898–921 | Observability |
| Return | 922 | |

### Verdict on complexity type

**One coherent persistence pipeline**, not a pile of unrelated responsibilities.

Evidence:

- Single output: one session dict (or `None` / previous-state preserve).
- Linear data flow: inputs → resolve slots/intent/missing → construct dict → attach extras → observability → return.
- Early exits are lifecycle gates (`None` clear, preserve previous), not divergent business engines.
- Hard rules already live in siblings; the function mostly **wires** them.

What *looks* like “many concerns” is mostly:

1. Many **session keys** written in one place (inherent to a session projector).
2. **Comment / diagnostic bulk** (~128 comment lines + ~100 blanks + large debug blocks).
3. **Leftover UX bookkeeping** (`awaiting_slot`, `slot_attempts`) that is verbose but still part of “what we persist.”

Contrast with pre-refactor merge: many independent decision engines (intent authority, semantic extraction, informational early return, bind/invalidate, missing-slot invariants) with shared mutable `merged` and order-sensitive side effects.

---

## 2. Dependency analysis

### Inbound

| Consumer | Role |
|---|---|
| `session_projector.SessionProjector.project` | Production façade used by `message.py` |
| Many session/planning/execution tests | Direct calls for persistence contracts |
| Comments in merge / turn_planner / workflows | Documentation only |

Production write path is already behind `SessionProjector` (“Phase 1 architectural boundary”). Further internal decomposition is not required to establish a module boundary.

### Outbound (from `persist.py`)

- `durable_intents` — filter/gate slots by durable intent  
- `schema` — serializable facts / guards  
- `intent_persist` / `missing_slots` / `appointment_extensions` — extracted stages  
- `temporal_proposal` — strip unconfirmed temporal slots; resolve proposals  
- `tracing` — SESSION_WRITE compact log; invariant `trace_stage`

Fan-out is moderate and appropriate for a persistence assembler. It is **not** an integration god-module like `turn_planner` or Luma `resolve_service`.

### Ordering constraints (why careless extraction is costly)

Critical order dependencies remain in the body:

1. Lifecycle clear gates must run **after** intent resolve / temporal strip.  
2. Session dict construction must precede proposal/extension attaches.  
3. Slot-attempts increment must run **last** among mutations (comments explicitly require this).  
4. Debug invariants intentionally run near the end, before retry increment.

These are pipeline constraints, not evidence of unrelated mixed domains — but they raise the **cost** of decomposition relative to merge’s more separable stages.

---

## 3. Comparison with post-refactor `merge_luma_with_session`

| Metric | `build_session_state_from_outcome` | `merge_luma_with_session` (post Phase 2) |
|---|---|---|
| Function lines | 802 | ~143 coordinator |
| Module lines | 923 (`persist.py` alone) | ~2 096 (helpers retained) |
| Persistence *layer* total (persist + siblings) | ~1 827 | n/a |
| Structure | Mostly inline pipeline + a few local helpers | Named stage helpers + coordinator |
| Control character | Assemble-and-write; ~5 early returns | Multi-stage decisions; early informational return; many asserts/raises |
| `raise` / `assert` density | Low (debug AssertionError path) | High (intent authority, durability, missing slots) |
| Why it was long | Session key assembly + debug/bookkeeping | Branching merge policy across turns |
| Prior extraction | **Already substantial** (intent, missing, appointments) | Was a true monolith; Phase 1–2 justified |

**Implication:** Merge decomposition paid for itself because the coordinator became readable *and* stages were independently reason-about-able under behavioural risk. Persist’s remaining bulk is mostly sequential assembly; extracting it would shrink line counts without creating equivalent reasoning boundaries.

Hotspot claim “identical structural problem to merge” is **not accurate** for the current tree.

---

## 4. Decomposition candidates (if ever revisited)

Natural boundaries **inside** the remaining function — mechanical lifts only, no redesign:

| Candidate | Approx. size | Risk | Benefit |
|---|---|---|---|
| `_resolve_slots_for_persist(...)` | ~45 | Low | Mild clarity |
| `_clear_awaiting_slot_if_filled(...)` | ~60 | Low–Med | Isolates legacy awaiting_slot |
| `_build_base_session_state(...)` (durable/ephemeral dict) | ~70 | Med | Core shape in one place |
| `_run_persistence_debug_invariants(...)` | ~112 | Low | Removes test/debug noise from happy path |
| `_increment_slot_attempts(...)` | ~112 | Med | Order-sensitive; heavy debug today |
| Deduplicate `date_constraint` assignment | 5 | Trivial | Tiny cleanup |

**Not recommended as a project wave:** moving appointment/intent/missing logic again (already extracted), or a merge-style “Phase 2 coordinator of 10 helpers.”

`SessionProjector` already exists; “Phase 2 consolidate projection logic here” should **not** be interpreted as a mandate for major `persist.py` surgery without a clearer payoff.

---

## 5. Cost / benefit assessment

| Factor | Assessment |
|---|---|
| Maintenance pain today | Moderate — file is long, but stages are linear and searchable; hard rules live in siblings |
| Benefit of **major** decomposition | Low — would mostly rename sequential blocks; little new testability |
| Benefit of **minor** lifts (debug / awaiting_slot / attempts) | Low–moderate readability only |
| Refactor cost | Medium–high: many tests call persist directly; order-sensitive slot_attempts / clear gates; easy to break SESSION_WRITE / invariant paths |
| Opportunity cost | High — merge just finished a justified decomposition; larger monoliths (`turn_planner`, Luma `resolve_service`) remain |
| Risk of unnecessary refactor | **High** if driven only by line count or the outdated hotspot ranking |

**Net:** expected maintenance benefit of further decomposition does **not** justify a planned refactor now.

---

## 6. Recommendation

### **Leave as-is**

`build_session_state_from_outcome` is long, but its complexity is largely **justified as a single persistence assembly pipeline**. The accidental monolith problem that justified merge Phase 1–2 does not apply here to the same degree: domain logic is already split out; what remains is coherent write-path wiring plus verbose debug/bookkeeping.

Do **not** schedule major decomposition. Optional mechanical noise extraction (debug invariants / slot_attempts helper) is acceptable only as opportunistic cleanup when already editing those lines — not as a standalone initiative.

---

## Summary table

| Question | Answer |
|---|---|
| Many unrelated responsibilities? | No — one write pipeline + already-extracted siblings |
| Complexity justified? | Mostly yes (assembly + ordering); line count inflated by comments/debug |
| Natural extraction boundaries? | Yes, but low value (debug, awaiting_slot, attempts) |
| Compare to merge? | Merge needed major decomp; persist does not |
| Recommendation | **Leave as-is** |
| Expected benefit of leaving as-is | Avoid high-risk, low-payoff refactor; keep focus on true monoliths |
