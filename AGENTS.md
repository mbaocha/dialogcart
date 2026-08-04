# DialogCart — Architectural Constitution

This document defines stable ownership boundaries between the major components of DialogCart. It describes **who owns what**, not how features are implemented. Subsystem-specific rules live in nested `AGENTS.md` files.

---

## NLU

NLU turns a user utterance (plus optional context supplied by Core) into structured understanding for a single request.

**Responsible for:**

- Intent classification
- Fact extraction (dates, times, services, references, and other entities expressed in the message)
- Entity resolution (mapping extracted phrases to tenant catalog identifiers)

**Not responsible for:**

- Session state
- Planning
- Booking execution

NLU is **stateless per request**. When Core provides conversation context, NLU may use it to interpret the **current** utterance—for example, resolving follow-up phrasing, continuing an active booking intent, or refining informational queries.

For availability interactions, NLU emits a single `AVAILABILITY` intent and an optional generic `operation` field (for example `browse_next`, `browse_previous`). NLU classifies user language; it does not instruct Core to execute availability search. See [`src/core/orchestration/contracts/AVAILABILITY_INTERACTION_CONTRACT.md`](src/core/orchestration/contracts/AVAILABILITY_INTERACTION_CONTRACT.md).

NLU must **not fabricate booking slots** that are not evidenced by the current user utterance. Context informs interpretation; it does not substitute for what the user said this turn. Core remains responsible for carrying forward booking state across turns.

---

## Core

Core is the orchestration layer. It owns the full booking conversation lifecycle after NLU returns.

**Single owner of:**

- Session state
- Session merge (combining NLU output with persisted session)
- Planning (what happens next in the conversation)
- Booking state (collected slots, confirmation, availability, and related workflow state)
- Confirmation (explicit user approval before irreversible actions)
- Booking execution (availability search, booking creation, cancellation, and related operations)

Core builds conversation context for NLU, merges NLU results into session, decides the next action, invokes execution, and persists durable state between turns.

**Debugging:** Core ships a completed Decision Trace framework (`core/tracing/`). When investigating orchestration behaviour, use `decision_trace` (enable with `DIALOGCART_TRACE_DECISIONS=1`) before reading raw logs. Standards live in [`src/core/AGENTS.md`](src/core/AGENTS.md) and [`src/core/tracing/DECISION_TRACE.md`](src/core/tracing/DECISION_TRACE.md).

Core-specific architectural rules live in [`src/core/AGENTS.md`](src/core/AGENTS.md).

---

## Capabilities

Capabilities are external business operations invoked when Core requires them—for example, payment collection or identity verification.

**Responsible for:**

- Executing the business operation they encapsulate
- Returning outcomes to Core

**Not responsible for:**

- Session state
- Planner logic
- Conversation state

Capabilities do not decide what the user should do next. Core activates a capability, receives its result, and merges any durable outcomes into session according to Core rules.

---

## Ownership

Each **persistent** piece of booking state has exactly one owner: **Core session**.

During a single request, transient representations of state may exist while NLU output is merged, planning runs, or execution completes. These are processing views, not competing sources of truth.

Authoritative booking state lives only in Core session. NLU produces per-turn deltas. Capabilities produce operation results. Core merges, persists, and interprets both.

---

## Component interaction

```
User message
    → Core (loads session, builds context)
    → NLU (intent, facts, entity resolution)
    → Core (merge, plan, confirm, execute)
    → Capabilities (when required)
    → Core (persist session, respond)
```

Nested documentation may add rules for NLU extraction or individual capabilities. Core rules live in [`src/core/AGENTS.md`](src/core/AGENTS.md). Nested documents must not contradict the boundaries above.
