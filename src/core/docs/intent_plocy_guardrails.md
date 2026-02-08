# INTENT POLICY GUARDRAILS (NON-NEGOTIABLE)

`src/core/config/intent_policy.yaml` is the SINGLE SOURCE OF TRUTH for:

- intent planning rules
- slot requirements
- execution sequencing
- execution modes (exploratory / committing)
- intent durability

Rules:
1. No intent behavior may be implemented in Python unless it exists in intent_policy.yaml.
2. No hard-coded intent names, slot rules, or execution conditions.
3. All changes to intent behavior MUST start by modifying intent_policy.yaml.
4. Code may only READ policy — never infer or invent logic.
5. If behavior cannot be expressed declaratively, STOP and ask.

Violations are bugs.
