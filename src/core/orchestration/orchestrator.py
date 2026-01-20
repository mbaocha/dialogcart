"""
Orchestration Layer - Planning Only

Control flow and decision making for conversation handling.

This module orchestrates the conversation flow by:
- Handling message entry
- Deriving org_id and domain
- Constructing catalog and tenant_context
- Calling Luma API
- Validating contracts
- Planning outcomes based on plan status (NEEDS_CLARIFICATION, AWAITING_CONFIRMATION, READY)

Core NEVER executes actions - it only plans and returns:
{
  intent,
  slots,
  missing_slots,
  executable_actions,
  dialog_instruction?
}

Execution layer consumes executable_actions only.

Constraints:
- No copy, no templates, no WhatsApp formatting
- Must only return structured planning outcomes
- NO execution logic - execution is handled by separate layer
"""

import logging
import json
import os
import copy
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from core.orchestration.nlu import LumaClient, assert_luma_contract, process_luma_response, build_clarify_outcome_from_reason
from core.orchestration.errors import ContractViolation, UpstreamError, UnsupportedIntentError
from core.orchestration.clients.catalog_client import CatalogClient
from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.cache.catalog_cache import catalog_cache
from core.orchestration.cache.org_domain_cache import org_domain_cache

logger = logging.getLogger(__name__)


def _build_planning_outcome(
    intent_name: str,
    slots: Dict[str, Any],
    missing_slots: List[str],
    executable_actions: List[str],
    dialog_instruction: Optional[Dict[str, Any]] = None,
    status: str = "READY"
) -> Dict[str, Any]:
    """
    Build planning-only outcome structure.
    
    Core NEVER executes - only returns planning information.
    
    Args:
        intent_name: Intent name
        slots: Collected slots
        missing_slots: Missing required slots
        executable_actions: Actions that can be executed with current slots
        dialog_instruction: Optional dialog instruction (if status is NEEDS_CLARIFICATION)
        status: Planning status (READY, NEEDS_CLARIFICATION, AWAITING_CONFIRMATION)
    
    Returns:
        Planning outcome dictionary matching core contract
    """
    outcome = {
        "intent": intent_name,
        "slots": slots,
        "missing_slots": missing_slots,
        "executable_actions": executable_actions
    }
    
    if dialog_instruction:
        outcome["dialog_instruction"] = dialog_instruction
    
    return outcome


def _handle_non_core_intent(
    luma_response: Dict[str, Any],
    decision: Dict[str, Any],
    user_id: str
) -> Dict[str, Any]:
    """
    Handle non-core intents by passing them through as non-orchestrated signals.

    Non-core intents (e.g., PAYMENT, CONFIRM_BOOKING, BOOKING_INQUIRY) are not
    orchestrated by core but are passed through to preserve conversational continuity.
    This enables workflow extensions to handle these intents in future steps.

    This function wraps the Luma response and produces a valid outcome without
    plan generation, execution, or confirmation gating.

    Args:
        luma_response: Original Luma API response
        decision: Decision plan from process_luma_response
        user_id: User identifier for logging

    Returns:
        Outcome dictionary with:
        - success: True (not an error, just non-orchestrated)
        - outcome.status: "NON_CORE_INTENT"
        - outcome.intent_name: The non-core intent name
        - outcome.facts: Facts container with slots, missing_slots, and context
    """
    intent_name = decision.get("intent_name", "")
    facts = decision.get("facts", {})

    # Ensure facts structure includes slots, missing_slots, and context
    # Facts from decision should already have this structure, but ensure completeness
    if not facts:
        facts = {}

    # Preserve slots from Luma response (decision.facts may already have this)
    slots = luma_response.get("slots", {})
    if slots:
        facts.setdefault("slots", slots)

    # Preserve missing_slots from Luma response (computed by merge from intent contract)
    # ARCHITECTURAL INVARIANT: missing_slots is computed exactly once per turn in session merge
    # missing_slots = [] is VALID and means all required slots are satisfied
    missing_slots = luma_response.get("missing_slots")
    if missing_slots is not None and isinstance(missing_slots, list):
        # Use merged missing_slots (even if []) - this is authoritative
        facts.setdefault("missing_slots", missing_slots)
    else:
        # This should never happen if merge ran correctly
        logger.error(
            f"[MISSING_SLOTS] VIOLATION: missing_slots is None or not a list in non-core intent! "
            f"user_id={user_id}, missing_slots={missing_slots}, luma_response_keys={list(luma_response.keys())}"
        )
        # Fail-safe: use empty list (but this indicates a bug)
        facts["missing_slots"] = []

    # Preserve context from Luma response
    context = luma_response.get("context")
    if context:
        facts.setdefault("context", context)
    elif "context" not in facts:
        facts["context"] = {}

    logger.info(
        f"Passing through non-core intent '{intent_name}' for user {user_id} "
        f"(not orchestrated by core)"
    )

    return {
        "success": True,
        "outcome": {
            "status": "NON_CORE_INTENT",
            "intent_name": intent_name,
            "facts": facts,
        }
    }


def _get_org_id_from_env() -> int:
    """Return organization_id from ORG_ID env var with safe default."""
    value = os.getenv("ORG_ID", "1")
    try:
        org_id = int(value)
        if org_id <= 0:
            raise ValueError("ORG_ID must be positive")
        return org_id
    except Exception:  # noqa: BLE001
        logger.warning("Invalid ORG_ID env value '%s', defaulting to 1", value)
        return 1


def handle_message(
    user_id: str,
    text: str,
    domain: str = "service",  # caller-provided; will be overridden by org domain
    timezone: str = "UTC",
    phone_number: Optional[str] = None,
    email: Optional[str] = None,
    customer_id: Optional[int] = None,
    organization_id: Optional[int] = None,
    luma_client: Optional[LumaClient] = None,
    catalog_client: Optional[CatalogClient] = None,
    organization_client: Optional[OrganizationClient] = None,
    verbose: bool = False,
    session_state: Optional[Dict[str, Any]] = None,
    transaction_id: Optional[str] = None,
    planning_only: bool = False
) -> Dict[str, Any]:
    """
    Handle a user message - stateless orchestration.

    Flow:
    1. Call Luma (LumaClient.resolve)
    2. Assert response contract (assert_luma_contract - only requires intent.name)
    3. Process Luma response and build planning outcome (facts may be empty/partial)
    4. Return planning-only outcome: {intent, slots, missing_slots, executable_actions, dialog_instruction?}
    
    FACT-ONLY CONTRACT: Only requires intent.name to exist.
    - Missing facts/slots are NOT errors - planner computes missing_slots
    - Planning proceeds as long as intent.name exists
    
    Core NEVER executes actions - execution is handled by separate layer.

    Args:
        user_id: User identifier (used for session lookup and logging, persistent across turns)
        text: User message text
        domain: Domain (default: "service")
        timezone: Timezone (default: "UTC")
        organization_id: Organization ID (optional, defaults to ORG_ID env or 1) 
        luma_client: Luma client instance (creates default if None)
        catalog_client: Catalog discovery client (creates default if None)
        organization_client: Organization client instance (creates default if None)
        session_state: Optional session state for follow-up handling
        transaction_id: Optional transaction ID for per-request tracing (never stored in session)
        planning_only: If True, return planning outcome only (no execution, no dialog, no session mutation beyond slot accumulation)

    Returns:
        Response dictionary with success and outcome
    """
    # Initialize default clients if not provided
    if luma_client is None:
        luma_client = LumaClient()
    if catalog_client is None:
        catalog_client = CatalogClient()
    if organization_client is None:
        organization_client = OrganizationClient()
    
    # Initialize execution clients only if not in planning-only mode
    # These are not needed when planning_only=True since we return early before execution
    booking_client = None
    customer_client = None
    if not planning_only:
        # Only import and initialize execution clients when not in planning-only mode
        # This avoids unnecessary imports and initialization for planning tests
        try:
            from core.execution.clients.booking_client import BookingClient
            from core.orchestration.clients.customer_client import CustomerClient
            booking_client = BookingClient()
            customer_client = CustomerClient()
        except (ImportError, AttributeError):
            # Execution clients may not be available in planning-only builds
            # This is fine - they won't be used in planning-only mode anyway
            pass

    # TODO: Replace env-based organization_id with channel/auth-derived organization_id
    resolved_org_id = organization_id if organization_id is not None else _get_org_id_from_env()

    # Derive domain from organization details (cached, long TTL)
    derived_domain, _ = org_domain_cache.get_domain(
        resolved_org_id, organization_client, force_refresh=False
    )

    # Step 1: Prepare tenant_context aliases (service/reservation)
    catalog_data_for_alias: Optional[Dict[str, Any]] = None
    tenant_context = None
    if derived_domain in ("service", "reservation"):
        catalog_data_for_alias = catalog_cache.get_catalog(
            resolved_org_id, catalog_client, domain=derived_domain)
        alias_map: Dict[str, Any] = {}
        if derived_domain == "service":
            services_for_alias = catalog_data_for_alias.get(
                "services", []) if isinstance(catalog_data_for_alias, dict) else []
            for svc in services_for_alias:
                if not isinstance(svc, dict) or svc.get("is_active") is False:
                    continue
                name = svc.get("name")
                if not name:
                    continue
                canonical_key = svc.get("service_family_id") or svc.get(
                    "canonical") or svc.get("slug") or name.lower().replace(" ", "_")
                if not canonical_key:
                    continue
                # Construct full canonical path if it's a short form (no dot)
                # Luma expects format: "category.family_id" (e.g., "beauty_and_wellness.haircut")
                if "." not in str(canonical_key):
                    # Short form canonical - prefix with category for service domain
                    canonical_key = f"beauty_and_wellness.{canonical_key}"
                alias_map[name.lower()] = canonical_key
        else:
            rooms_for_alias = catalog_data_for_alias.get(
                "rooms", []) if isinstance(catalog_data_for_alias, dict) else []
            for rt in rooms_for_alias:
                if not isinstance(rt, dict) or rt.get("is_active") is False:
                    continue
                name = rt.get("name")
                if not name:
                    continue
                canonical_key = rt.get("canonical_key") or rt.get("canonical") or rt.get(
                    "slug") or name.lower().replace(" ", "_")
                if not canonical_key:
                    continue
                alias_map[name.lower()] = canonical_key
            extras_for_alias = catalog_data_for_alias.get(
                "extras", []) if isinstance(catalog_data_for_alias, dict) else []
            for ex in extras_for_alias:
                if not isinstance(ex, dict) or ex.get("is_active") is False:
                    continue
                name = ex.get("name")
                if not name:
                    continue
                canonical_key = ex.get("canonical") or ex.get(
                    "slug") or name.lower().replace(" ", "_")
                if not canonical_key:
                    continue
                alias_map[name.lower()] = canonical_key

        # Always create tenant_context with booking_mode, even if no aliases
        tenant_context = {}
        if alias_map:
            tenant_context["aliases"] = alias_map
        # Always include booking_mode in tenant_context so Luma can determine intent correctly
        # booking_mode should match domain: "service" for appointments, "reservation" for reservations
        tenant_context["booking_mode"] = derived_domain

    # Step 2: Call Luma
    # Build and log Luma payload
    luma_payload = {
        "user_id": user_id,
        "text": text,
        "domain": derived_domain,
        "timezone": timezone,
    }
    if tenant_context:
        luma_payload["tenant_context"] = tenant_context
    else:
        logger.warning(
            f"[ORCHESTRATOR] No tenant_context to send to Luma (domain={derived_domain})"
        )

    # Log sentence passed to Luma
    logger.info("Luma request payload: %s", json.dumps(
        luma_payload, ensure_ascii=False))

    # Store raw response for attachment to effective_response (must be accessible after try block)
    raw_luma_response_deep_copy = None
    
    try:
        luma_response = luma_client.resolve(
            user_id=user_id,
            text=text,
            domain=derived_domain,
            timezone=timezone,
            tenant_context=tenant_context
        )

        # Store raw response for attachment to effective_response (must be accessible after try block)
        raw_luma_response_deep_copy = copy.deepcopy(luma_response)

        # ARCHITECTURAL INVARIANT: Create authoritative slot view BEFORE any processing
        # effective_turn_slots = merge(session_state.slots, raw_luma_response.slots)
        # Required-slot computation MUST ONLY use effective_turn_slots
        raw_luma_slots = luma_response.get("slots", {})
        if not isinstance(raw_luma_slots, dict):
            raw_luma_slots = {}
        
        session_slots_for_merge = {}
        if session_state and session_state.get("status") == "NEEDS_CLARIFICATION":
            session_slots_for_merge = session_state.get("slots", {})
            if not isinstance(session_slots_for_merge, dict):
                session_slots_for_merge = {}
        
        # Create authoritative slot view: merge session slots with raw Luma slots
        # This ensures required-slot computation always sees current-turn Luma output
        effective_turn_slots = {**session_slots_for_merge, **raw_luma_slots}
        
        # GUARD ASSERTION (test/debug only): If raw_luma_response.slots is non-empty but effective_turn_slots doesn't contain those slots → ERROR
        # This ensures raw Luma slots are never lost in the merge
        import os
        if (os.getenv("PYTEST_CURRENT_TEST") or os.getenv("DEBUG_SLOT_MERGE") == "1"):
            if raw_luma_slots:
                missing_slots = set(raw_luma_slots.keys()) - set(effective_turn_slots.keys())
                if missing_slots:
                    error_msg = (
                        f"INVARIANT VIOLATION: raw_luma_response.slots contains slots that are missing from effective_turn_slots! "
                        f"user_id={user_id}, missing_slots={list(missing_slots)}, "
                        f"raw_luma_slots={list(raw_luma_slots.keys())}, "
                        f"session_slots={list(session_slots_for_merge.keys())}, "
                        f"effective_turn_slots={list(effective_turn_slots.keys())}"
                    )
                    logger.error(f"[EFFECTIVE_TURN_SLOTS] {error_msg}")
                    # In test mode, raise assertion
                    if os.getenv("PYTEST_CURRENT_TEST"):
                        raise AssertionError(error_msg)
        
        logger.debug(
            f"[EFFECTIVE_TURN_SLOTS] Created authoritative slot view: "
            f"session_slots={list(session_slots_for_merge.keys())}, "
            f"raw_luma_slots={list(raw_luma_slots.keys())}, "
            f"effective_turn_slots={list(effective_turn_slots.keys())}"
        )

        
        # DEBUG: Print raw Luma response for weekday follow-ups (guarded by env var)
        import pprint
        if os.getenv("DEBUG_LUMA_WEEKDAY") == "1":
            # Only dump for suspected weekday messages
            text_l = (text or "").lower()
            weekday_keywords = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", 
                              "next monday", "next tuesday", "next wednesday", "next thursday", "next friday", 
                              "next saturday", "next sunday"]
            if any(w in text_l for w in weekday_keywords):
                print("\n=== DEBUG_LUMA_WEEKDAY RAW LUMA RESPONSE ===")
                print(f"Input text: {text}")
                print(f"User ID: {user_id}")
                print(f"Session state exists: {session_state is not None}")
                if session_state:
                    print(f"Session status: {session_state.get('status')}")
                    print(f"Session intent: {session_state.get('intent')}")
                try:
                    # Print full response without truncation
                    response_str = json.dumps(luma_response, indent=2, default=str, ensure_ascii=False)
                    print(response_str)
                except Exception as e:
                    print(f"JSON serialization failed: {e}")
                    pprint.pprint(luma_response)
                print("=== END DEBUG_LUMA_WEEKDAY ===\n")
        

    except UpstreamError as e:
        logger.error(f"Luma API error for user {user_id}: {str(e)}")
        # FIX: Safe fallback when Luma call fails - preserve session and return planning response
        if session_state and session_state.get("status") == "NEEDS_CLARIFICATION":
            # Return planning response derived from session state
            session_intent = session_state.get("intent")
            session_intent_str = session_intent if isinstance(session_intent, str) else (session_intent.get("name", "") if isinstance(session_intent, dict) else "")
            session_slots = session_state.get("slots", {})
            if not isinstance(session_slots, dict):
                session_slots = {}
            
            # Compute missing_slots from session intent and slots
            from core.orchestration.api.slot_contract import compute_missing_slots
            missing_slots = compute_missing_slots(session_intent_str, session_slots, domain=derived_domain) if session_intent_str else []
            
            # Extract stage/action from session state if available, otherwise use defaults
            session_stage = session_state.get("stage", "AVAILABILITY")
            session_action = session_state.get("action")
            
            return {
                "success": True,
                "outcome": {
                    "intent": session_intent_str,
                    "stage": session_stage,
                    "action": session_action,
                    "slots": session_slots,
                    "missing_slots": missing_slots,
                    "status": "NEEDS_CLARIFICATION" if missing_slots else "READY"
                }
            }
        return {
            "success": False,
            "error": "upstream_error",
            "message": str(e)
        }

    # FIX: Guard against None or empty Luma response
    if not luma_response or not isinstance(luma_response, dict):
        logger.error(f"Luma returned None or invalid response for user {user_id}")
        # Safe fallback when Luma response is missing - preserve session and return planning response
        if session_state and session_state.get("status") == "NEEDS_CLARIFICATION":
            # Return planning response derived from session state
            session_intent = session_state.get("intent")
            session_intent_str = session_intent if isinstance(session_intent, str) else (session_intent.get("name", "") if isinstance(session_intent, dict) else "")
            session_slots = session_state.get("slots", {})
            if not isinstance(session_slots, dict):
                session_slots = {}
            
            # Compute missing_slots from session intent and slots
            from core.orchestration.api.slot_contract import compute_missing_slots
            missing_slots = compute_missing_slots(session_intent_str, session_slots, domain=derived_domain) if session_intent_str else []
            
            # Extract stage/action from session state if available, otherwise use defaults
            session_stage = session_state.get("stage", "AVAILABILITY")
            session_action = session_state.get("action")
            
            return {
                "success": True,
                "outcome": {
                    "intent": session_intent_str,
                    "stage": session_stage,
                    "action": session_action,
                    "slots": session_slots,
                    "missing_slots": missing_slots,
                    "status": "NEEDS_CLARIFICATION" if missing_slots else "READY"
                }
            }
        return {
            "success": False,
            "error": "upstream_error",
            "message": "Luma returned empty response"
        }

    # Step 2: Assert contract (fact-only: only requires intent.name)
    # Contract validation ensures intent.name exists for planning
    # Missing facts/slots are NOT errors - planner computes missing_slots
    try:
        assert_luma_contract(luma_response)
    except ContractViolation as e:
        logger.error(f"Contract violation for user {user_id}: {str(e)}")
        # FIX: Safe fallback on contract violation - preserve session and return planning response
        if session_state and session_state.get("status") == "NEEDS_CLARIFICATION":
            # Return planning response derived from session state
            session_intent = session_state.get("intent")
            session_intent_str = session_intent if isinstance(session_intent, str) else (session_intent.get("name", "") if isinstance(session_intent, dict) else "")
            session_slots = session_state.get("slots", {})
            if not isinstance(session_slots, dict):
                session_slots = {}
            
            # Compute missing_slots from session intent and slots
            from core.orchestration.api.slot_contract import compute_missing_slots
            missing_slots = compute_missing_slots(session_intent_str, session_slots, domain=derived_domain) if session_intent_str else []
            
            # Extract stage/action from session state if available, otherwise use defaults
            session_stage = session_state.get("stage", "AVAILABILITY")
            session_action = session_state.get("action")
            
            return {
                "success": True,
                "outcome": {
                    "intent": session_intent_str,
                    "stage": session_stage,
                    "action": session_action,
                    "slots": session_slots,
                    "missing_slots": missing_slots,
                    "status": "NEEDS_CLARIFICATION" if missing_slots else "READY"
                }
            }
        return {
            "success": False,
            "error": "contract_violation",
            "message": str(e)
        }

    # FACT-ONLY CONTRACT: No success flag check
    # Planning proceeds as long as intent.name exists (validated by assert_luma_contract)
    # Missing slots are NOT errors - planner will compute missing_slots from intent_planning.yaml

    # Step 3.5: Determine effective intent and construct effective_response
    # Intent override MUST happen BEFORE process_luma_response, planner, and allowed action checks
    log_transaction_id = f" transaction_id={transaction_id}" if transaction_id else ""
    
    # Extract Luma intent
    luma_intent_obj = luma_response.get("intent", {})
    luma_intent_name = luma_intent_obj.get("name", "") if isinstance(luma_intent_obj, dict) else ""
    
    # Resolve effective_intent using session
    # CRITICAL INTENT MERGE RULE:
    # IF luma_intent != "UNKNOWN": use luma_intent
    # ELSE: KEEP session.intent (NEVER allow UNKNOWN to overwrite session intent)
    effective_intent = luma_intent_name
    session_reset_occurred = False
    
    if session_state and session_state.get("status") == "NEEDS_CLARIFICATION":
        session_intent = session_state.get("intent")
        session_intent_str = session_intent if isinstance(session_intent, str) else (session_intent.get("name", "") if isinstance(session_intent, dict) else "")
        
        # Check for domain switch based on canonical service evidence (even for UNKNOWN intents)
        canonical_indicates_switch = False
        context = luma_response.get("context", {})
        services = context.get("services", []) if isinstance(context, dict) else []
        
        if services and isinstance(services, list) and len(services) > 0:
            first_service = services[0]
            if isinstance(first_service, dict):
                canonical = first_service.get("canonical") or first_service.get("canonical_key")
                if canonical:
                    canonical_str = str(canonical).lower()
                    # Check if canonical indicates reservation domain (hospitality.*)
                    if canonical_str.startswith("hospitality.") or canonical_str.startswith("lodging."):
                        # Canonical indicates reservation domain
                        if session_intent_str == "CREATE_APPOINTMENT":
                            canonical_indicates_switch = True
                            logger.info(
                                f"[session] domain_switch_detected user_id={user_id}{log_transaction_id} "
                                f"canonical={canonical} indicates reservation domain, session was service"
                            )
                    elif canonical_str.startswith("beauty_and_wellness.") or canonical_str.startswith("service."):
                        # Canonical indicates service domain
                        if session_intent_str == "CREATE_RESERVATION":
                            canonical_indicates_switch = True
                            logger.info(
                                f"[session] domain_switch_detected user_id={user_id}{log_transaction_id} "
                                f"canonical={canonical} indicates service domain, session was reservation"
                            )
        
        if canonical_indicates_switch:
            # Domain switch detected - reset session and use new intent based on canonical
            # Determine new intent from canonical evidence
            new_intent = None
            if services and isinstance(services, list) and len(services) > 0:
                first_service = services[0]
                if isinstance(first_service, dict):
                    canonical = first_service.get("canonical") or first_service.get("canonical_key")
                    if canonical:
                        canonical_str = str(canonical).lower()
                        if canonical_str.startswith("hospitality.") or canonical_str.startswith("lodging."):
                            new_intent = "CREATE_RESERVATION"
                        elif canonical_str.startswith("beauty_and_wellness.") or canonical_str.startswith("service."):
                            new_intent = "CREATE_APPOINTMENT"
            
            if new_intent:
                effective_intent = new_intent
            else:
                # Fallback: use session intent if canonical parsing fails
                effective_intent = session_intent_str
                canonical_indicates_switch = False
            
            if canonical_indicates_switch:
                from core.orchestration.session.session_manager import clear_session
                clear_session(user_id)
                session_state = None
                session_reset_occurred = True
                logger.info(
                    f"[session] domain_switch_reset user_id={user_id}{log_transaction_id} "
                    f"old={session_intent_str} new={effective_intent} (canonical-based switch)"
                )
        elif luma_intent_name == "UNKNOWN":
            # Rule: If luma.intent == UNKNOWN, KEEP session.intent (NEVER allow UNKNOWN to overwrite)
            effective_intent = session_intent_str
            logger.info(
                f"[session] intent_override user_id={user_id}{log_transaction_id} "
                f"UNKNOWN -> session.intent={effective_intent}"
            )
        else:
            # Check if new intent is non-core (DISCOVERY, CONFIRM_BOOKING, etc.)
            from core.routing.intents.base_intents import is_core_intent
            is_new_intent_core = is_core_intent(luma_intent_name)
            is_session_intent_core = is_core_intent(session_intent_str) if session_intent_str else False
            
            # Rule: Non-core intents (DISCOVERY, CONFIRM_BOOKING) should NOT overwrite active booking session
            if is_session_intent_core and not is_new_intent_core:
                # Keep session intent - non-core intents are side-intents that don't interrupt booking flow
                effective_intent = session_intent_str
                logger.info(
                    f"[session] non_core_intent_ignored user_id={user_id}{log_transaction_id} "
                    f"session.intent={session_intent_str} luma.intent={luma_intent_name} (non-core, preserving session)"
                )
            elif is_new_intent_core and luma_intent_name != session_intent_str:
                # Core booking intent changed - clear old session
                effective_intent = luma_intent_name
                from core.orchestration.session.session_manager import clear_session
                clear_session(user_id)
                session_state = None
                session_reset_occurred = True
                logger.info(
                    f"[session] intent_changed user_id={user_id}{log_transaction_id} "
                    f"old={session_intent_str} new={luma_intent_name}"
                )
            else:
                # Same core intent or no switch - keep session
                effective_intent = session_intent_str
    
    # Hard assertion: effective_intent must NOT be UNKNOWN when session exists (and not reset)
    if session_state and session_state.get("status") == "NEEDS_CLARIFICATION" and not session_reset_occurred:
        assert effective_intent != "UNKNOWN", (
            f"Assertion failed: effective_intent is UNKNOWN but session.intent exists. "
            f"session.intent={session_state.get('intent')}, luma.intent={luma_intent_name}"
        )
    
    # Construct effective_response: Copy luma_response and replace intent.name with effective_intent
    # FACT-ONLY CONTRACT: facts may be empty or partial - this is valid
    # Missing slots are NOT errors - planner will compute missing_slots from intent_planning.yaml
    effective_response = luma_response.copy()
    effective_response["intent"] = {"name": effective_intent}
    
    # FACT-ONLY: Promote facts to slots BEFORE any other processing
    # This ensures facts.service_id, facts.times, etc. are available for planning
    from core.orchestration.luma_facts_adapter import facts_to_slots
    facts_obj = luma_response.get("facts", {})
    # Pass effective_intent for date_range promotion (CREATE_RESERVATION with 2+ dates)
    promoted_slots = facts_to_slots(
        facts_obj,
        intent_name=effective_intent,
        source_text=text,
    ) if isinstance(facts_obj, dict) else {}
    
    # Extract slots from facts.facts.slots if present (nested format)
    # Otherwise fall back to legacy slots field
    nested_slots = {}
    if isinstance(facts_obj, dict) and "slots" in facts_obj:
        # Nested format: facts.facts.slots
        nested_slots = facts_obj.get("slots", {})
    elif "slots" in luma_response:
        # Legacy format: slots at top level
        nested_slots = luma_response.get("slots", {})
    
    # Merge promoted slots (from facts.service_id, facts.times, etc.) with nested slots
    # Promoted slots take precedence (they are the source of truth)
    effective_response["slots"] = {**nested_slots, **promoted_slots}

    # Preserve the raw user text for follow-up slot promotion
    effective_response["_source_text"] = text
    
    # Normalize service_id using tenant aliases (e.g., "suite" -> "room", "deluxe" -> "room")
    # CRITICAL: Preserve raw tenant value while storing canonical for planning
    if tenant_context and "aliases" in tenant_context and "service_id" in effective_response["slots"]:
        aliases = tenant_context["aliases"]
        raw_service_id = effective_response["slots"]["service_id"]
        if isinstance(raw_service_id, str) and raw_service_id.lower() in aliases:
            canonical_service_id = aliases[raw_service_id.lower()]
            logger.info(f"Normalized service_id: {raw_service_id} -> {canonical_service_id} (via tenant aliases)")
            # Store canonical for planning, preserve raw for outcome
            effective_response["slots"]["_canonical_service_id"] = canonical_service_id
            # Keep raw value in service_id slot (for outcome/dialog)
            effective_response["slots"]["service_id"] = raw_service_id
    
    # Ensure slots is always a dict
    if not isinstance(effective_response.get("slots"), dict):
        effective_response["slots"] = {}
    
    # Attach raw Luma response for debugging (must include: intent, slots, context, entities, status, clarification, original text)
    # This must be preserved through merge_luma_with_session and included in test snapshots
    # DO NOT mutate or normalize _raw_luma_response - it is for debugging only
    if raw_luma_response_deep_copy is not None:
        effective_response["_raw_luma_response"] = raw_luma_response_deep_copy
    
    logger.info(
        f"effective_intent_resolved user_id={user_id}{log_transaction_id} "
        f"luma_intent={luma_intent_name} effective_intent={effective_intent}"
    )
    
    # Step 4: Process Luma response (interpret and decide CLARIFY vs EXECUTE)
    # Use ONLY effective_response (never the raw luma_response)
    # ARCHITECTURAL FIX: Always compute effective_collected_slots, even when there's no session
    # This ensures slots are persisted correctly on the first turn
    from core.orchestration.api.session_merge import merge_luma_with_session, _compute_effective_collected_slots
    
    # Initialize prior_slots for logging (used later)
    prior_slots = []
    prior_intent = None
    prior_missing = []
    
    # If session exists and not reset, merge slots from session
    if session_state and session_state.get("status") == "NEEDS_CLARIFICATION" and not session_reset_occurred:
        prior_intent = session_state.get("intent")
        prior_missing = session_state.get("missing_slots", [])
        prior_slots = list(session_state.get("slots", {}).keys())
        
        effective_response = merge_luma_with_session(effective_response, session_state, planning_only=planning_only)
        
        # AFTER_MERGE: Log right after session merge
        effective_collected_slots = effective_response.get("_effective_collected_slots", {})
        after_merge_log = {
            "trace": "AFTER_MERGE",
            "intent": effective_response.get("intent"),
            "slots": effective_response.get("slots"),
            "effective_collected_slots": effective_collected_slots,
            "modification_context": effective_response.get("_modification_context")
        }
    else:
        # No session (first turn) - still need to compute effective_collected_slots
        # This ensures slots are persisted correctly on the first turn
        if effective_response and isinstance(effective_response, dict):
            effective_response = _compute_effective_collected_slots(effective_response, planning_only=planning_only)
            
            # AFTER_MERGE: Log right after computing effective_collected_slots (first turn)
            effective_collected_slots = effective_response.get("_effective_collected_slots", {})
            after_merge_log = {
                "trace": "AFTER_MERGE",
                "intent": effective_response.get("intent"),
                "slots": effective_response.get("slots"),
                "effective_collected_slots": effective_collected_slots,
                "modification_context": effective_response.get("_modification_context")
            }
            # INVARIANT CHECK: missing_slots must be computed for first turns
            if "missing_slots" not in effective_response:
                # This should never happen if _compute_effective_collected_slots worked correctly
                logger.error(
                    f"[MISSING_SLOTS] VIOLATION: missing_slots not computed in _compute_effective_collected_slots! "
                    f"user_id={user_id}, effective_response_keys={list(effective_response.keys())}"
                )
                # Fail-safe: compute missing_slots now
                from core.orchestration.api.slot_contract import compute_missing_slots
                intent_name = effective_response.get("intent", {}).get("name", "")
                slots = effective_response.get("slots", {})
                if intent_name:
                    effective_response["missing_slots"] = compute_missing_slots(intent_name, slots)
                else:
                    effective_response["missing_slots"] = []
        else:
            # If effective_response is None or invalid, create a minimal dict with empty effective_collected_slots
            if not effective_response:
                effective_response = {}
            effective_response["_effective_collected_slots"] = {}
            # INVARIANT: missing_slots must always be a list (never None)
            effective_response["missing_slots"] = []
        
        # AWAITING_SLOT ROUTING: Route compatible temporal values into awaited slot
        # Slots are merged additively - no special routing needed
        # Users can provide missing slots in any order
    
    # Log merge results for debugging (moved outside if/else to access prior_slots)
    merged_slots = effective_response.get("slots", {})
    merged_missing = effective_response.get("missing_slots", [])
    extracted_slots = [k for k in merged_slots.keys() if k not in prior_slots]
    remaining_missing = merged_missing
    effective_intent_name = effective_response.get("intent", {}).get("name", "")
    
    logger.info(
        f"session_merged user_id={user_id}{log_transaction_id} "
        f"prior_intent={prior_intent} luma_intent={luma_intent_name} effective_intent={effective_intent_name} "
        f"prior_missing_slots={prior_missing} extracted_slots={extracted_slots} remaining_missing_slots={remaining_missing}"
    )
    
    # Verify intent before processing
    # Guard: effective_response must be a dict
    if not effective_response or not isinstance(effective_response, dict):
        logger.error(f"effective_response is None or not a dict: {effective_response}")
        return {
            "success": False,
            "error": "internal_error",
            "message": "Invalid effective_response"
        }
    
    final_intent_check = effective_response.get("intent", {}).get("name", "")
    if final_intent_check == "UNKNOWN" and session_state and session_state.get("status") == "NEEDS_CLARIFICATION":
        logger.error(
            f"INTENT_OVERRIDE_FAILED user_id={user_id}{log_transaction_id} "
            f"effective_response.intent={final_intent_check} session.intent={session_state.get('intent')}"
        )
        # Force override as last resort
        effective_response["intent"] = {"name": session_state.get("intent")}
        final_intent_check = effective_response.get("intent", {}).get("name", "")
    
    logger.info(
        f"calling_process_luma_response user_id={user_id}{log_transaction_id} "
        f"intent={final_intent_check}"
    )
    
    # INVARIANT CHECK: missing_slots MUST be computed before planning
    # missing_slots must be a list (never None, never missing)
    # This is computed in merge_luma_with_session or _compute_effective_collected_slots
    missing_slots_before_plan = effective_response.get("missing_slots")
    assert missing_slots_before_plan is not None, (
        f"missing_slots must be computed before planning! "
        f"user_id={user_id}, effective_response_keys={list(effective_response.keys())}"
    )
    assert isinstance(missing_slots_before_plan, list), (
        f"missing_slots must be a list before planning, got {type(missing_slots_before_plan)}: {missing_slots_before_plan}"
    )
    
    # LUMA_RAW: Log raw Luma response (facts, intent, source_text)
    raw_luma_response = effective_response.get("_raw_luma_response", {})
    raw_luma_facts = raw_luma_response.get("facts", {}) if isinstance(raw_luma_response, dict) else {}
    raw_luma_intent = raw_luma_response.get("intent", {}) if isinstance(raw_luma_response, dict) else {}
    raw_luma_intent_name = raw_luma_intent.get("name", "") if isinstance(raw_luma_intent, dict) else ""
    source_text = effective_response.get("_source_text", text)
    # LUMA_RAW: Log raw Luma response (facts, intent, source_text)
    raw_luma_response = effective_response.get("_raw_luma_response", {})
    raw_luma_facts = raw_luma_response.get("facts", {}) if isinstance(raw_luma_response, dict) else {}
    raw_luma_intent = raw_luma_response.get("intent", {}) if isinstance(raw_luma_response, dict) else {}
    raw_luma_intent_name = raw_luma_intent.get("name", "") if isinstance(raw_luma_intent, dict) else ""
    source_text = effective_response.get("_source_text", text)
    logger.info("[LUMA_RAW] user_id=%s facts=%s intent=%s source_text=%s",
                user_id, json.dumps(raw_luma_facts, default=str, ensure_ascii=True),
                raw_luma_intent_name, source_text)
    
    decision = process_luma_response(effective_response, derived_domain, user_id)
    
    # Guard: decision must be a dict
    # CRITICAL: Missing slots are NEVER an error - always return a planning response
    if not decision or not isinstance(decision, dict):
        logger.error(f"process_luma_response returned None or not a dict: {decision}")
        # Even on error, return a minimal planning response (not an error)
        # This ensures planning always proceeds, even if process_luma_response fails
        intent_name = effective_response.get("intent", {}).get("name", "")
        slots = effective_response.get("slots", {})
        missing_slots = effective_response.get("missing_slots", [])
        
        # Build minimal plan with correct action selection
        # CRITICAL: Apply action selection rules even in fallback case
        if missing_slots:
            stage = "AVAILABILITY"
            action = "SEARCH_AVAILABILITY"
        else:
            stage = "CONFIRM"
            # Apply action selection rules for complete slots
            if intent_name == "CREATE_APPOINTMENT":
                action = "CONFIRM_APPOINTMENT"
            elif intent_name == "CREATE_RESERVATION":
                action = "CONFIRM_RESERVATION"
            else:
                action = None
        
        minimal_plan = {
            "status": "NEEDS_CLARIFICATION" if missing_slots else "READY",
            "allowed_actions": [],
            "blocked_actions": [],
            "awaiting": None,
            "executable_actions": [],
            "stage": stage,
            "action": action
        }
        
        # Build minimal decision
        decision = {
            "intent_name": intent_name,
            "plan": minimal_plan,
            "facts": {
                "slots": slots,
                "missing_slots": missing_slots,
                "context": effective_response.get("context", {})
            }
        }
        logger.warning(f"Created minimal decision due to process_luma_response failure: intent={intent_name}, missing_slots={missing_slots}")


    # Extract decision plan
    plan = decision.get("plan", {})
    plan_status = plan.get("status", "READY")
    allowed_actions = plan.get("allowed_actions", [])
    blocked_actions = plan.get("blocked_actions", [])
    awaiting = plan.get("awaiting")
    
    # PLANNING INVARIANT: Set has_datetime when plan.status == READY
    # has_datetime = true when:
    # - plan status == READY
    # - AND one of:
    #   - date + time exists in slots
    #   - date_range + time exists in slots
    #   - datetime_range exists
    # Rules:
    # - has_datetime must NEVER be set when status != READY
    # - has_datetime is derived, not user-provided
    # - This must happen BEFORE any status checks to ensure invariant is set
    if plan_status == "READY":
        facts = decision.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}
        slots = facts.get("slots", {})
        if isinstance(slots, dict):
            has_time = bool(slots.get("time"))
            has_date = bool(slots.get("date"))
            has_date_range = isinstance(slots.get("date_range"), dict) and bool(slots.get("date_range", {}).get("start"))
            has_datetime_range = isinstance(slots.get("datetime_range"), dict) and bool(slots.get("datetime_range", {}).get("start"))
            
            # Check if sufficient temporal information exists
            has_sufficient_temporal = (
                (has_date and has_time) or  # date + time
                (has_date_range and has_time) or  # date_range + time
                has_datetime_range  # datetime_range
            )
            
            if has_sufficient_temporal:
                # Ensure facts["slots"] exists and is a dict
                if "slots" not in facts:
                    facts["slots"] = {}
                if not isinstance(facts["slots"], dict):
                    facts["slots"] = {}
                
                # Set has_datetime invariant (derived, not user-provided)
                facts["slots"]["has_datetime"] = True
                # Update decision facts with has_datetime
                decision["facts"] = facts
                logger.debug(f"Set has_datetime=true in facts.slots (planning invariant: READY with temporal info)")
    
    
    # PLANNING-ONLY SHORTCUT: Return planning outcome early if planning_only=True
    # This allows tests to validate planning logic without executing actions or mutating session
    # SINGLE SOURCE OF TRUTH: Construct flattened response directly from plan and facts
    if planning_only:
        intent_name = decision.get("intent_name", "")
        
        # Extract stage and action from plan (single source of truth)
        stage = plan.get("stage")
        action = plan.get("action")
        
        # FIND REAL SOURCES: Check ALL possible locations for missing_slots and slots
        # Priority order: decision.facts > decision top-level > effective_response > plan
        # NOTE: plan does NOT contain missing_slots/slots (only stage/action/status)
        missing_slots = None
        slots = None
        
        # 1. Check decision.facts first (primary source from process_luma_response)
        facts = decision.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}
        if facts.get("missing_slots") is not None:
            missing_slots = facts.get("missing_slots")
        if facts.get("slots") is not None:
            slots = facts.get("slots")
        
        # 2. Check decision top-level (may have data in some paths)
        if missing_slots is None and decision.get("missing_slots") is not None:
            missing_slots = decision.get("missing_slots")
        if slots is None and decision.get("slots") is not None:
            slots = decision.get("slots")
        
        # 3. Fallback to effective_response (source before process_luma_response)
        # This is guaranteed to have missing_slots (asserted earlier)
        if missing_slots is None and effective_response.get("missing_slots") is not None:
            missing_slots = effective_response.get("missing_slots")
        if slots is None and effective_response.get("slots") is not None:
            slots = effective_response.get("slots")
        
        # 4. Check plan (unlikely, but check for completeness)
        if missing_slots is None and plan.get("missing_slots") is not None:
            missing_slots = plan.get("missing_slots")
        if slots is None and plan.get("slots") is not None:
            slots = plan.get("slots")
        
        # 5. Final fallback: empty defaults (should never happen due to assertion)
        if missing_slots is None:
            missing_slots = []
        if slots is None:
            slots = {}
        
        # Ensure correct types
        if not isinstance(missing_slots, list):
            missing_slots = []
        if not isinstance(slots, dict):
            slots = {}
        
        # CRITICAL: For planning_only, use RAW Luma fact values (not normalized aliases)
        # Tests expect raw values like "massage", not normalized like "beauty_and_wellness.massage"
        # Normalized values remain internal-only for execution paths
        # This applies ONLY to planning_only responses - execution paths still use normalized values
        if effective_response and isinstance(effective_response, dict):
            raw_luma_response = effective_response.get("_raw_luma_response", {})
            if isinstance(raw_luma_response, dict):
                raw_luma_facts = raw_luma_response.get("facts", {})
                if isinstance(raw_luma_facts, dict) and raw_luma_facts:
                    # Start with normalized slots (has time, date, etc. from normalization)
                    raw_slots = slots.copy() if isinstance(slots, dict) else {}
                    
                    # Override service_id with raw fact value (tests expect raw, not normalized alias)
                    if "service_id" in raw_luma_facts:
                        raw_slots["service_id"] = raw_luma_facts["service_id"]
                    elif isinstance(effective_response.get("facts"), dict):
                        raw_facts_in_effective = effective_response.get("facts", {}).get("service_id")
                        if raw_facts_in_effective:
                            raw_slots["service_id"] = raw_facts_in_effective
                    
                    slots = raw_slots
        
        # CRITICAL: Always populate plan object with all required fields
        # This ensures plan.stage and plan.action are always present (no silent failures)
        populated_plan = {
            "intent": intent_name,
            "stage": stage,
            "action": action,
            "missing_slots": missing_slots,
            "slots": slots,
            "status": plan_status,
            "executable_actions": plan.get("executable_actions", []),
            "allowed_actions": plan.get("allowed_actions", []),
            "blocked_actions": plan.get("blocked_actions", [])
        }
        
        # CRITICAL: Ensure outcome always uses raw service_id (not canonical)
        # Dialog output, outcome.slots, outcome.facts.slots MUST use raw tenant value
        outcome_slots = slots.copy() if isinstance(slots, dict) else {}
        
        # Get raw service_id from session or effective_response
        # Priority: 1) session.slots["service_id"], 2) effective_response slots, 3) current slots
        raw_service_id_for_outcome = None
        if session_state and isinstance(session_state, dict):
            session_slots = session_state.get("slots", {})
            if isinstance(session_slots, dict) and "service_id" in session_slots:
                raw_service_id_for_outcome = session_slots["service_id"]
        
        if not raw_service_id_for_outcome and effective_response:
            effective_slots = effective_response.get("slots", {})
            if isinstance(effective_slots, dict) and "service_id" in effective_slots:
                raw_service_id_for_outcome = effective_slots["service_id"]
        
        if not raw_service_id_for_outcome and "service_id" in outcome_slots:
            raw_service_id_for_outcome = outcome_slots["service_id"]
        
        # Always use raw service_id in outcome
        # Priority: session raw > effective_response raw > current slots
        if raw_service_id_for_outcome:
            outcome_slots["service_id"] = raw_service_id_for_outcome
            logger.debug(f"Using raw service_id in outcome: {raw_service_id_for_outcome}")
        elif "service_id" in outcome_slots:
            # Keep existing service_id if no raw found (shouldn't happen but fail-safe)
            logger.debug(f"Using existing service_id in outcome: {outcome_slots.get('service_id')}")
        
        # Remove canonical from outcome slots (never expose canonical to tests/dialog)
        if "_canonical_service_id" in outcome_slots:
            del outcome_slots["_canonical_service_id"]
            logger.debug(f"Removed _canonical_service_id from outcome, using raw service_id: {outcome_slots.get('service_id')}")
        
        # Construct flattened planning response with both structures:
        # 1. Flattened fields at outcome level (for test compatibility)
        # 2. Complete plan object (for observability and debugging)
        # 3. Facts object for backward compatibility (snapshot builder reads outcome.facts.*)
        result = {
            "success": True,
            "outcome": {
                "intent_name": intent_name,
                "stage": stage,
                "action": action,
                "missing_slots": missing_slots,
                "slots": outcome_slots,  # Use raw service_id only
                "status": plan_status,
                "plan": populated_plan,  # Always include complete plan object
                "facts": {
                    "missing_slots": missing_slots,
                    "slots": outcome_slots  # Use raw service_id only
                }
            }
        }
        
        # Store effective Luma response for session building (for test snapshots)
        if effective_response and "_raw_luma_response" in effective_response:
            result["_merged_luma_response"] = effective_response
        
        # GUARD LOG: Final plan values before return
        logger.error(
            "[PLAN_FINAL] stage=%s action=%s missing=%s slots=%s",
            populated_plan["stage"], populated_plan["action"], 
            populated_plan["missing_slots"], populated_plan["slots"]
        )
        
        # OUTCOME: Log final outcome structure
        outcome = result.get("outcome", {})
        outcome_slots = outcome.get("slots", {})
        outcome_missing_slots = outcome.get("missing_slots", [])
        logger.info("[OUTCOME] user_id=%s intent=%s stage=%s action=%s missing_slots=%s slots=%s",
                    user_id, intent_name, outcome.get("stage"), outcome.get("action"),
                    outcome_missing_slots, json.dumps(outcome_slots, default=str, ensure_ascii=True))
        
        return result

    # Handle AWAITING_CONFIRMATION status
    if plan_status == "AWAITING_CONFIRMATION":
        # Return confirmation prompt outcome
        booking = decision.get("booking", {})
        facts = decision.get("facts", {})
        # Include _raw_luma_response in facts for test snapshots (preserved from effective_response)
        if effective_response and "_raw_luma_response" in effective_response:
            if not isinstance(facts, dict):
                facts = {}
            facts["_raw_luma_response"] = effective_response["_raw_luma_response"]
        result = {
            "success": True,
            "outcome": {
                "status": "AWAITING_CONFIRMATION",
                "awaiting": awaiting,
                "booking": booking,
                "allowed_actions": allowed_actions,
                "blocked_actions": blocked_actions,
                "facts": facts
            }
        }
        # Store effective Luma response for session building
        result["_merged_luma_response"] = effective_response
        return result

    # Handle NEEDS_CLARIFICATION status
    if plan_status == "NEEDS_CLARIFICATION":
        # Check if there's an outcome (clarification) or error
        if "outcome" in decision:
            # decision["outcome"] is already a complete outcome dict with success/outcome structure
            # from _build_clarify_outcome, so return it directly
            # Store effective Luma response for session building (private field, ignored by existing code)
            result = decision["outcome"]
            # CRITICAL: Ensure plan is attached to outcome (tests check outcome.plan.stage/action)
            if "outcome" in result:
                # Plan might already be in outcome, or we need to add it
                if "plan" not in result["outcome"]:
                    result["outcome"]["plan"] = plan
                # Promote stage/action/missing_slots/slots from plan/facts to top-level outcome for test compatibility
                plan_obj = result["outcome"].get("plan", plan)
                promoted_stage = plan_obj.get("stage")
                promoted_action = plan_obj.get("action")
                result["outcome"]["stage"] = promoted_stage
                result["outcome"]["action"] = promoted_action
                # Also promote missing_slots and slots if available in facts
                facts_obj = result["outcome"].get("facts", {})
                if "missing_slots" in facts_obj:
                    result["outcome"]["missing_slots"] = facts_obj["missing_slots"]
                if "slots" in facts_obj:
                    result["outcome"]["slots"] = facts_obj["slots"]
            result["_merged_luma_response"] = effective_response
            return result
        if "error" in decision:
            return {
                "success": False,
                "error": decision["error"],
                "message": decision.get("message", "An error occurred")
            }
        
        # Synthesize clarification outcome when Luma didn't provide one (follow-up turns)
        # Core's responsibility: generate clarification from intent, missing_slots, and domain
        intent_name = decision.get("intent_name", "")
        facts = decision.get("facts", {})
        
        # Get missing_slots from facts (already merged/normalized from process_luma_response)
        # Get missing_slots from facts or effective_response (computed by session merge)
        # ARCHITECTURAL INVARIANT: missing_slots is computed exactly once per turn in session merge
        # missing_slots MUST NOT be recomputed here - it is a pure derived value
        # missing_slots = [] is VALID and means all required slots are satisfied
        missing_slots = None
        if "missing_slots" in facts:
            facts_missing = facts.get("missing_slots")
            if isinstance(facts_missing, list):
                missing_slots = facts_missing  # Use facts missing_slots (even if [])
        
        # If not in facts, try effective_response
        if missing_slots is None and "missing_slots" in effective_response:
            response_missing = effective_response.get("missing_slots")
            if isinstance(response_missing, list):
                missing_slots = response_missing  # Use response missing_slots (even if [])
        
        # INVARIANT CHECK: missing_slots must be a list (never None after merge)
        if missing_slots is None:
            # This should never happen if merge ran correctly
            logger.error(
                f"[MISSING_SLOTS] VIOLATION: missing_slots is None in orchestrator! "
                f"user_id={user_id}, intent={intent_name}, "
                f"facts_keys={list(facts.keys())}, effective_response_keys={list(effective_response.keys())}"
            )
            # Fail-safe: use empty list (but this indicates a bug)
            missing_slots = []
        
        # INVARIANT CHECK: missing_slots must be a list
        assert isinstance(missing_slots, list), (
            f"missing_slots must be a list, got {type(missing_slots)}: {missing_slots}"
        )
        
        # DEBUG: Log why we're synthesizing clarification
        logger.info(
            f"[SYNTHESIZE_CLARIFICATION] user_id={user_id} intent={intent_name} "
            f"missing_slots_from_facts={facts.get('missing_slots')} "
            f"missing_slots_from_response={effective_response.get('missing_slots')} "
            f"final_missing_slots={missing_slots} "
            f"facts_slots={facts.get('slots', {})} "
            f"effective_response_slots={effective_response.get('slots', {})} "
            f"effective_response_booking_services={effective_response.get('booking', {}).get('services') if isinstance(effective_response.get('booking'), dict) else None}"
        )
        
        # Normalize missing_slots (especially for MODIFY_BOOKING) - safety check
        # Import here to avoid circular dependency
        from core.orchestration.nlu.luma_response_processor import _normalize_modify_booking_missing_slots
        missing_slots = _normalize_modify_booking_missing_slots(missing_slots, effective_response)
        
        # INVARIANT CHECK: After normalization, missing_slots must still be a list
        assert isinstance(missing_slots, list), (
            f"missing_slots must be a list after normalization, got {type(missing_slots)}: {missing_slots}"
        )
        
        # CRITICAL: missing_slots = [] is VALID - it means all required slots are satisfied
        # If status is NEEDS_CLARIFICATION but missing_slots = [], this indicates a logic error
        # But we should not override missing_slots - it is a pure derived value
        if len(missing_slots) == 0:
            logger.warning(
                f"NEEDS_CLARIFICATION status but missing_slots is empty for user {user_id}. "
                f"This may indicate a logic error, but missing_slots is a pure derived value and will not be overridden."
            )
        
        # Build issues dict from missing_slots for clarification generation
        issues = {slot: "missing" for slot in missing_slots}
        
        # Extract context and booking from effective_response
        context = effective_response.get("context", {})
        booking = effective_response.get("booking")
        
        # Build clarification outcome using build_clarify_outcome_from_reason
        # (already imported at top of file, but import here for clarity)
        from core.orchestration.nlu.luma_response_processor import _derive_clarification_reason_from_missing_slots
        
        # Derive clarification reason from missing slots
        clarification_reason = _derive_clarification_reason_from_missing_slots(missing_slots)
        
        # Ensure facts has normalized missing_slots
        facts["missing_slots"] = missing_slots
        
        # Include _raw_luma_response in facts for test snapshots (preserved from effective_response)
        if effective_response and "_raw_luma_response" in effective_response:
            if not isinstance(facts, dict):
                facts = {}
            facts["_raw_luma_response"] = effective_response["_raw_luma_response"]
        
        # Build clarification outcome
        result = build_clarify_outcome_from_reason(
            reason=clarification_reason,
            issues=issues,
            booking=booking,
            domain=derived_domain,
            facts=facts
        )
        
        # Set intent_name if available
        if intent_name and "outcome" in result:
            result["outcome"]["intent_name"] = intent_name
        
        # Add plan to outcome for session building
        if "outcome" in result:
            result["outcome"]["plan"] = plan
            
            # Promote stage/action/missing_slots/slots to top-level outcome for test compatibility
            # Always copy stage/action from plan, even if None
            synthesized_stage = plan.get("stage")
            synthesized_action = plan.get("action")
            result["outcome"]["stage"] = synthesized_stage
            result["outcome"]["action"] = synthesized_action
            # Also promote missing_slots and slots if available in facts
            facts_obj = result["outcome"].get("facts", {})
            if "missing_slots" in facts_obj:
                result["outcome"]["missing_slots"] = facts_obj["missing_slots"]
            if "slots" in facts_obj:
                result["outcome"]["slots"] = facts_obj["slots"]
        
        # Store effective Luma response for session building
        result["_merged_luma_response"] = effective_response
        
        logger.info(
            f"Synthesized clarification outcome for user {user_id}: "
            f"intent={intent_name}, missing_slots={missing_slots}, reason={clarification_reason}"
        )
        
        return result

    # Step 5: Return planning-only outcome (plan_status == "READY")
    # Core NEVER executes - execution is handled by separate layer
    # Return planning outcome matching core contract: {intent, slots, missing_slots, executable_actions, dialog_instruction?}
    if plan_status == "READY":
        intent_name = decision.get("intent_name", "")
        facts = decision.get("facts", {})
        slots = facts.get("slots", {})
        missing_slots = facts.get("missing_slots", [])
        executable_actions = plan.get("executable_actions", [])
        
        # Include _raw_luma_response in facts for test snapshots (preserved from effective_response)
        if effective_response and "_raw_luma_response" in effective_response:
            if not isinstance(facts, dict):
                facts = {}
            facts["_raw_luma_response"] = effective_response["_raw_luma_response"]
        
        # Build planning-only outcome matching core contract
        planning_outcome = _build_planning_outcome(
            intent_name=intent_name,
            slots=slots,
            missing_slots=missing_slots,
            executable_actions=executable_actions,
            status="READY"
        )
        
        # CRITICAL: Extract stage/action from plan for top-level promotion
        stage = plan.get("stage")
        action = plan.get("action")
        
        result = {
            "success": True,
            "outcome": {
                "status": "READY",
                "intent_name": intent_name,
                **planning_outcome,
                "facts": facts,
                "plan": plan  # CRITICAL: Plan must be in outcome (tests check outcome.plan.stage/action)
            }
        }
        
        # Promote stage/action/missing_slots/slots to top-level outcome (required by tests)
        # Tests assert outcome.stage / outcome.action / outcome.missing_slots / outcome.slots directly
        # Always copy stage/action from plan, even if None
        result["outcome"]["stage"] = stage
        result["outcome"]["action"] = action
        result["outcome"]["missing_slots"] = missing_slots
        result["outcome"]["slots"] = slots
        
        # Store effective Luma response for session building
        result["_merged_luma_response"] = effective_response
        return result
    
    # This should never be reached - all statuses handled above
    logger.error(f"Unexpected plan_status: {plan_status} for user {user_id}")
    return {
        "success": False,
        "error": "internal_error",
        "message": f"Unexpected plan status: {plan_status}"
    }
    # Priority: commit action if allowed, otherwise first allowed fallback
    action_to_execute = None

    # Get commit action from plan (if any allowed action is a commit action)
    intent_name = decision.get("intent_name", "")

    # Enforce core intent boundary: pass through non-core intents without orchestration
    if intent_name:
        from core.routing.intents.base_intents import is_core_intent
        if not is_core_intent(intent_name):
            # Pass through non-core intents as non-orchestrated signals
            # This preserves conversational continuity and enables workflow extensions
            return _handle_non_core_intent(effective_response, decision, user_id)

    from core.orchestration.nlu.luma_response_processor import _load_intent_execution_config
    intent_configs = _load_intent_execution_config()
    intent_config = intent_configs.get(intent_name, {})
    commit_config = intent_config.get("commit", {})
    commit_action = commit_config.get(
        "action") if isinstance(commit_config, dict) else None

    # Prefer commit action if it's allowed
    if commit_action and commit_action in allowed_actions:
        action_to_execute = commit_action
    elif allowed_actions:
        # Use first allowed action (fallback)
        action_to_execute = allowed_actions[0]
    else:
        # No allowed actions - this should not happen for READY status, but handle gracefully
        logger.warning(
            f"No allowed actions in plan for user {user_id}. "
            f"Status: {plan_status}, Blocked: {blocked_actions}"
        )
        return {
            "success": False,
            "error": "no_allowed_actions",
            "message": "No actions are allowed to execute at this time"
        }

    # Map action to handler
    handler_action = get_handler_action(action_to_execute)
    if not handler_action:
        # Fallback to legacy action_name for backward compatibility
        handler_action = decision.get("action_name")
        if not handler_action:
            logger.error(
                f"Could not map action {action_to_execute} to handler for user {user_id}"
            )
            return {
                "success": False,
                "error": "unsupported_action",
                "message": f"Action {action_to_execute} is not supported"
            }

    # Verify action is allowed (safety check)
    if action_to_execute in blocked_actions:
        logger.error(
            f"Attempted to execute blocked action {action_to_execute} for user {user_id}"
        )
        return {
            "success": False,
            "error": "action_blocked",
            "message": f"Action {action_to_execute} is blocked"
        }

    action_name = handler_action
    booking = decision["booking"]

    # Determine booking_type from intent_name if not explicitly set in booking
    # CREATE_RESERVATION -> "reservation", CREATE_APPOINTMENT -> "service"
    booking_type = booking.get("booking_type")
    if not booking_type:
        if intent_name == "CREATE_RESERVATION":
            booking_type = "reservation"
        elif intent_name == "CREATE_APPOINTMENT":
            booking_type = "service"
        else:
            booking_type = "service"  # Default fallback
    booking["booking_type"] = booking_type  # Ensure it's set in booking object

    # Helper function to check if slots have any temporal structure (date/date_range/datetime_range)
    def has_any_date(slots_dict: Dict[str, Any]) -> bool:
        """Check if slots contain any temporal structure (date, date_range, or datetime_range)."""
        return (
            slots_dict.get("date") or
            (isinstance(slots_dict.get("date_range"), dict) and slots_dict["date_range"].get("start")) or
            (isinstance(slots_dict.get("datetime_range"), dict) and slots_dict["datetime_range"].get("start"))
        )

    # Extract service and datetime_range from facts.slots if missing in booking
    # Luma may provide these in slots instead of booking object
    facts = decision.get("facts", {})
    slots = facts.get("slots", {})

    if not booking.get("services") and booking_type == "service":
        service_id = slots.get("service_id")
        if service_id:
            # Convert service_id string to services array format
            booking["services"] = [{"text": service_id}]
            logger.info(
                f"Extracted service from facts.slots.service_id: {service_id}")

    # For reservations, extract service_id as room identifier
    if not booking.get("services") and booking_type == "reservation":
        service_id = slots.get("service_id")
        if service_id:
            booking["services"] = [{"text": service_id}]
            logger.info(
                f"Extracted room/service from facts.slots.service_id: {service_id}")

    # Extract datetime_range from facts.slots if missing in booking
    if not booking.get("datetime_range"):
        # Try datetime_range first
        if slots.get("datetime_range"):
            datetime_range = slots.get("datetime_range")
            if datetime_range:
                booking["datetime_range"] = datetime_range
                logger.info(
                    f"Extracted datetime_range from facts.slots: {datetime_range}")
        # Try date_range (with start_date/end_date) and convert to datetime_range format
        elif slots.get("date_range"):
            date_range = slots.get("date_range")
            if isinstance(date_range, dict):
                start_date = date_range.get(
                    "start_date") or date_range.get("start")
                end_date = date_range.get("end_date") or date_range.get("end")
                if start_date and end_date:
                    # Convert date_range to datetime_range format
                    # For reservations, dates are typically date-only, so we'll use them as-is
                    # and let the execution backend handle time if needed
                    booking["datetime_range"] = {
                        "start": start_date,
                        "end": end_date
                    }
                    logger.info(
                        f"Converted date_range to datetime_range: start={start_date}, end={end_date}")
        # For service bookings: construct datetime_range from date/date_range + time
        # Use helper to check for any temporal structure (date, date_range, datetime_range) + time
        elif booking_type == "service" and has_any_date(slots) and slots.get("time"):
            from datetime import datetime as dt
            try:
                # Extract date from any temporal structure
                date_str = None
                if slots.get("date"):
                    date_str = str(slots.get("date"))
                elif isinstance(slots.get("date_range"), dict):
                    # For date_range, use the start date
                    date_range = slots.get("date_range")
                    date_str = str(date_range.get("start") or date_range.get("start_date"))
                elif isinstance(slots.get("datetime_range"), dict):
                    # For datetime_range, extract date part from start
                    datetime_range = slots.get("datetime_range")
                    start = datetime_range.get("start")
                    if start:
                        date_str = str(start).split("T")[0].split(" ")[0]
                
                if not date_str:
                    raise ValueError("No date found in slots")
                
                time_str = str(slots.get("time"))
                
                # Parse date (assume YYYY-MM-DD format)
                date_obj = None
                if isinstance(date_str, str):
                    # Remove time component if present (take only date part)
                    date_only = date_str.split("T")[0].split(" ")[0]
                    try:
                        date_obj = dt.strptime(date_only, "%Y-%m-%d")
                    except ValueError:
                        # Try ISO format
                        try:
                            date_obj = dt.fromisoformat(date_only)
                        except (ValueError, AttributeError):
                            pass
                
                # Parse time (assume HH:MM or HH:MM:SS format)
                if date_obj:
                    # Normalize time string (remove spaces, handle formats like "11am", "11:00", etc.)
                    time_normalized = time_str.lower().replace("am", "").replace("pm", "").strip()
                    if ":" in time_normalized:
                        time_parts = time_normalized.split(":")
                    else:
                        # Assume format like "11" means 11:00
                        time_parts = [time_normalized, "00"]
                    
                    if len(time_parts) >= 2:
                        try:
                            hour = int(time_parts[0])
                            minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                            
                            # Handle AM/PM
                            if "pm" in time_str.lower() and hour < 12:
                                hour += 12
                            elif "am" in time_str.lower() and hour == 12:
                                hour = 0
                            
                            # Combine date and time
                            start_datetime = date_obj.replace(hour=hour, minute=minute, second=0, microsecond=0)
                            # For service bookings, end time will be computed from duration
                            # For now, set end = start (duration will be added later if needed)
                            end_datetime = start_datetime
                            
                            booking["datetime_range"] = {
                                "start": start_datetime.isoformat(),
                                "end": end_datetime.isoformat()
                            }
                            logger.info(
                                f"Constructed datetime_range from date+time: {booking['datetime_range']}")
                        except (ValueError, IndexError, TypeError) as e:
                            # If parsing fails, construct as ISO string
                            booking["datetime_range"] = {
                                "start": f"{date_str}T{time_str}:00",
                                "end": f"{date_str}T{time_str}:00"
                            }
                            logger.info(
                                f"Constructed datetime_range from date+time (fallback): {booking['datetime_range']}")
                    else:
                        # Time format not recognized, use date + time as string
                        booking["datetime_range"] = {
                            "start": f"{date_str}T{time_str}:00",
                            "end": f"{date_str}T{time_str}:00"
                        }
                        logger.info(
                            f"Constructed datetime_range from date+time (string): {booking['datetime_range']}")
                else:
                    # Date parsing failed, use string concatenation
                    booking["datetime_range"] = {
                        "start": f"{date_str}T{time_str}:00",
                        "end": f"{date_str}T{time_str}:00"
                    }
                    logger.info(
                        f"Constructed datetime_range from date+time (string fallback): {booking['datetime_range']}")
            except Exception as e:
                logger.warning(
                    f"Failed to construct datetime_range from date+time: {e}. "
                    f"slots={list(slots.keys())}, time={slots.get('time')}")
                # Final fallback: construct as string concatenation
                # Try to get date from any temporal structure
                fallback_date = None
                if slots.get("date"):
                    fallback_date = str(slots.get("date"))
                elif isinstance(slots.get("date_range"), dict):
                    date_range = slots.get("date_range")
                    fallback_date = str(date_range.get("start") or date_range.get("start_date"))
                elif isinstance(slots.get("datetime_range"), dict):
                    datetime_range = slots.get("datetime_range")
                    start = datetime_range.get("start")
                    if start:
                        fallback_date = str(start).split("T")[0].split(" ")[0]
                
                if fallback_date and slots.get("time"):
                    booking["datetime_range"] = {
                        "start": f"{fallback_date}T{slots.get('time')}:00",
                        "end": f"{fallback_date}T{slots.get('time')}:00"
                    }

    # Set has_datetime in facts.slots when both date and time are present
    # Use helper to check for any temporal structure AND time
    has_date = has_any_date(slots)
    has_time = slots.get("time")
    
    if booking_type == "service" and has_date and has_time:
        # Preserve existing slots (including service_id) when setting has_datetime
        # Facts already has slots from decision - just add has_datetime to it
        if "slots" not in facts:
            facts["slots"] = {}
        # Ensure slots is a dict and preserve all existing slots
        if not isinstance(facts["slots"], dict):
            facts["slots"] = {}
        # Merge accumulated slots into facts.slots to preserve service_id
        facts["slots"] = {**slots, **facts["slots"]}
        facts["slots"]["has_datetime"] = True
        logger.info("Set has_datetime=true in facts.slots (date and time both present)")
    
    # Log processed booking
    logger.debug("Processed booking: %s", json.dumps(
        booking, ensure_ascii=False, default=str))

    # Resolve service item_id using catalog discovery (no org details scanning)
    def _resolve_service_id(services_from_luma: list, catalog_services_list: list[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Resolve service item_id deterministically.

        Rules:
        - If services[].text provided: exact name match (case-insensitive) → service ID
        - Else, use canonical if exactly one active service matches
        - If multiple matches for canonical → clarification
        - If no match → clarification
        """
        if not catalog_services_list:
            return {"item_id": None, "clarification": True, "reason": "MISSING_SERVICE"}

        active_services = [
            s for s in catalog_services_list
            if isinstance(s, dict) and s.get("is_active", True) is not False
        ]

        if not services_from_luma:
            return {"item_id": None, "clarification": True, "reason": "MISSING_SERVICE"}

        svc = services_from_luma[0] if isinstance(
            services_from_luma, list) else services_from_luma
        if not isinstance(svc, dict):
            return {"item_id": None, "clarification": True, "reason": "MISSING_SERVICE"}

        text_name = svc.get("text")
        canonical = svc.get("canonical") or svc.get(
            "service_family_id") or svc.get("slug")

        # Name match
        if text_name:
            name_lower = str(text_name).lower()
            matches = [s for s in active_services if str(
                s.get("name", "")).lower() == name_lower]
            if len(matches) == 1:
                return {"item_id": matches[0].get("id"), "clarification": False}
            if len(matches) > 1:
                return {"item_id": None, "clarification": True, "reason": "SERVICE_VARIANT_AMBIGUITY"}

        # Canonical match (only if exactly one)
        if canonical:
            canonical_lower = str(canonical).lower()
            matches = [
                s for s in active_services
                if str(s.get("canonical") or s.get("slug") or "").lower() == canonical_lower
            ]
            if len(matches) == 1:
                return {"item_id": matches[0].get("id"), "clarification": False}
            if len(matches) > 1:
                return {"item_id": None, "clarification": True, "reason": "SERVICE_VARIANT_AMBIGUITY"}

        return {"item_id": None, "clarification": True, "reason": "MISSING_SERVICE"}

    try:
        if action_name == "booking.create":
            resolved_item_id = None
            if booking_type == "service" and derived_domain != "reservation":
                # Use cached catalog data to resolve ID
                if catalog_data_for_alias is None:
                    catalog_data_for_alias = catalog_cache.get_catalog(
                        resolved_org_id, catalog_client, domain="service")
                catalog_services_for_resolution = catalog_data_for_alias.get(
                    "services", []) if isinstance(catalog_data_for_alias, dict) else []
                booking_services = booking.get("services", [])
                resolution = _resolve_service_id(booking_services, catalog_services_for_resolution)
                if resolution.get("clarification"):
                    reason = resolution.get("reason", "MISSING_SERVICE")
                    return build_clarify_outcome_from_reason(
                        reason=reason,
                        issues={"service": "missing"},
                        booking=booking,
                        domain=derived_domain
                    )
                resolved_item_id = resolution.get("item_id")
                # Inject resolved id back into booking for downstream usage
                services_list = booking.get("services")
                if services_list and isinstance(services_list, list) and isinstance(services_list[0], dict):
                    services_list[0]["id"] = resolved_item_id
                booking["_resolved_item_id"] = resolved_item_id
            elif booking_type == "reservation" or derived_domain == "reservation":
                if catalog_data_for_alias is None:
                    catalog_data_for_alias = catalog_cache.get_catalog(
                        resolved_org_id, catalog_client, domain="reservation")
                rooms_catalog = catalog_data_for_alias.get(
                    "rooms", []) if isinstance(catalog_data_for_alias, dict) else []
                catalog_extras = catalog_data_for_alias.get(
                    "extras", []) if isinstance(catalog_data_for_alias, dict) else []

                def _resolve_room_type(room_from_luma: Dict[str, Any], rooms: list[Dict[str, Any]]) -> Dict[str, Any]:
                    if not rooms:
                        return {"room_type_id": None, "clarification": True, "reason": "MISSING_ROOM_TYPE"}
                    active_rooms = [r for r in rooms if isinstance(
                        r, dict) and r.get("is_active", True) is not False]
                    if not room_from_luma or not isinstance(room_from_luma, dict):
                        return {"room_type_id": None, "clarification": True, "reason": "MISSING_ROOM_TYPE"}
                    text_name = room_from_luma.get("text")
                    canonical = room_from_luma.get("canonical_key") or room_from_luma.get(
                        "canonical") or room_from_luma.get("slug")
                    if text_name:
                        name_lower = str(text_name).lower()
                        matches = [r for r in active_rooms if str(
                            r.get("name", "")).lower() == name_lower]
                        if len(matches) == 1:
                            return {"room_type_id": matches[0].get("id"), "clarification": False}
                        if len(matches) > 1:
                            return {"room_type_id": None, "clarification": True, "reason": "ROOM_VARIANT_AMBIGUITY"}
                    if canonical:
                        canonical_lower = str(canonical).lower()
                        matches = [
                            r for r in active_rooms
                            if str(r.get("canonical_key") or r.get("canonical") or r.get("slug") or "").lower() == canonical_lower
                        ]
                        if len(matches) == 1:
                            return {"room_type_id": matches[0].get("id"), "clarification": False}
                        if len(matches) > 1:
                            return {"room_type_id": None, "clarification": True, "reason": "ROOM_VARIANT_AMBIGUITY"}
                    return {"room_type_id": None, "clarification": True, "reason": "MISSING_ROOM_TYPE"}

                def _resolve_extras(extras_from_luma: list, extras_catalog: list[Dict[str, Any]], room_type_id: Optional[int]) -> Dict[str, Any]:
                    if not extras_from_luma:
                        return {"extras": [], "clarification": False}
                    active_extras = [e for e in extras_catalog if isinstance(
                        e, dict) and e.get("is_active", True) is not False]
                    resolved_extras = []
                    for ex in extras_from_luma:
                        if not isinstance(ex, dict):
                            return {"extras": None, "clarification": True, "reason": "INVALID_EXTRA"}
                        text_name = ex.get("text")
                        canonical = ex.get("canonical") or ex.get("slug")
                        match = None
                        if text_name:
                            name_lower = str(text_name).lower()
                            name_matches = [e for e in active_extras if str(
                                e.get("name", "")).lower() == name_lower]
                            if len(name_matches) == 1:
                                match = name_matches[0]
                            elif len(name_matches) > 1:
                                return {"extras": None, "clarification": True, "reason": "EXTRA_VARIANT_AMBIGUITY"}
                        if match is None and canonical:
                            canonical_lower = str(canonical).lower()
                            canonical_matches = [e for e in active_extras if str(
                                e.get("canonical") or e.get("slug") or "").lower() == canonical_lower]
                            if len(canonical_matches) == 1:
                                match = canonical_matches[0]
                            elif len(canonical_matches) > 1:
                                return {"extras": None, "clarification": True, "reason": "EXTRA_VARIANT_AMBIGUITY"}
                        if match is None:
                            return {"extras": None, "clarification": True, "reason": "INVALID_EXTRA"}

                        applies_all = match.get("applies_to_all", False)
                        applicable_room_types = match.get(
                            "applicable_room_types") or match.get("room_types") or []
                        if not applies_all and room_type_id is not None and applicable_room_types:
                            if room_type_id not in applicable_room_types:
                                return {"extras": None, "clarification": True, "reason": "EXTRA_NOT_APPLICABLE"}
                        resolved_extras.append({"id": match.get("id")})
                    return {"extras": resolved_extras, "clarification": False}

                room_candidates = booking.get(
                    "rooms") or booking.get("services") or []
                room_svc = room_candidates[0] if isinstance(
                    room_candidates, list) else room_candidates
                room_resolution = _resolve_room_type(
                    room_svc, rooms_catalog)
                if room_resolution.get("clarification"):
                    reason = room_resolution.get("reason", "MISSING_ROOM_TYPE")
                    return build_clarify_outcome_from_reason(
                        reason=reason,
                        issues={"room_type": "missing"},
                        booking=booking,
                        domain=derived_domain
                    )
                resolved_room_id = room_resolution.get("room_type_id")
                booking["_resolved_room_type_id"] = resolved_room_id
                extras_resolution = _resolve_extras(booking.get(
                    "extras", []), catalog_extras, resolved_room_id)
                if extras_resolution.get("clarification"):
                    reason = extras_resolution.get("reason", "INVALID_EXTRA")
                    return build_clarify_outcome_from_reason(
                        reason=reason,
                        issues={"extras": "missing"},
                        booking=booking,
                        domain=derived_domain
                    )
                resolved_extras = extras_resolution.get("extras", [])
                if resolved_extras is not None:
                    booking["_resolved_extras"] = resolved_extras
                    if isinstance(booking.get("extras"), list):
                        for idx, ex in enumerate(booking["extras"]):
                            if isinstance(ex, dict) and idx < len(resolved_extras):
                                booking["extras"][idx]["id"] = resolved_extras[idx].get(
                                    "id")

            # Execute full booking creation flow
            result = _execute_booking_creation(
                user_id=user_id,
                booking=booking,
                customer_client=customer_client,
                booking_client=booking_client,
                catalog_client=catalog_client,
                organization_id=resolved_org_id,
                catalog_data=catalog_data_for_alias,
                phone_number=phone_number,
                email=email,
                customer_id=customer_id
            )

            logger.info(f"Successfully created booking for user {user_id}")

            def _extract_booking_code(resp: Dict[str, Any]) -> Optional[Any]:
                candidates = [
                    resp.get("booking_code"),
                    resp.get("code"),
                ]
                booking_obj = resp.get("booking") if isinstance(
                    resp.get("booking"), dict) else None
                if booking_obj:
                    candidates.append(booking_obj.get("booking_code"))
                    candidates.append(booking_obj.get("code"))
                data_obj = resp.get("data") if isinstance(
                    resp.get("data"), dict) else None
                if data_obj:
                    booking_data = data_obj.get("booking") if isinstance(
                        data_obj.get("booking"), dict) else None
                    if booking_data:
                        candidates.append(booking_data.get("booking_code"))
                        candidates.append(booking_data.get("code"))
                        candidates.append(booking_data.get("id"))
                # Fallback to top-level id if present
                candidates.append(resp.get("id"))
                for c in candidates:
                    if c:
                        return c
                return None

            booking_code_extracted = _extract_booking_code(result)
            booking_data = None
            if isinstance(result, dict):
                booking_data = result.get("booking")
                if isinstance(result.get("data"), dict) and isinstance(result["data"].get("booking"), dict):
                    booking_data = result["data"]["booking"]

            status_extracted = (
                result.get("status")
                or (booking_data.get("status") if isinstance(booking_data, dict) else None)
                or "pending"
            )

            starts_at = booking_data.get("starts_at") if isinstance(
                booking_data, dict) else None
            ends_at = booking_data.get("ends_at") if isinstance(
                booking_data, dict) else None
            total_amount = booking_data.get("total_amount") if isinstance(
                booking_data, dict) else None
            reservation_fee = booking_data.get(
                "reservation_fee") if isinstance(booking_data, dict) else None
            booking_type_resp = booking_data.get(
                "type") if isinstance(booking_data, dict) else None

            # Build outcome with facts if date+time were present (for has_datetime check)
            # Use helper function to check for any temporal structure AND time
            outcome_has_date = has_any_date(slots)
            outcome_has_time = slots.get("time")
            
            outcome_facts = None
            if booking_type == "service" and outcome_has_date and outcome_has_time:
                # Preserve all accumulated slots (including service_id) in outcome facts
                outcome_facts = {
                    "slots": {
                        **slots,  # Include all accumulated slots (service_id, date, time, etc.)
                        "has_datetime": True
                    }
                }
            
            outcome = {
                "success": True,
                "outcome": {
                    "status": "EXECUTED",
                    "booking_code": booking_code_extracted,
                    "booking_status": status_extracted,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "total_amount": total_amount,
                    "reservation_fee": reservation_fee,
                    "booking_type": booking_type_resp,
                }
            }
            
            # Include facts if date+time were present
            if outcome_facts:
                outcome["outcome"]["facts"] = outcome_facts

            # Invoke workflow after_execute hook if registered
            outcome["outcome"] = _invoke_workflow_after_execute(
                intent_name, outcome["outcome"]
            )

            # Notify Luma about execution completion (for lifecycle tracking)
            if booking_code_extracted and luma_client:
                try:
                    result = luma_client.notify_execution(
                        user_id=user_id,
                        booking_id=booking_code_extracted,
                        domain=derived_domain
                    )
                    # Check if the endpoint doesn't exist (404 handled gracefully)
                    if result.get("error") == "endpoint_not_found":
                        logger.debug(
                            f"Luma /notify_execution endpoint not available (non-critical lifecycle update)"
                        )
                    else:
                        logger.info(
                            f"Notified Luma about execution completion for user {user_id}, booking_id={booking_code_extracted}"
                        )
                except Exception as e:  # noqa: BLE001
                    # Log but don't fail the request - lifecycle update is non-critical
                    # The notify_execution endpoint may not exist in Luma (404), which is fine
                    logger.debug(
                        f"Failed to notify Luma about execution (non-critical): {e}"
                    )

            return outcome

        elif action_name == "booking.modify":
            # Expect a booking reference and updates
            booking_code = booking.get("booking_code") or booking.get("code")
            if not booking_code:
                raise ValueError("booking_code is required for modification")

            updates = booking.get("updates") or {}
            # Fallback: build updates from datetime_range if present
            datetime_range = booking.get("datetime_range")
            if datetime_range and isinstance(datetime_range, dict):
                start = datetime_range.get("start")
                end = datetime_range.get("end")
                if start:
                    updates.setdefault("starts_at", start)
                if end:
                    updates.setdefault("ends_at", end)

            if not updates:
                raise ValueError(
                    "No updates supplied for booking modification")

            # Route execution by mode
            execution_backend = _get_execution_backend(booking_client)
            api_response = execution_backend.update_booking(
                booking_code=booking_code,
                organization_id=resolved_org_id,
                updates=updates,
            )

            booking_data = api_response.get("booking")
            if isinstance(api_response.get("data"), dict) and isinstance(api_response["data"].get("booking"), dict):
                booking_data = api_response["data"]["booking"]

            status_extracted = (
                api_response.get("status")
                or (booking_data.get("status") if isinstance(booking_data, dict) else None)
                or "updated"
            )

            outcome = {
                "success": True,
                "outcome": {
                    "status": "EXECUTED",
                    "booking_code": booking_code,
                    "booking_status": status_extracted,
                    "booking": booking_data,
                },
            }

            # Invoke workflow after_execute hook if registered
            outcome["outcome"] = _invoke_workflow_after_execute(
                intent_name, outcome["outcome"]
            )

            return outcome

        elif action_name == "booking.cancel":
            # Extract booking_code from booking payload
            booking_code = booking.get("booking_code") or booking.get("code")
            if not booking_code:
                raise ValueError("booking_code is required for cancellation")

            # Extract cancellation details from booking payload
            cancellation_type = booking.get(
                "cancellation_type", "user_initiated")
            reason = booking.get("reason")
            notes = booking.get("notes")
            refund_method = booking.get("refund_method")
            notify_customer = booking.get("notify_customer")

            # Route execution by mode
            execution_backend = _get_execution_backend(booking_client)
            api_response = execution_backend.cancel_booking(
                booking_code=booking_code,
                organization_id=resolved_org_id,
                cancellation_type=cancellation_type,
                reason=reason,
                notes=notes,
                refund_method=refund_method,
                notify_customer=notify_customer
            )

            logger.info(
                f"Successfully cancelled booking {booking_code} for user {user_id}")
            outcome = {
                "success": True,
                "outcome": {
                    "status": "EXECUTED",
                    "booking_code": booking_code,
                    "booking_status": api_response.get("status", "cancelled")
                }
            }

            # Invoke workflow after_execute hook if registered
            outcome["outcome"] = _invoke_workflow_after_execute(
                intent_name, outcome["outcome"]
            )

            return outcome
        elif action_name == "booking.inquiry":
            booking_code = booking.get("booking_code") or booking.get("code")
            if not booking_code:
                raise ValueError(
                    "booking_code is required for booking inquiry")

            # Route execution by mode
            execution_backend = _get_execution_backend(booking_client)
            api_response = execution_backend.get_booking(booking_code)
            booking_data = api_response.get("booking")
            if isinstance(api_response.get("data"), dict) and isinstance(api_response["data"].get("booking"), dict):
                booking_data = api_response["data"]["booking"]

            outcome = {
                "success": True,
                "outcome": {
                    "status": "EXECUTED",
                    "booking_code": booking_code,
                    "booking": booking_data or api_response,
                },
            }

            # Invoke workflow after_execute hook if registered
            outcome["outcome"] = _invoke_workflow_after_execute(
                intent_name, outcome["outcome"]
            )

            return outcome
        else:
            raise UnsupportedIntentError(
                f"Action {action_name} not implemented")

    except (UpstreamError, ValueError) as e:
        error_type = "upstream_error" if isinstance(
            e, UpstreamError) else "invalid_request"
        logger.error(
            f"Business API error for user {user_id} action {action_name}: {str(e)}")
        return {
            "success": False,
            "error": error_type,
            "message": str(e),
            "action": action_name
        }
    except UnsupportedIntentError as e:
        logger.error(f"Unsupported action for user {user_id}: {str(e)}")
        return {
            "success": False,
            "error": "unsupported_action",
            "message": str(e)
        }
