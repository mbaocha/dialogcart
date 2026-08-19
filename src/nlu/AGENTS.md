# NLU — Architectural Constitution

Extends the root [Architectural Constitution](../../AGENTS.md). NLU-specific ownership and constraints—not an exhaustive description of helpers or current implementation details.

**Terminology:** **NLU** is the canonical subsystem name. **Luma** is a retained product/implementation alias for NLU; older APIs, traces, recordings, or artifacts that use Luma refer to this subsystem. New architectural instructions should use **NLU**.

---

## Scope

NLU converts the current user utterance, plus optional conversation context supplied by Core, into structured semantic evidence for one request. NLU is stateless per request and does not own conversation or booking-workflow state.

NLU is the sole owner of semantic interpretation of raw user language. It owns intent and dialogue-act classification; fact extraction and normalization; semantic grounding and entity resolution; ambiguity detection; and validation of the structured semantic output it emits.

---

## Conceptual pipeline

The stable conceptual flow is:

1. **Contextualize the current turn** — consume Core-supplied context only to interpret the current utterance; context must not substitute for evidence absent from this turn.
2. **Interpret language** — classify intent and dialogue acts, including confirmation, proposal response, correction, replacement, navigation, option reference, negation, hypothetical language, and mixed acts.
3. **Extract and normalize facts** — produce structured facts expressed by the current utterance, including service, reference, date, time, and other supported entities.
4. **Ground entities** — map extracted phrases to tenant catalog identifiers or emit structured ambiguity when grounding is not sufficiently certain.
5. **Validate semantic output** — validate evidence, consistency, provenance, grounding, and ambiguity at the semantic boundary. Insufficiently validated meaning becomes structured uncertainty or clarification evidence, not a guessed fact.
6. **Emit a per-turn semantic delta** — return structured understanding to Core without persisting state or selecting a workflow action.

Implementations may organize these responsibilities into stages, groups, or passes, but those structures must preserve this ownership flow. A specialized intent group may interpret or validate language for its semantic domain; it must not own session state, workflow sequencing, execution, persistence, or user-facing wording.

---

## Stable internal ownership boundaries

- **Context handling** provides prior-turn evidence for interpretation of the current utterance. It must not carry booking state forward as if the user restated it.
- **Language interpretation** owns the semantic meaning of raw text. Downstream normalization and validation may refine structured evidence but must not silently introduce unsupported meaning.
- **Fact extraction and normalization** own current-turn semantic facts and their normalized representation. They do not decide whether a fact becomes durable workflow state.
- **Entity resolution and grounding** own mapping language to tenant catalog identifiers and reporting unresolved or ambiguous candidates. They do not choose workflow actions or fabricate a unique match.
- **Semantic validation** owns validation of structured NLU output, including internal consistency, grounding sufficiency, ambiguity, and current-turn provenance. It does not validate session lifecycle or execution eligibility.
- **Output assembly** owns the per-turn semantic contract returned to Core. It does not persist raw fact bags, mutate Core session, or encode workflow outcomes.

No stage, group, pass, post-processor, or compatibility adapter may bypass these boundaries by interpreting raw language outside the semantic pipeline or by selecting Core workflow consequences.

---

## Dialogue and workflow boundary

- NLU identifies whether language accepts, rejects, modifies, or supersedes a confirmation or proposal and identifies the semantic target.
- NLU interprets availability language, including search criteria, browse direction, and option or ordinal references. It emits structured intent and operation evidence; it never directs Core to execute a search or booking action.
- NLU identifies the semantic need for clarification and emits structured uncertainty or clarification evidence when meaning or grounding cannot be validated sufficiently.
- Core decides whether clarification, confirmation, availability search, browsing, execution, or another outcome is the next workflow action.
- Rendering produces all user-facing wording from supplied evidence.

---

## Validation boundary

- NLU owns semantic interpretation, grounding, ambiguity detection, and validation of structured semantic output.
- Core may validate the shape and workflow applicability of NLU evidence, but it must not reconstruct, reinterpret, or revalidate semantic meaning from raw text.
- Core owns workflow and session validation, proposal validity, confirmation authorization, availability validity, and execution eligibility.
- Capabilities and execution clients own validation of the external operation constraints, requests, and responses within their contracts.

---

## State and execution prohibitions

NLU must not:

- own or persist conversation, session, proposal, confirmation, availability, capability, or booking-workflow state;
- carry prior booking slots forward as current-turn facts;
- decide which semantic evidence becomes durable session state;
- select workflow actions, confirmation lifecycle consequences, availability execution, capability activation, or booking execution;
- perform persistence or external business operations;
- produce final conversational wording as a substitute for Rendering.

Core session remains the single owner of all persistent DialogCart conversation and booking-workflow state. External systems remain authoritative for their own business records.

---

## Architectural conflict policy

Before implementing a requested change, compare it with the code structure, responsibilities, and ownership boundaries defined in this instruction file and any applicable parent or nested `AGENTS.md` files.

If the requested implementation would conflict with those boundaries:

1. Stop before modifying files.
2. Identify the specific conflicting instruction.
3. Explain how the proposed implementation violates the defined code structure, responsibility, or ownership boundary.
4. Recommend an architecturally compliant alternative.
5. Wait for explicit user direction before proceeding.

Do not silently reinterpret, weaken, bypass, or override an architectural rule merely to complete an implementation.

If the conflict is uncertain rather than definite, report the concern and inspect the relevant architecture before deciding whether implementation can safely proceed.

This stop requirement applies to implementation work. Investigation, review, and diagnosis may continue in read-only form so that existing or proposed architectural conflicts can be identified and explained.

## Test execution policy

Codex must not execute tests after making changes.

This prohibition includes:

- unit, integration, end-to-end, and live-model tests;
- `pytest`, `unittest`, Jest, Vitest, Playwright, or equivalent test runners;
- test commands invoked indirectly through scripts, Makefiles, task runners, or CI helpers.

After every implementation:

1. Do not run tests.
2. Provide the exact recommended test commands for the user to execute.
3. Separate focused tests from the full regression suite.
4. Explain briefly what each command verifies.
5. Report tests as `NOT RUN — awaiting user execution`.
6. Never claim that tests pass until the user supplies the results.
7. When the user supplies test results, analyze them and make any required corrections, but still do not execute tests.

Codex may inspect, add, or modify test files. It may run non-test validation commands such as compilation, type checking, linting, formatting checks, and `git diff --check`, unless the user instructs otherwise.
