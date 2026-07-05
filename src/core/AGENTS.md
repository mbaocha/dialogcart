# Core — Architectural Constitution

This document extends the root [Architectural Constitution](../../AGENTS.md). It defines **Core-specific** ownership and constraints—the rules of the booking engine. It describes who owns what within Core, not implementation details.

---

## Scope

Core is the orchestration layer between NLU, Capabilities, and the user. Everything in this document applies **only within Core**. Cross-system boundaries (NLU, Capabilities, component interaction) remain in the root constitution.

---

## Session state

Core session is the **single owner of persistent booking state** (see root Ownership).

During a single request, transient views of state may exist while NLU output is merged, planning runs, or execution completes. These are processing views, not competing sources of truth.

---

## Session merge

Core merges each NLU response with persisted session before planning or execution.

- NLU produces per-turn deltas; Core decides what is retained, promoted, or discarded.
- Merge applies when a durable booking flow is active—not based on conversation status alone.
- Session intent is immutable within a turn unless the session is explicitly reset.
- `missing_slots` are derived from intent policy and effective slots, computed once per turn.

---

## Proposals and durable slots

NLU may surface **proposals** (for example, date or time proposals). Core decides what becomes **durable** session slots.

- Unconfirmed temporal proposals are not persisted as durable slots until bound or explicitly confirmed.
- Only durable slots participate in session persistence and downstream planning completeness.
- NLU must not fabricate booking slots absent from the current utterance; Core carries forward durable state across turns.

---

## Confirmation

Core owns explicit user approval before irreversible booking actions.

- `confirmation_state` (`pending`, `confirmed`, or cleared) lives in Core session, managed through the confirmation gate.
- While confirmation is pending, each turn is classified once (accept, reject, revise, or none); downstream code branches on that decision rather than re-deriving it.
- Committing execution steps require user confirmation when policy and workflow state demand it.

---

## Availability

Core owns when an availability outcome is **trusted** for the current booking parameters.

- Trust is established through slot fingerprints, bound datetimes, or equivalent session evidence—not by assuming a prior search remains valid after slots change.
- When parameters change, availability must be re-established before committing steps proceed.
- Presentation of availability to the user is a Core responsibility; business fact `availability_ready` reflects this trust at planning time.

---

## Planner architecture

1. Business sequencing comes from policy.
2. Runtime computes business facts.
3. The planner interprets policy and must not encode intent-specific sequencing.

Business facts are derived at runtime from implementation state (session, slots, fingerprints, confirmation gates, and related workflow state). Policy consumes those facts to determine which execution step is eligible next.

If new booking behaviour is required:

1. derive or extend a business fact in the runtime fact registry,
2. express sequencing in `intent_policy.yaml`,
3. allow the generic policy interpreter to select the next execution step.

Do not introduce new intent-specific sequencing branches into planner code.

### Execution vs presentation

`plan.action` is **execution-only**. When no execution step should run, `action` must be `null`.

Presentation state is represented by `status`, `stage`, `awaiting`, `missing_slots`, and execution artifacts—not by `action`.

Do not introduce `presentation_action`. Do not use `action` as a conversation-phase label.

**Awaiting confirmation** (no commit this turn):

| Field | Value |
|-------|--------|
| `status` | `AWAITING_CONFIRMATION` |
| `stage` | `CONFIRM` |
| `awaiting` | `USER_CONFIRMATION` |
| `action` | `null` |

**User confirmed, execution allowed** — policy may select the commit step, for example `action = CONFIRM_APPOINTMENT`.

| Concern | Owner |
|--------|--------|
| What step runs next | `intent_policy.yaml` + generic selector |
| Whether a step is eligible | Business facts (`requires` in policy) |
| How facts are computed | Runtime fact registry |
| Conversation phase / UI cues | `status`, `stage`, `awaiting`, `missing_slots`, execution artifacts |
| Slot completeness, safety checks, dispatch | Planner infrastructure (not sequencing) |

---

## Intent policy

Within Core, `src/core/config/intent_policy.yaml` is the single source of truth for:

- intent planning rules and slot requirements
- execution sequencing and step modes (exploratory / committing)
- intent durability

Code may read policy and derive business facts for it—it must not invent intent behaviour or sequencing outside policy.

---

## Booking execution

Core dispatches the execution step selected by policy (availability search, booking hold, confirm, modify, cancel, and related operations).

- Execution clients perform the requested operation and return outcomes.
- Clients do not decide what the user should do next; Core merges results into session and plans the following turn.

---

## Capabilities (within Core)

When Core requires a Capability (payment, identity verification, and similar), Core activates it, receives the outcome, and merges any durable results into session. Capabilities do not own session state, planner logic, or conversation flow.
