"""
Luma Response Processor

Interprets Luma API responses and produces decision plans.

This module is pure and side-effect free:
- No external API calls
- No state mutation
- Deterministic interpretation of Luma responses

Responsibilities:
- Clarification interpretation (reason, issues, context)
- CLARIFY outcome construction
- Intent extraction and validation
- Building decision plans with status, allowed_actions, blocked_actions, awaiting
"""

import logging
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List, Set

import yaml

from core.routing import get_template_key, get_action_name
from core.orchestration.errors import UnsupportedIntentError
from luma.clarification.reasons import ClarificationReason

logger = logging.getLogger(__name__)

# Cache for intent execution config (loaded once per process)
_intent_execution_cache: Optional[Dict[str, Any]] = None
_cache_lock = threading.Lock()


def _load_intent_execution_config() -> Dict[str, Any]:
    """
    Load intent execution configuration from YAML file (cached at module level).
    
    Thread-safe lazy loading: loads once on first access, reuses cached data
    for subsequent calls. Zero YAML I/O on request path after initial load.
    
    Returns:
        Dictionary with intent execution config (intents -> commit, fallbacks)
    """
    global _intent_execution_cache
    
    # Fast path: return cached data if already loaded
    if _intent_execution_cache is not None:
        return _intent_execution_cache
    
    # Slow path: load and cache (thread-safe)
    with _cache_lock:
        # Double-check after acquiring lock (another thread may have loaded it)
        if _intent_execution_cache is not None:
            return _intent_execution_cache
        
        # Load YAML file
        config_dir = Path(__file__).resolve().parent.parent / "config"
        config_path = config_dir / "intent_execution.yaml"
        
        if not config_path.exists():
            logger.warning(
                f"intent_execution.yaml not found at {config_path}, "
                "using empty config (no commit actions or fallbacks)"
            )
            _intent_execution_cache = {}
            return _intent_execution_cache
        
        with config_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        
        # Extract intents dict from YAML (structure: {intents: {INTENT_NAME: {...}}})
        _intent_execution_cache = raw.get("intents", {}) if isinstance(raw, dict) else {}
        return _intent_execution_cache


def _extract_missing_slots(luma_response: Dict[str, Any]) -> List[str]:
    """
    Extract missing slots from Luma response.
    
    Checks multiple sources:
    1. Direct missing_slots field in response
    2. Intent result missing_slots
    3. Issues dict (slots with "missing" value)
    
    Args:
        luma_response: Luma API response
        
    Returns:
        List of missing slot names
    """
    missing_slots: List[str] = []
    
    # Check direct missing_slots field
    if "missing_slots" in luma_response:
        direct_missing = luma_response.get("missing_slots")
        if isinstance(direct_missing, list):
            missing_slots.extend(direct_missing)
    
    # Check intent result
    intent = luma_response.get("intent", {})
    if isinstance(intent, dict) and "missing_slots" in intent:
        intent_missing = intent.get("missing_slots")
        if isinstance(intent_missing, list):
            missing_slots.extend(intent_missing)
    
    # Check issues dict (slots with "missing" value)
    issues = luma_response.get("issues", {})
    if isinstance(issues, dict):
        for slot_name, slot_value in issues.items():
            if slot_value == "missing" and slot_name not in missing_slots:
                missing_slots.append(slot_name)
    
    # Deduplicate and return
    return list(set(missing_slots))


def _evaluate_fallbacks(
    intent_config: Dict[str, Any],
    missing_slots: List[str]
) -> List[str]:
    """
    Evaluate fallback actions based on missing slots.
    
    Matches fallback.when_missing.any_of against missing_slots.
    Returns list of action names for matching fallbacks.
    
    Args:
        intent_config: Intent config from intent_execution.yaml
        missing_slots: List of missing slot names from Luma
        
    Returns:
        List of allowed fallback action names
    """
    allowed_actions: List[str] = []
    fallbacks = intent_config.get("fallbacks", [])
    
    if not isinstance(fallbacks, list):
        return allowed_actions
    
    missing_slots_set = set(missing_slots)
    
    for fallback in fallbacks:
        if not isinstance(fallback, dict):
            continue
        
        action = fallback.get("action")
        if not action:
            continue
        
        when_missing = fallback.get("when_missing", {})
        if not isinstance(when_missing, dict):
            continue
        
        any_of = when_missing.get("any_of", [])
        if not isinstance(any_of, list):
            continue
        
        # Check if any of the required slots are missing
        if any(slot in missing_slots_set for slot in any_of):
            allowed_actions.append(action)
    
    return allowed_actions


def _build_decision_plan(
    intent_name: str,
    luma_response: Dict[str, Any],
    domain: str
) -> Dict[str, Any]:
    """
    Build a decision plan from Luma response and intent execution config.
    
    Applies rules:
    - commit.action is the irreversible commit step
    - Block commit when needs_clarification == true OR booking.confirmation_state != "confirmed"
    - Evaluate fallbacks by matching when_missing.any_of against missing_slots
    - Allow matching fallback actions (non-destructive)
    
    Args:
        intent_name: Intent name from Luma
        luma_response: Luma API response
        domain: Domain for template key routing
        
    Returns:
        Decision plan dictionary with:
        - status: READY, NEEDS_CLARIFICATION, or AWAITING_CONFIRMATION
        - allowed_actions: List of allowed action names
        - blocked_actions: List of blocked action names
        - awaiting: USER_CONFIRMATION or null
    """
    # Load intent execution config
    intent_configs = _load_intent_execution_config()
    intent_config = intent_configs.get(intent_name, {})
    
    # Extract missing slots
    missing_slots = _extract_missing_slots(luma_response)
    
    # Determine status
    needs_clarification = luma_response.get("needs_clarification", False)
    booking = luma_response.get("booking", {})
    confirmation_state = booking.get("confirmation_state") if isinstance(booking, dict) else None
    
    # Determine status
    if needs_clarification:
        status = "NEEDS_CLARIFICATION"
    elif confirmation_state == "pending":
        status = "AWAITING_CONFIRMATION"
    else:
        status = "READY"
    
    # Get commit action
    commit_config = intent_config.get("commit", {})
    commit_action = commit_config.get("action") if isinstance(commit_config, dict) else None
    
    # Determine allowed and blocked actions
    allowed_actions: List[str] = []
    blocked_actions: List[str] = []
    
    # Evaluate fallbacks (always allowed if matching)
    fallback_actions = _evaluate_fallbacks(intent_config, missing_slots)
    allowed_actions.extend(fallback_actions)
    
    # Commit action blocking rules
    if commit_action:
        # Block commit when:
        # 1. needs_clarification == true
        # 2. booking.confirmation_state != "confirmed"
        should_block_commit = (
            needs_clarification or
            confirmation_state != "confirmed"
        )
        
        if should_block_commit:
            blocked_actions.append(commit_action)
        else:
            allowed_actions.append(commit_action)
    
    # Deduplicate
    allowed_actions = list(set(allowed_actions))
    blocked_actions = list(set(blocked_actions))
    
    # Determine awaiting
    awaiting = "USER_CONFIRMATION" if confirmation_state == "pending" else None
    
    return {
        "status": status,
        "allowed_actions": allowed_actions,
        "blocked_actions": blocked_actions,
        "awaiting": awaiting
    }


def _extract_clarification_data(
    clarification_reason: Optional[str],
    issues: Dict[str, Any],
    clarification_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Extract structured clarification data from Luma response.

    Builds a structured data object with:
    - reason: Stable enum-like value (e.g., "MISSING_TIME", "MULTIPLE_MATCHES")
    - missing: Explicit list of required fields that are missing (e.g., ["time"])
    - ambiguous: List of ambiguous fields that need disambiguation (e.g., ["service"]) for MULTIPLE_MATCHES
    - Additional fields from clarification_data (e.g., "options" for MULTIPLE_MATCHES)

    This function extracts semantic cause of clarification and populates
    data.reason, data.missing, and data.ambiguous. This is the single, authoritative source
    for clarification semantics.

    Args:
        clarification_reason: Clarification reason string from Luma
        issues: Issues dict from Luma (contains missing slots, time issues, etc.)
        clarification_data: Optional structured clarification data from Luma (e.g., options for MULTIPLE_MATCHES)

    Returns:
        Dictionary with 'reason', 'missing', and 'ambiguous' fields (all always present), plus any additional fields from clarification_data
    """
    # Extract reason (use clarification_reason if present and non-empty, otherwise infer from issues)
    reason = clarification_reason if clarification_reason and clarification_reason.strip() else None

    # Extract missing and ambiguous fields from issues dict
    # Issues structure: {slot_name: "missing"} or {slot_name: "ambiguous"} or {slot_name: {...}} for rich issues
    missing = []
    ambiguous = []
    if isinstance(issues, dict):
        for slot_name, slot_value in issues.items():
            # If value is "missing", add to missing list
            if slot_value == "missing":
                missing.append(slot_name)
            # If value is "ambiguous", add to ambiguous list (NOT missing)
            elif slot_value == "ambiguous":
                ambiguous.append(slot_name)
            # For rich time issues, still consider "time" as missing if present
            elif slot_name == "time" and isinstance(slot_value, dict):
                # Time has ambiguity but is still missing resolution
                missing.append("time")

    # For UNSUPPORTED_SERVICE, ensure "service" is in missing if not already there
    # Note: MULTIPLE_MATCHES is now handled in Luma (issues.service = "ambiguous"),
    # so it will be extracted from issues above. Only UNSUPPORTED_SERVICE needs fallback handling here.
    if reason == ClarificationReason.UNSUPPORTED_SERVICE.value:
        if "service" not in missing and "service_id" not in missing:
            missing.append("service")

    # If no reason provided but we have missing slots, infer reason
    if not reason and missing:
        if "time" in missing:
            reason = ClarificationReason.MISSING_TIME.value
        elif "date" in missing:
            reason = ClarificationReason.MISSING_DATE.value
        elif "service" in missing or "service_id" in missing:
            reason = ClarificationReason.MISSING_SERVICE.value
        else:
            reason = ClarificationReason.MISSING_CONTEXT.value  # Generic fallback

    # Build structured data object - always include all fields
    # reason defaults to MISSING_CONTEXT if not provided and cannot be inferred
    if not reason:
        reason = ClarificationReason.MISSING_CONTEXT.value

    # missing and ambiguous default to empty lists if none found
    if not missing:
        missing = []
    if not ambiguous:
        ambiguous = []

    # Start with reason, missing, and ambiguous (always present)
    data = {
        "reason": reason,
        "missing": missing,
        "ambiguous": ambiguous
    }

    # Merge additional fields from clarification_data (e.g., options for MULTIPLE_MATCHES)
    if clarification_data and isinstance(clarification_data, dict):
        # Merge clarification_data into data (e.g., options for MULTIPLE_MATCHES)
        # But preserve reason, missing, and ambiguous as authoritative
        # Note: service_family is not included here - use context.services[0].canonical instead
        for key, value in clarification_data.items():
            if key not in ("reason", "missing", "ambiguous"):  # Don't override reason/missing/ambiguous
                data[key] = value

    return data


def _derive_clarification_reason_from_missing_slots(missing: List[str]) -> str:
    """
    Derive canonical clarification reason from missing slots.
    
    This function provides the single source of truth for mapping missing slots
    to canonical clarification reasons. All clarification outcomes should use
    this function to ensure consistency.
    
    Args:
        missing: List of missing slot names (e.g., ["start_date", "end_date"])
        
    Returns:
        Canonical clarification reason string
    """
    missing_set = set(missing)
    
    if missing_set == {"start_date", "end_date"}:
        return "MISSING_DATE_RANGE"
    elif missing_set == {"start_date"}:
        return "MISSING_START_DATE"
    elif missing_set == {"end_date"}:
        return "MISSING_END_DATE"
    elif missing_set == {"time"}:
        return "MISSING_TIME"
    elif missing_set == {"date"}:
        return "MISSING_DATE"
    elif "time" in missing_set:
        return "MISSING_TIME"
    else:
        return "NEEDS_CLARIFICATION"


def _build_clarify_outcome(
    clarification_reason: str,
    issues: Dict[str, Any],
    context: Dict[str, Any],
    booking: Optional[Dict[str, Any]],
    domain: str,
    clarification_data: Optional[Dict[str, Any]] = None,
    facts: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Build a CLARIFY outcome from Luma response data.

    Args:
        clarification_reason: Clarification reason string from Luma
        issues: Issues dict from Luma
        context: Context dict from Luma
        booking: Optional booking payload from Luma
        domain: Domain for template key routing
        clarification_data: Optional structured clarification data from Luma (e.g., options for MULTIPLE_MATCHES)
        facts: Optional facts container with slots, missing_slots, context (for rendering)

    Returns:
        Complete CLARIFY outcome dictionary ready to return
    """
    template_key = get_template_key(clarification_reason, domain)

    logger.info(
        f"Clarification needed: {clarification_reason} -> {template_key}"
    )

    # Extract structured clarification data (reason, missing, and ambiguous fields)
    # This is the single, authoritative source for clarification semantics
    data = _extract_clarification_data(
        clarification_reason, issues, clarification_data)

    # Derive canonical clarification_reason from missing slots
    # This is the top-level field that tests expect
    missing_slots = data.get("missing", [])
    canonical_reason = _derive_clarification_reason_from_missing_slots(missing_slots)

    # Build outcome with facts for rendering
    outcome = {
        "status": "NEEDS_CLARIFICATION",
        "clarification_reason": canonical_reason,  # Top-level canonical reason derived from missing slots
        "template_key": template_key,
        # data contains reason, missing, ambiguous, and any additional fields from clarification_data (e.g., options)
        "data": data,
        "context": context,  # Include context if present
        "booking": booking
    }
    
    # Include facts if provided (needed for renderer to access slots)
    if facts:
        outcome["facts"] = facts

    return {
        "success": True,
        "outcome": outcome
    }


def process_luma_response(
    luma_response: Dict[str, Any],
    domain: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Process Luma response and produce a decision plan.

    This function interprets the Luma response and returns a decision plan that includes:
    - status (READY, NEEDS_CLARIFICATION, AWAITING_CONFIRMATION)
    - allowed_actions
    - blocked_actions
    - awaiting (USER_CONFIRMATION or null)

    Args:
        luma_response: Validated Luma API response
        domain: Domain for template key routing
        user_id: User identifier for logging

    Returns:
        Decision dictionary with:
        - plan: Decision plan (status, allowed_actions, blocked_actions, awaiting)
        - outcome: Clarification outcome (if status is NEEDS_CLARIFICATION)
        - intent_name: Intent name (if status is READY or AWAITING_CONFIRMATION)
        - action_name: Action name for handler mapping
        - booking: Booking payload
        - facts: Facts container with slots, missing_slots, context (passthrough from Luma)
        - error: Error code (if error occurred)
        - message: Error message (if error occurred)
    """
    # Extract intent and validate
    intent = luma_response.get("intent", {})
    intent_name = intent.get("name", "").strip() if intent.get("name") else ""
    
    if not intent_name:
        logger.error(f"Missing intent name in Luma response for user {user_id}")
        # Extract facts container even for error cases
        facts = {
            "slots": luma_response.get("slots", {}),
            "missing_slots": luma_response.get("missing_slots", []),
            "context": luma_response.get("context", {})
        }
        return {
            "error": "missing_intent",
            "message": "Intent name is missing from Luma response",
            "plan": {
                "status": "NEEDS_CLARIFICATION",
                "allowed_actions": [],
                "blocked_actions": [],
                "awaiting": None
            },
            "facts": facts
        }
    
    # Build decision plan
    plan = _build_decision_plan(intent_name, luma_response, domain)
    
    # Check if Luma indicates clarification is needed
    if luma_response.get("needs_clarification", False):
        reason = luma_response.get("clarification_reason", "")
        issues = luma_response.get("issues", {})
        context = luma_response.get("context", {})
        booking = luma_response.get("booking")
        clarification_data = luma_response.get("clarification_data")
        
        # Extract facts container (passthrough data from Luma)
        facts = {
            "slots": luma_response.get("slots", {}),
            "missing_slots": luma_response.get("missing_slots", []),
            "context": context  # context is already extracted above
        }

        return {
            "outcome": _build_clarify_outcome(
                clarification_reason=reason,
                issues=issues,
                context=context,
                booking=booking,
                domain=domain,
                clarification_data=clarification_data,
                facts=facts
            ),
            "plan": plan,
            "facts": facts
        }

    # Get action name for handler mapping
    action_name = get_action_name(intent_name)

    if not action_name:
        logger.warning(
            f"Unsupported intent for user {user_id}: {intent_name!r} "
            f"(type: {type(intent_name).__name__})"
        )
        # Debug: log available intents for troubleshooting
        from core.routing.intent_router import INTENT_ACTIONS
        logger.debug(f"Available intents: {list(INTENT_ACTIONS.keys())}")
        # Extract facts container even for unsupported intent
        facts = {
            "slots": luma_response.get("slots", {}),
            "missing_slots": luma_response.get("missing_slots", []),
            "context": luma_response.get("context", {})
        }
        return {
            "error": "unsupported_intent",
            "message": f"Intent {intent_name} is not supported",
            "plan": plan,
            "facts": facts
        }

    # Return execution instruction with decision plan
    booking = luma_response.get("booking", {})
    
    # Extract facts container (passthrough data from Luma)
    facts = {
        "slots": luma_response.get("slots", {}),
        "missing_slots": luma_response.get("missing_slots", []),
        "context": luma_response.get("context", {})
    }
    
    return {
        "intent_name": intent_name,
        "action_name": action_name,
        "booking": booking,
        "plan": plan,
        "facts": facts
    }


def build_clarify_outcome_from_reason(
    reason: str,
    issues: Dict[str, Any],
    booking: Optional[Dict[str, Any]],
    domain: str,
    facts: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Build a CLARIFY outcome from a core-initiated clarification reason.

    Used when orchestrator detects clarification needs during service/room/extras resolution.

    Args:
        reason: Clarification reason (e.g., "MISSING_SERVICE")
        issues: Issues dict (e.g., {"service": "missing"})
        booking: Optional booking payload
        domain: Domain for template key routing
        facts: Optional facts container with slots, missing_slots, context (for rendering)

    Returns:
        Complete CLARIFY outcome dictionary ready to return
    """
    template_key = get_template_key(reason, domain)

    # Extract structured clarification data
    clarification_data = _extract_clarification_data(reason, issues)

    # Derive canonical clarification_reason from missing slots
    # This is the top-level field that tests expect
    missing_slots = clarification_data.get("missing", [])
    canonical_reason = _derive_clarification_reason_from_missing_slots(missing_slots)

    outcome = {
        "status": "NEEDS_CLARIFICATION",
        "clarification_reason": canonical_reason,  # Top-level canonical reason derived from missing slots
        "template_key": template_key,
        "data": clarification_data,
        "booking": booking
    }
    
    # Include facts if provided (needed for renderer to access slots)
    if facts:
        outcome["facts"] = facts

    return {
        "success": True,
        "outcome": outcome
    }
