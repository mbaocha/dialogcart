"""
Outcome Template Key Mapper

Derives deterministic template key candidates from decision and outcome objects.
"""

from typing import Any, Dict, List, Optional


def extract_intent(decision: Optional[Dict[str, Any]], outcome: Dict[str, Any]) -> str:
    """
    Extract intent name with fallback chain.

    Priority:
    1. decision["intent_name"]
    2. decision["plan"]["intent_name"]
    3. outcome["intent_name"]
    4. "GENERIC" (fallback)

    Args:
        decision: Decision dictionary (optional)
        outcome: Outcome dictionary

    Returns:
        Intent name string (UPPER_SNAKE_CASE)
    """
    # Priority 1: decision.intent_name
    if decision:
        intent = decision.get("intent_name")
        if intent and isinstance(intent, str) and intent.strip():
            return intent.strip().upper()

        # Priority 2: decision.plan.intent_name
        plan = decision.get("plan", {})
        if isinstance(plan, dict):
            intent = plan.get("intent_name")
            if intent and isinstance(intent, str) and intent.strip():
                return intent.strip().upper()

    # Priority 3: outcome.intent_name
    intent = outcome.get("intent_name")
    if intent and isinstance(intent, str) and intent.strip():
        return intent.strip().upper()

    # Priority 4: Fallback
    return "GENERIC"


def derive_outcome_template_key_candidates(
    decision: Optional[Dict[str, Any]], outcome: Dict[str, Any]
) -> List[str]:
    """
    Derive template key candidates in priority order for outcome rendering.

    Only generates candidates when outcome.status in ["EXECUTED", "FAILED"].

    Format: OUTCOME__[INTENT]__[STATE]

    Candidates (in order):
    1. OUTCOME__INTENT__STATE (only if INTENT != GENERIC)
    2. OUTCOME__STATE
    3. OUTCOME

    Args:
        decision: Decision dictionary (optional, for intent extraction)
        outcome: Outcome dictionary with status

    Returns:
        List of template key candidates (most specific first), or empty list if not EXECUTED or FAILED
    """
    # Only generate candidates for EXECUTED or FAILED status
    outcome_status = outcome.get("status")
    if outcome_status not in ("EXECUTED", "FAILED"):
        return []

    candidates = []

    # Extract intent
    intent = extract_intent(decision, outcome)

    # Use status as state (EXECUTED or FAILED)
    state = outcome_status

    # Candidate 1: Full format (OUTCOME__INTENT__STATE)
    # Only include if intent is not "GENERIC"
    if intent and intent != "GENERIC":
        candidates.append(f"OUTCOME__{intent}__{state}")

    # Candidate 2: Category + State (OUTCOME__STATE)
    candidates.append(f"OUTCOME__{state}")

    # Candidate 3: Category-only fallback (OUTCOME)
    candidates.append("OUTCOME")

    return candidates
