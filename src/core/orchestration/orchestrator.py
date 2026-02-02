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
from core.orchestration.persistence.durable_intents import is_durable_intent

logger = logging.getLogger(__name__)
# Dedicated turn-level logger (clean, minimal logs - ONE log per section)
turn_logger = logging.getLogger("core.turn_log")
turn_logger.setLevel(logging.INFO)

# IMMEDIATELY disable noisy logs - set to ERROR ONLY
logging.getLogger("core.orchestration").setLevel(logging.ERROR)
logging.getLogger("core.planning").setLevel(logging.ERROR)
logging.getLogger("core.slot").setLevel(logging.ERROR)
logging.getLogger("core.execution").setLevel(logging.ERROR)
logging.getLogger("core.nlu").setLevel(logging.ERROR)
logging.getLogger("core.session_merge").setLevel(logging.ERROR)


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
    text: str,
    user_id: str,
    luma_client: Optional[LumaClient] = None,
    availability_client: Optional[Any] = None,
    organization_client: Optional[OrganizationClient] = None,
    session_store: Optional[Any] = None,
    frozen_time: Optional[datetime] = None,
    organization_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Canonical Core entrypoint for handling user messages.

    This function orchestrates the full flow:
    1. Retrieves session state (if session_store provided)
    2. Calls Luma for NLU (via plan_message)
    3. Gets planning result (via plan_message)
    4. Dispatches execution based on plan.action (if applicable)
    5. Returns execution result or planning result

    This function does NOT:
    - Perform rendering
    - Perform booking or confirmation execution
    - Access catalog or payment systems

    Args:
        text: User message text
        user_id: User identifier
        luma_client: Injected Luma client instance (creates default if None)
        availability_client: Injected availability client instance (required for SEARCH_AVAILABILITY)
        organization_client: Injected organization client instance (creates default if None)
        session_store: Optional session store with get_session(user_id) method
        frozen_time: Optional frozen time for testing
        organization_id: Optional organization ID (defaults to ORG_ID env or 1)

    Returns:
        Dictionary with:
        - If execution occurred: execution result (e.g., availability slots)
        - If no execution: planning result (stage, action, slots, missing_slots, etc.)
        - On error: {"success": False, "error": "...", "message": "..."}
    """
    # Import execution dispatcher
    from core.orchestration.execution.dispatcher import execute

    # LOG 1 — TURN INPUT (first line of handle_message)
    turn_logger.info(
        json.dumps({
            "turn": "INPUT",
            "user_id": user_id,
            "text": text
        }, ensure_ascii=True)
    )

    # Get session state if session_store provided
    session_state = None
    if session_store is not None:
        try:
            if hasattr(session_store, 'get_session'):
                session_state = session_store.get_session(user_id)
            elif callable(session_store):
                session_state = session_store(user_id)
        except Exception as e:
            logger.warning(f"Failed to get session for user {user_id}: {e}")

    # LOG 3 — SESSION READ (immediately after session_store.get_session)
    turn_logger.info(
        json.dumps({
            "turn": "SESSION_READ",
            "session": session_state
        }, ensure_ascii=True, default=str)
    )

    # Call plan_message to get planning result
    # plan_message internally calls Luma and handle_message_legacy
    plan = plan_message(
        text=text,
        user_id=user_id,
        session_state=session_state,
        luma_client=luma_client,
        organization_client=organization_client,
        frozen_time=frozen_time
    )

    # Check if planning failed
    if not plan or plan.get("error"):
        return {
            "success": False,
            "error": plan.get("error", "planning_failed"),
            "message": plan.get("message", "Planning failed"),
            "plan": plan
        }

    # HARD GUARD: Skip execution if planning requires clarification
    # Execution should ONLY run when plan status indicates readiness (READY, AWAITING_CONFIRMATION, etc.)
    plan_status = plan.get("status")
    if plan_status == "NEEDS_CLARIFICATION":
        logger.debug(
            f"Skipping execution: plan status is NEEDS_CLARIFICATION. "
            f"Missing slots: {plan.get('missing_slots', [])}"
        )
        return {
            "success": True,
            "result": plan
        }

    # POLICY-DRIVEN EXECUTION SELECTION
    # Use intent_policy.yaml to determine if and which execution step should run
    # Only reached if plan status indicates readiness (not NEEDS_CLARIFICATION)
    intent_name = plan.get("intent_name") or plan.get("intent")
    slots = plan.get("slots", {})

    # Determine availability_resolved flag from session state
    # (availability is resolved if it was executed in a previous turn)
    availability_resolved = False
    if session_state:
        # Check if previous execution result indicates availability was resolved
        prev_result = session_state.get("last_execution_result")
        if prev_result and isinstance(prev_result, dict):
            result_type = prev_result.get("type")
            if result_type == "availability":
                availability_resolved = True

    # Select next execution step using policy
    from core.policy.intent_policy import select_next_execution_step

    flags = {
        "availability_resolved": availability_resolved
    }

    execution_step = select_next_execution_step(
        intent_name=intent_name,
        slots=slots,
        flags=flags
    )

    # Only execute if policy selected a step
    if execution_step:
        action = execution_step.get("action")
        client_name = execution_step.get("client", "")

        # Map client name to actual client instance
        execution_client = None
        if client_name == "availability_client":
            execution_client = availability_client
        elif client_name == "booking_client":
            # booking_client not yet supported in handle_message
            logger.warning(
                f"Execution step {action} requires {client_name}, but it's not yet supported")
            execution_step = None
        else:
            logger.warning(
                f"Unknown client name '{client_name}' for execution step {action}")
            execution_step = None

        # Only proceed if we have the required client
        if execution_step and execution_client is None:
            # Missing required client - return planning result (no error for clarification turns)
            # This prevents "missing_dependency" errors when user is clarifying
            logger.debug(
                f"Execution step {action} requires {client_name}, but client not provided. "
                "Returning planning result (likely clarification turn)."
            )
            return {
                "success": True,
                "result": plan
            }

        if execution_step and execution_client:
            # Ensure organization_id is in slots for execution
            if not slots.get("organization_id") and organization_id is not None:
                slots["organization_id"] = organization_id
            elif not slots.get("organization_id"):
                # Try to get from env as fallback
                slots["organization_id"] = _get_org_id_from_env()

            # Update plan with organization_id
            plan["slots"] = slots

            # Update plan action to match selected step
            plan["action"] = action

            try:
                # Execute the selected step
                if client_name == "availability_client":
                    execution_result = execute(
                        plan=plan,
                        availability_client=execution_client
                    )
                else:
                    # Other clients not yet supported
                    logger.warning(
                        f"Execution for {client_name} not yet implemented")
                    return {
                        "success": True,
                        "result": plan
                    }

                # Return execution result
                return {
                    "success": True,
                    "result": execution_result,
                    "plan": plan
                }
            except Exception as e:
                logger.error(f"Execution failed for action {action}: {e}")
                return {
                    "success": False,
                    "error": "execution_failed",
                    "message": str(e),
                    "plan": plan
                }

    # No execution step selected by policy - return planning result
    return {
        "success": True,
        "result": plan
    }


def handle_message_legacy(
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
    [LEGACY] Handle a user message - stateless orchestration.

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
            from core.orchestration.execution.clients.booking_client import BookingClient
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
        # Skip catalog access when planning_only=True to avoid network calls
        if not planning_only:
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
        else:
            # planning_only=True: Create minimal tenant_context with only booking_mode
            tenant_context = {"booking_mode": derived_domain}

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
    luma_response = None  # Initialize to None to handle exception cases

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

        # LOG 2 — LUMA OUTPUT (right after luma.resolve)
        luma_intent_obj = luma_response.get("intent", {})
        luma_intent = luma_intent_obj.get(
            "name", "") if isinstance(luma_intent_obj, dict) else ""
        luma_slots = luma_response.get("slots", {})
        luma_missing_slots = luma_response.get("missing_slots", [])
        turn_logger.info(
            json.dumps({
                "turn": "LUMA",
                "intent": luma_intent,
                "slots": luma_slots,
                "missing_slots": luma_missing_slots
            }, ensure_ascii=True, default=str)
        )

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
                missing_slots = set(raw_luma_slots.keys()) - \
                    set(effective_turn_slots.keys())
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
                    response_str = json.dumps(
                        luma_response, indent=2, default=str, ensure_ascii=False)
                    print(response_str)
                except Exception as e:
                    print(f"JSON serialization failed: {e}")
                    pprint.pprint(luma_response)
                print("=== END DEBUG_LUMA_WEEKDAY ===\n")

    except UpstreamError as e:
        logger.error(
            f"[LUMA_ERROR_FALLBACK] Luma API error for user {user_id}: {str(e)}")
        # SESSION LIFECYCLE RULE: For durable intents, reuse session state as-is on Luma error
        # Do not recompute slots or missing_slots - preserve session state exactly as-is
        if session_state:
            # CRITICAL: Check intent_name first, then fall back to intent
            session_intent_str = session_state.get("intent_name") or (
                session_state.get("intent") if isinstance(session_state.get("intent"), str) else
                (session_state.get("intent", {}).get("name", "")
                 if isinstance(session_state.get("intent"), dict) else "")
            )

            # Check if session intent is durable
            is_durable = False
            if session_intent_str:
                try:
                    is_durable = is_durable_intent(session_intent_str)
                except (ImportError, Exception) as e:
                    logger.warning(
                        f"[LUMA_ERROR_FALLBACK] Failed to check durable status: {e}")

            logger.error(
                f"[LUMA_ERROR_FALLBACK] session_intent={session_intent_str}, is_durable={is_durable}, "
                f"session_status={session_state.get('status')}, session_missing_slots={session_state.get('missing_slots', [])}"
            )

            # For durable intents, reuse session state as-is without recomputation
            if is_durable:
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}
                session_missing_slots = session_state.get("missing_slots", [])
                if not isinstance(session_missing_slots, list):
                    session_missing_slots = []
                session_stage = session_state.get("stage", "AVAILABILITY")
                session_action = session_state.get("action")
                # Derive action from stage if missing (for empty response recovery)
                if not session_action and session_stage == "AVAILABILITY":
                    session_action = "SEARCH_AVAILABILITY"
                elif not session_action and session_stage == "CONFIRM":
                    session_action = "CONFIRM_APPOINTMENT"
                session_status = session_state.get(
                    "status", "NEEDS_CLARIFICATION")
                # CRITICAL: Status must be NEEDS_CLARIFICATION if there are missing slots
                # Do NOT use session_status if it's READY but there are missing slots
                final_status = "NEEDS_CLARIFICATION" if session_missing_slots else session_status

                logger.error(
                    f"[LUMA_ERROR_FALLBACK] Final status={final_status} (session_status={session_status}, "
                    f"missing_slots={session_missing_slots})"
                )

                # Build outcome with plan and facts for consistency
                outcome = {
                    "intent_name": session_intent_str,
                    "stage": session_stage,
                    "action": session_action,
                    "slots": session_slots,
                    "missing_slots": session_missing_slots,
                    "status": final_status,
                    "plan": {
                        "intent": session_intent_str,
                        "stage": session_stage,
                        "action": session_action,
                        "missing_slots": session_missing_slots,
                        "slots": session_slots,
                        "status": final_status,
                        "executable_actions": [session_action] if session_action else []
                    },
                    "facts": {
                        "slots": session_slots,
                        "missing_slots": session_missing_slots
                    }
                }

                return {
                    "success": True,
                    "outcome": outcome
                }

            # For other intents, preserve session and return planning response (existing behavior)
            if session_state.get("status") == "NEEDS_CLARIFICATION":
                # Return planning response derived from session state
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}

                # Compute missing_slots from session intent and slots
                from core.planning.orchestration.missing_slots import compute_missing_slots
                missing_slots = compute_missing_slots(
                    session_intent_str, session_slots) if session_intent_str else []

                # Extract stage/action from session state if available, otherwise use defaults
                session_stage = session_state.get("stage", "AVAILABILITY")
                session_action = session_state.get("action")

                return {
                    "success": True,
                    "outcome": {
                        "intent_name": session_intent_str,
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

    # SESSION LIFECYCLE RULE: For durable intents, reuse session state as-is on empty/null Luma response
    # Do not recompute slots or missing_slots - preserve session state exactly as-is
    if not luma_response or not isinstance(luma_response, dict):
        logger.error(
            f"[LUMA_ERROR_FALLBACK] Luma returned None or invalid response for user {user_id}")
        if session_state:
            # CRITICAL: Check intent_name first, then fall back to intent
            session_intent_str = session_state.get("intent_name") or (
                session_state.get("intent") if isinstance(session_state.get("intent"), str) else
                (session_state.get("intent", {}).get("name", "")
                 if isinstance(session_state.get("intent"), dict) else "")
            )

            # Check if session intent is durable
            is_durable = False
            if session_intent_str:
                try:
                    is_durable = is_durable_intent(session_intent_str)
                except (ImportError, Exception) as e:
                    logger.warning(
                        f"[LUMA_ERROR_FALLBACK] Failed to check durable status: {e}")

            logger.error(
                f"[LUMA_ERROR_FALLBACK] session_intent={session_intent_str}, is_durable={is_durable}, "
                f"session_status={session_state.get('status')}, session_missing_slots={session_state.get('missing_slots', [])}"
            )

            # For durable intents, reuse session state as-is without recomputation
            if is_durable:
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}
                session_missing_slots = session_state.get("missing_slots", [])
                if not isinstance(session_missing_slots, list):
                    session_missing_slots = []
                session_stage = session_state.get("stage", "AVAILABILITY")
                session_action = session_state.get("action")
                # Derive action from stage if missing (for empty response recovery)
                if not session_action and session_stage == "AVAILABILITY":
                    session_action = "SEARCH_AVAILABILITY"
                elif not session_action and session_stage == "CONFIRM":
                    session_action = "CONFIRM_APPOINTMENT"
                session_status = session_state.get(
                    "status", "NEEDS_CLARIFICATION")
                # CRITICAL: Status must be NEEDS_CLARIFICATION if there are missing slots
                # Do NOT use session_status if it's READY but there are missing slots
                final_status = "NEEDS_CLARIFICATION" if session_missing_slots else session_status

                logger.error(
                    f"[LUMA_ERROR_FALLBACK] Final status={final_status} (session_status={session_status}, "
                    f"missing_slots={session_missing_slots})"
                )

                # Build outcome with plan and facts for consistency
                outcome = {
                    "intent_name": session_intent_str,
                    "stage": session_stage,
                    "action": session_action,
                    "slots": session_slots,
                    "missing_slots": session_missing_slots,
                    "status": final_status,
                    "plan": {
                        "intent": session_intent_str,
                        "stage": session_stage,
                        "action": session_action,
                        "missing_slots": session_missing_slots,
                        "slots": session_slots,
                        "status": final_status,
                        "executable_actions": [session_action] if session_action else []
                    },
                    "facts": {
                        "slots": session_slots,
                        "missing_slots": session_missing_slots
                    }
                }

                return {
                    "success": True,
                    "outcome": outcome
                }

            # For other intents, preserve session and return planning response (existing behavior)
            if session_state.get("status") == "NEEDS_CLARIFICATION":
                # Return planning response derived from session state
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}

                # Compute missing_slots from session intent and slots
                from core.planning.orchestration.missing_slots import compute_missing_slots
                missing_slots = compute_missing_slots(
                    session_intent_str, session_slots) if session_intent_str else []

                # Extract stage/action from session state if available, otherwise use defaults
                session_stage = session_state.get("stage", "AVAILABILITY")
                session_action = session_state.get("action")

                return {
                    "success": True,
                    "outcome": {
                        "intent_name": session_intent_str,
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
        logger.error(
            f"[LUMA_ERROR_FALLBACK] Contract violation for user {user_id}: {str(e)}")
        # SESSION LIFECYCLE RULE: For durable intents, reuse session state as-is on contract violation
        # Do not recompute slots or missing_slots - preserve session state exactly as-is
        if session_state:
            # CRITICAL: Check intent_name first, then fall back to intent
            session_intent_str = session_state.get("intent_name") or (
                session_state.get("intent") if isinstance(session_state.get("intent"), str) else
                (session_state.get("intent", {}).get("name", "")
                 if isinstance(session_state.get("intent"), dict) else "")
            )

            # Check if session intent is durable
            is_durable = False
            if session_intent_str:
                try:
                    is_durable = is_durable_intent(session_intent_str)
                except (ImportError, Exception) as e:
                    logger.warning(
                        f"[LUMA_ERROR_FALLBACK] Failed to check durable status: {e}")

            logger.error(
                f"[LUMA_ERROR_FALLBACK] session_intent={session_intent_str}, is_durable={is_durable}, "
                f"session_status={session_state.get('status')}, session_missing_slots={session_state.get('missing_slots', [])}"
            )

            # For durable intents, reuse session state as-is without recomputation
            if is_durable:
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}
                session_missing_slots = session_state.get("missing_slots", [])
                if not isinstance(session_missing_slots, list):
                    session_missing_slots = []
                session_stage = session_state.get("stage", "AVAILABILITY")
                session_action = session_state.get("action")
                # Derive action from stage if missing (for empty response recovery)
                if not session_action and session_stage == "AVAILABILITY":
                    session_action = "SEARCH_AVAILABILITY"
                elif not session_action and session_stage == "CONFIRM":
                    session_action = "CONFIRM_APPOINTMENT"
                session_status = session_state.get(
                    "status", "NEEDS_CLARIFICATION")
                # CRITICAL: Status must be NEEDS_CLARIFICATION if there are missing slots
                # Do NOT use session_status if it's READY but there are missing slots
                final_status = "NEEDS_CLARIFICATION" if session_missing_slots else session_status

                logger.error(
                    f"[LUMA_ERROR_FALLBACK] Final status={final_status} (session_status={session_status}, "
                    f"missing_slots={session_missing_slots})"
                )

                # Build outcome with plan and facts for consistency
                outcome = {
                    "intent_name": session_intent_str,
                    "stage": session_stage,
                    "action": session_action,
                    "slots": session_slots,
                    "missing_slots": session_missing_slots,
                    "status": final_status,
                    "plan": {
                        "intent": session_intent_str,
                        "stage": session_stage,
                        "action": session_action,
                        "missing_slots": session_missing_slots,
                        "slots": session_slots,
                        "status": final_status,
                        "executable_actions": [session_action] if session_action else []
                    },
                    "facts": {
                        "slots": session_slots,
                        "missing_slots": session_missing_slots
                    }
                }

                return {
                    "success": True,
                    "outcome": outcome
                }

            # For other intents, preserve session and return planning response (existing behavior)
            if session_state.get("status") == "NEEDS_CLARIFICATION":
                # Return planning response derived from session state
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}

                # Compute missing_slots from session intent and slots
                from core.planning.orchestration.missing_slots import compute_missing_slots
                missing_slots = compute_missing_slots(
                    session_intent_str, session_slots) if session_intent_str else []

                # Extract stage/action from session state if available, otherwise use defaults
                session_stage = session_state.get("stage", "AVAILABILITY")
                session_action = session_state.get("action")

                return {
                    "success": True,
                    "outcome": {
                        "intent_name": session_intent_str,
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

    # Guard: Ensure luma_response is valid before proceeding
    if not luma_response or not isinstance(luma_response, dict):
        logger.error(
            f"Invalid luma_response after error handling for user {user_id}: {luma_response}")
        return {
            "success": False,
            "error": "invalid_luma_response",
            "message": "Luma response is None or invalid"
        }

    # Extract Luma intent for logging (before intent resolution)
    luma_intent_obj = luma_response.get("intent", {})
    luma_intent_name = luma_intent_obj.get(
        "name", "") if isinstance(luma_intent_obj, dict) else ""

    # Resolve effective intent using new intent_resolution module
    from core.planning.orchestration.intent_resolution import resolve_effective_intent
    # CRITICAL: Preserve original session_state before intent resolution
    # This ensures session (with intent_name + slots) is available for merge_luma_with_session
    original_session_state = session_state
    effective_intent, session_reset_occurred = resolve_effective_intent(
        luma_response,
        session_state,
        user_id,
        transaction_id
    )

    # Track the source of session_reset_occurred
    session_reset_occurred_source = "resolve_effective_intent"
    logger.error(
        f"[SESSION_RESET_WRITER] RECEIVED_FROM_RESOLVER value={session_reset_occurred} "
        f"source={session_reset_occurred_source} "
        f"effective_intent={effective_intent} "
        f"user_id={user_id}{log_transaction_id}"
    )

    # DEBUG: Log result immediately after resolve_effective_intent returns
    logger.error(
        f"[ORCHESTRATOR] AFTER resolve_effective_intent: effective_intent={effective_intent} "
        f"session_reset_occurred={session_reset_occurred} "
        f"will_null_session_state={session_reset_occurred} "
        f"user_id={user_id}{log_transaction_id}"
    )

    # Update session_state if it was reset (for downstream logic)
    # BUT preserve original_session_state for merge decision - merge needs access to original session
    # CRITICAL: UNKNOWN → concrete intent transitions do NOT reset (intent materialization, not destructive switch)
    # Only true intent switches (e.g., CREATE_APPOINTMENT → DETAILS) reset the session
    if session_reset_occurred:
        # DEBUG: Log why session_state is being nulled
        import traceback
        # Last 2 frames before this one
        call_stack = ''.join(traceback.format_stack()[-3:-1])
        logger.error(
            f"[ORCHESTRATOR] NULLING session_state: reason=session_reset_occurred=True "
            f"effective_intent={effective_intent} "
            f"original_session_intent={original_session_state.get('intent_name') if original_session_state else None} "
            f"call_stack={call_stack} "
            f"user_id={user_id}{log_transaction_id}"
        )
        session_state = None

    # SINGLE SOURCE OF TRUTH: Ensure effective_intent is never None/empty when durable session intent exists
    # This is the authoritative intent that will be used throughout planning
    if not effective_intent or effective_intent == "UNKNOWN":
        # Fallback to durable session intent if available
        if session_state and not session_reset_occurred:
            session_intent = session_state.get(
                "intent_name") or session_state.get("intent")
            session_intent_str = session_intent if isinstance(session_intent, str) else (
                session_intent.get("name", "") if isinstance(session_intent, dict) else "")
            if session_intent_str:
                # Check if session intent is durable
                try:
                    from core.policy.intent_policy import get_intent_durable
                    if get_intent_durable(session_intent_str):
                        effective_intent = session_intent_str
                        logger.info(
                            f"[INTENT_PRESERVATION] Using durable session intent as effective_intent: {effective_intent} "
                            f"(luma returned UNKNOWN/empty, user_id={user_id}{log_transaction_id})"
                        )
                except (ImportError, Exception) as e:
                    logger.warning(
                        f"Failed to check durable status for '{session_intent_str}': {e}. "
                        f"Using session intent as fallback."
                    )
                    effective_intent = session_intent_str
                    logger.info(
                        f"[INTENT_PRESERVATION] Using session intent as effective_intent (durability check failed): {effective_intent} "
                        f"(user_id={user_id}{log_transaction_id})"
                    )

    # SHORT-CIRCUIT: Non-durable intents must NOT reach planning or persistence
    # Rule: Any intent with durable=false may be recognized, but must:
    # - STOP before planning (do not call build_decision_plan)
    # - NOT be persisted to session (do not update session.intent_name or slots)
    # - Preserve any existing durable session state
    # Only durable intents may reach build_decision_plan
    # CRITICAL: Check AFTER UNKNOWN recovery to ensure we catch non-durable intents even after recovery
    if effective_intent and effective_intent != "UNKNOWN":
        try:
            from core.policy.intent_policy import get_intent_durable
            is_durable = get_intent_durable(effective_intent)

            if not is_durable:
                # Non-durable intent detected - return informational response immediately
                # Do NOT proceed to planning, merge, or persistence
                # Do NOT treat this as a session reset - preserve existing durable session state
                logger.info(
                    f"[NON_DURABLE_INTENT] Short-circuiting planning for non-durable intent: {effective_intent} "
                    f"(user_id={user_id}{log_transaction_id})"
                )

                # Extract facts from Luma response for informational response
                facts_obj = luma_response.get("facts", {})
                from core.orchestration.luma_facts_adapter import facts_to_slots
                slots = facts_to_slots(
                    facts_obj,
                    intent_name=effective_intent,
                    source_text=text,
                ) if isinstance(facts_obj, dict) else {}

                # Also check for slots in nested facts.facts.slots or top-level slots
                if isinstance(facts_obj, dict) and "slots" in facts_obj:
                    nested_slots = facts_obj.get("slots", {})
                    if isinstance(nested_slots, dict):
                        slots.update(nested_slots)
                elif "slots" in luma_response:
                    top_level_slots = luma_response.get("slots", {})
                    if isinstance(top_level_slots, dict):
                        slots.update(top_level_slots)

                # Return informational response (similar to non-core intent handling)
                # Do NOT persist to session, do NOT plan, do NOT merge
                # Preserve existing durable session state (do not clear session)
                return {
                    "success": True,
                    "outcome": {
                        "status": "NON_DURABLE_INTENT",
                        "intent_name": effective_intent,
                        "slots": slots,
                        "missing_slots": [],  # Non-durable intents don't have required slots
                        "facts": {
                            "slots": slots,
                            "missing_slots": [],
                            "context": luma_response.get("context", {}),
                        }
                    }
                }
        except (ImportError, Exception) as e:
            # If durability check fails, log warning but continue (defensive)
            logger.warning(
                f"Failed to check durable status for '{effective_intent}': {e}. "
                f"Continuing with planning (assuming durable)."
            )

    # Construct effective_response: Copy luma_response and replace intent.name with effective_intent
    # FACT-ONLY CONTRACT: facts may be empty or partial - this is valid
    # Missing slots are NOT errors - planner will compute missing_slots from intent_planning.yaml
    # CRITICAL: This is the SINGLE SOURCE OF TRUTH for intent - no downstream component may recompute it
    effective_response = luma_response.copy()
    effective_response["intent"] = {"name": effective_intent}
    # CRITICAL: Set _effective_intent for process_luma_response to recover durable intents
    # This ensures UNKNOWN intents can be recovered from session when durable
    effective_response["_effective_intent"] = effective_intent or ""

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

    # Extract time_constraint from luma_response for CREATE_APPOINTMENT missing slot computation
    time_constraint = luma_response.get("time_constraint")
    if time_constraint:
        effective_response["time_constraint"] = time_constraint

    # Normalize service_id using tenant aliases (e.g., "suite" -> "room", "deluxe" -> "room")
    # CRITICAL: Preserve raw tenant value while storing canonical for planning
    if tenant_context and "aliases" in tenant_context and "service_id" in effective_response["slots"]:
        aliases = tenant_context["aliases"]
        raw_service_id = effective_response["slots"]["service_id"]
        if isinstance(raw_service_id, str) and raw_service_id.lower() in aliases:
            canonical_service_id = aliases[raw_service_id.lower()]
            logger.info(
                f"Normalized service_id: {raw_service_id} -> {canonical_service_id} (via tenant aliases)")
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

    # PHASE 1 INSTRUMENTATION: Log intent state BEFORE merge_luma_with_session and process_luma_response
    # CRITICAL: Use original_session_state for logging to show actual session state before any reset
    session_intent_for_trace = None
    session_status_for_trace = None
    if original_session_state:
        session_intent_for_trace = original_session_state.get(
            "intent_name") or original_session_state.get("intent")
        session_status_for_trace = original_session_state.get("status")
    logger.error(
        "[INTENT_TRACE_ORCHESTRATOR] BEFORE merge_luma_with_session: "
        f"effective_intent={effective_intent}, "
        f"effective_response['intent']['name']={effective_response.get('intent', {}).get('name', '')}, "
        f"session.intent_name={session_intent_for_trace}, "
        f"session.status={session_status_for_trace}, "
        f"session_reset_occurred={session_reset_occurred}, "
        f"user_id={user_id}{log_transaction_id}"
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
    # SESSION LIFECYCLE RULE: Merge for NEEDS_CLARIFICATION sessions OR READY sessions with durable intents
    # CRITICAL: Use original_session_state (preserved before reset) for merge decision and merge call
    # This ensures session (with intent_name + slots) is always available even if session_state was set to None
    session_for_merge = original_session_state if original_session_state else session_state
    session_status_for_merge = session_for_merge.get(
        "status") if session_for_merge else None
    # CRITICAL: Session stores intent_name, not intent. Check intent_name first, then fall back to intent.
    session_intent_for_merge = session_for_merge.get(
        "intent_name") if session_for_merge else None
    if not session_intent_for_merge and session_for_merge:
        # Fallback to intent (for backward compatibility)
        session_intent_for_merge = session_for_merge.get("intent")
    session_intent_str_for_merge = session_intent_for_merge if isinstance(session_intent_for_merge, str) else (
        session_intent_for_merge.get("name", "") if isinstance(session_intent_for_merge, dict) else "")

    # Check if session intent is durable
    is_session_intent_durable = False
    if session_intent_str_for_merge:
        try:
            is_session_intent_durable = is_durable_intent(
                session_intent_str_for_merge)
        except (ImportError, Exception) as e:
            logger.warning(
                f"Failed to check durable status for '{session_intent_str_for_merge}': {e}. "
                f"Assuming not durable for merge decision."
            )

    # SESSION MERGE GATING: Allow merge for:
    # 1. NEEDS_CLARIFICATION sessions (normal follow-up turns)
    # 2. READY sessions with durable intents (modification turns)
    # 3. UNKNOWN → concrete intent transitions (intent materialization, preserves pre-intent slots)
    #    Note: session_reset_occurred is False for UNKNOWN → concrete transitions (see intent_resolution.py)
    #    This ensures pre-intent slots (e.g., date from "tomorrow") are merged when intent materializes

    # DEBUG: Log merge decision inputs before computing should_merge_session
    session_for_merge_intent = session_for_merge.get(
        "intent_name") if session_for_merge else None
    session_for_merge_slots = list(session_for_merge.get(
        "slots", {}).keys()) if session_for_merge else []

    # CRITICAL: Final check - who last set session_reset_occurred?
    logger.error(
        f"[SESSION_RESET_WRITER] FINAL_CHECK_BEFORE_MERGE: "
        f"session_reset_occurred={session_reset_occurred} "
        f"source={session_reset_occurred_source} "
        f"session_for_merge_exists={session_for_merge is not None} "
        f"session_for_merge_intent={session_for_merge_intent} "
        f"session_for_merge_slots={session_for_merge_slots} "
        f"session_status_for_merge={session_status_for_merge} "
        f"will_block_merge={session_reset_occurred} "
        f"user_id={user_id}{log_transaction_id}"
    )

    logger.error(
        f"[ORCHESTRATOR] BEFORE should_merge_session: session_reset_occurred={session_reset_occurred} "
        f"session_for_merge_intent={session_for_merge_intent} "
        f"session_for_merge_slots={session_for_merge_slots} "
        f"session_status_for_merge={session_status_for_merge} "
        f"is_session_intent_durable={is_session_intent_durable} "
        f"user_id={user_id}{log_transaction_id}"
    )

    should_merge_session = (
        session_for_merge and not session_reset_occurred and (
            session_status_for_merge == "NEEDS_CLARIFICATION" or
            (session_status_for_merge == "READY" and is_session_intent_durable)
        )
    )

    # DEBUG: Log final merge decision
    logger.error(
        f"[ORCHESTRATOR] should_merge_session={should_merge_session} "
        f"user_id={user_id}{log_transaction_id}"
    )

    if should_merge_session:
        logger.info(
            f"[SESSION_MERGE] Merging session slots: status={session_status_for_merge}, intent={session_intent_str_for_merge}, "
            f"session_slots={list(session_for_merge.get('slots', {}).keys())}"
        )
        prior_intent = session_for_merge.get("intent")
        prior_missing = session_for_merge.get("missing_slots", [])
        prior_slots = list(session_for_merge.get("slots", {}).keys())

        effective_response = merge_luma_with_session(
            effective_response, session_for_merge, planning_only=planning_only)

        # AFTER_MERGE: Log right after session merge
        effective_collected_slots = effective_response.get(
            "_effective_collected_slots", {})
        after_merge_log = {
            "trace": "AFTER_MERGE",
            "intent": effective_response.get("intent"),
            "slots": effective_response.get("slots"),
            "effective_collected_slots": effective_collected_slots,
            "modification_context": effective_response.get("_modification_context")
        }
    else:
        # No session merge (first turn or session reset or READY non-CREATE_APPOINTMENT)
        if original_session_state:
            logger.info(
                f"[SESSION_MERGE] Skipping merge: status={session_status_for_merge}, intent={session_intent_str_for_merge}, "
                f"session_reset_occurred={session_reset_occurred}"
            )
        # No session (first turn) - still need to compute effective_collected_slots
        # This ensures slots are persisted correctly on the first turn
        if effective_response and isinstance(effective_response, dict):
            effective_response = _compute_effective_collected_slots(
                effective_response, planning_only=planning_only)

            # AFTER_MERGE: Log right after computing effective_collected_slots (first turn)
            effective_collected_slots = effective_response.get(
                "_effective_collected_slots", {})
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
                from core.planning.orchestration.missing_slots import compute_missing_slots
                intent_name = effective_response.get(
                    "intent", {}).get("name", "")
                slots = effective_response.get("slots", {})
                time_constraint = effective_response.get("time_constraint")
                if intent_name:
                    effective_response["missing_slots"] = compute_missing_slots(
                        intent_name, slots, time_constraint=time_constraint
                    )
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
    effective_intent_name = effective_response.get(
        "intent", {}).get("name", "")

    logger.info(
        f"session_merged user_id={user_id}{log_transaction_id} "
        f"prior_intent={prior_intent} luma_intent={luma_intent_name} effective_intent={effective_intent_name} "
        f"prior_missing_slots={prior_missing} extracted_slots={extracted_slots} remaining_missing_slots={remaining_missing}"
    )

    # Verify intent before processing
    # Guard: effective_response must be a dict
    if not effective_response or not isinstance(effective_response, dict):
        logger.error(
            f"effective_response is None or not a dict: {effective_response}")
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
        final_intent_check = effective_response.get(
            "intent", {}).get("name", "")

    logger.info(
        f"calling_process_luma_response user_id={user_id}{log_transaction_id} "
        f"intent={final_intent_check}"
    )

    # HARD INVARIANT: Planning must NEVER run with intent=UNKNOWN or empty when durable session intent exists
    # This ensures durable intents are preserved even if intent resolution failed or was overwritten
    planning_intent = effective_response.get("intent", {}).get("name", "")
    effective_intent_for_planning = effective_response.get(
        "_effective_intent", "")

    # If planning intent is UNKNOWN/empty but effective_intent exists, use effective_intent
    if (not planning_intent or planning_intent == "UNKNOWN") and effective_intent_for_planning:
        planning_intent = effective_intent_for_planning
        effective_response["intent"] = {"name": planning_intent}
        logger.info(
            f"[INTENT_PRESERVATION] Recovered intent for planning: {planning_intent} "
            f"(was UNKNOWN/empty, user_id={user_id}{log_transaction_id})"
        )

    # If still UNKNOWN/empty and session has durable intent, recover from session
    if (not planning_intent or planning_intent == "UNKNOWN") and session_state and not session_reset_occurred:
        session_intent = session_state.get("intent")
        session_intent_str = session_intent if isinstance(session_intent, str) else (
            session_intent.get("name", "") if isinstance(session_intent, dict) else "")
        if session_intent_str:
            # Check if session intent is durable
            try:
                from core.policy.intent_policy import get_intent_durable
                if get_intent_durable(session_intent_str):
                    planning_intent = session_intent_str
                    effective_response["intent"] = {"name": planning_intent}
                    effective_response["_effective_intent"] = planning_intent
                    logger.info(
                        f"[INTENT_PRESERVATION] Recovered durable intent from session for planning: {planning_intent} "
                        f"(was UNKNOWN/empty, user_id={user_id}{log_transaction_id})"
                    )
            except (ImportError, Exception) as e:
                logger.warning(
                    f"Failed to check durable status for '{session_intent_str}': {e}. "
                    f"Using session intent as fallback."
                )
                planning_intent = session_intent_str
                effective_response["intent"] = {"name": planning_intent}
                effective_response["_effective_intent"] = planning_intent
                logger.info(
                    f"[INTENT_PRESERVATION] Recovered intent from session (durability check failed): {planning_intent} "
                    f"(user_id={user_id}{log_transaction_id})"
                )

    # SAFETY ASSERTION: Planning must NEVER run with invalid intent when durable session intent exists
    if (not planning_intent or planning_intent == "UNKNOWN") and session_state and not session_reset_occurred:
        session_intent = session_state.get("intent")
        session_intent_str = session_intent if isinstance(session_intent, str) else (
            session_intent.get("name", "") if isinstance(session_intent, dict) else "")
        if session_intent_str:
            try:
                from core.policy.intent_policy import get_intent_durable
                if get_intent_durable(session_intent_str):
                    raise AssertionError(
                        f"Planning invoked without a valid intent when durable session intent exists. "
                        f"planning_intent={planning_intent}, session_intent={session_intent_str}, "
                        f"user_id={user_id}{log_transaction_id}"
                    )
            except (ImportError, Exception):
                # If durability check fails, skip assertion (don't fail on import errors)
                pass

    # Update final_intent_check for logging
    final_intent_check = effective_response.get("intent", {}).get("name", "")

    # PHASE 1 INSTRUMENTATION: Log intent state BEFORE process_luma_response
    session_intent_for_trace = None
    session_status_for_trace = None
    if session_state:
        session_intent_for_trace = session_state.get(
            "intent_name") or session_state.get("intent")
        session_status_for_trace = session_state.get("status")
    logger.error(
        "[INTENT_TRACE_ORCHESTRATOR] BEFORE process_luma_response: "
        f"effective_response['intent']['name']={final_intent_check}, "
        f"effective_response['_effective_intent']={effective_response.get('_effective_intent', '')}, "
        f"session.intent_name={session_intent_for_trace}, "
        f"session.status={session_status_for_trace}, "
        f"user_id={user_id}{log_transaction_id}"
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
    raw_luma_facts = raw_luma_response.get(
        "facts", {}) if isinstance(raw_luma_response, dict) else {}
    raw_luma_intent = raw_luma_response.get(
        "intent", {}) if isinstance(raw_luma_response, dict) else {}
    raw_luma_intent_name = raw_luma_intent.get(
        "name", "") if isinstance(raw_luma_intent, dict) else ""
    source_text = effective_response.get("_source_text", text)
    # LUMA_RAW: Log raw Luma response (facts, intent, source_text)
    raw_luma_response = effective_response.get("_raw_luma_response", {})
    raw_luma_facts = raw_luma_response.get(
        "facts", {}) if isinstance(raw_luma_response, dict) else {}
    raw_luma_intent = raw_luma_response.get(
        "intent", {}) if isinstance(raw_luma_response, dict) else {}
    raw_luma_intent_name = raw_luma_intent.get(
        "name", "") if isinstance(raw_luma_intent, dict) else ""
    source_text = effective_response.get("_source_text", text)
    logger.info("[LUMA_RAW] user_id=%s facts=%s intent=%s source_text=%s",
                user_id, json.dumps(
                    raw_luma_facts, default=str, ensure_ascii=True),
                raw_luma_intent_name, source_text)

    decision = process_luma_response(
        effective_response, derived_domain, user_id)

    # Guard: decision must be a dict
    # CRITICAL: Missing slots are NEVER an error - always return a planning response
    if not decision or not isinstance(decision, dict):
        logger.error(
            f"process_luma_response returned None or not a dict: {decision}")
        # Even on error, return a minimal planning response (not an error)
        # This ensures planning always proceeds, even if process_luma_response fails
        intent_name = effective_response.get("intent", {}).get("name", "")
        slots = effective_response.get("slots", {})
        missing_slots = effective_response.get("missing_slots", [])

        # Build minimal plan with correct action selection
        # CRITICAL: Apply action selection rules even in fallback case
        # PLANNING POLICY: CREATE_APPOINTMENT ALWAYS goes through AVAILABILITY first
        if intent_name == "CREATE_APPOINTMENT":
            stage = "AVAILABILITY"
            action = "SEARCH_AVAILABILITY"
        elif missing_slots:
            stage = "AVAILABILITY"
            action = "SEARCH_AVAILABILITY"
        else:
            stage = "CONFIRM"
            # Apply action selection rules for complete slots
            if intent_name == "CREATE_RESERVATION":
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
        logger.warning(
            f"Created minimal decision due to process_luma_response failure: intent={intent_name}, missing_slots={missing_slots}")

    # Extract decision plan
    plan = decision.get("plan", {})
    plan_status = plan.get("status", "READY")
    allowed_actions = plan.get("allowed_actions", [])
    blocked_actions = plan.get("blocked_actions", [])
    awaiting = plan.get("awaiting")

    # PLANNING POLICY: CREATE_APPOINTMENT ALWAYS requires AVAILABILITY stage first
    # Even when exact date/time is provided, availability must be checked before confirmation
    intent_name = decision.get("intent_name", "")
    if intent_name == "CREATE_APPOINTMENT":
        # Override plan stage/action to force AVAILABILITY
        plan["stage"] = "AVAILABILITY"
        plan["action"] = "SEARCH_AVAILABILITY"

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
            has_date_range = isinstance(slots.get("date_range"), dict) and bool(
                slots.get("date_range", {}).get("start"))
            has_datetime_range = isinstance(slots.get("datetime_range"), dict) and bool(
                slots.get("datetime_range", {}).get("start"))

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
                logger.debug(
                    f"Set has_datetime=true in facts.slots (planning invariant: READY with temporal info)")

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
            raw_luma_response = effective_response.get(
                "_raw_luma_response", {})
            if isinstance(raw_luma_response, dict):
                raw_luma_facts = raw_luma_response.get("facts", {})
                if isinstance(raw_luma_facts, dict) and raw_luma_facts:
                    # Start with normalized slots (has time, date, etc. from normalization)
                    raw_slots = slots.copy() if isinstance(slots, dict) else {}

                    # Override service_id with raw fact value (tests expect raw, not normalized alias)
                    if "service_id" in raw_luma_facts:
                        raw_slots["service_id"] = raw_luma_facts["service_id"]
                    elif isinstance(effective_response.get("facts"), dict):
                        raw_facts_in_effective = effective_response.get(
                            "facts", {}).get("service_id")
                        if raw_facts_in_effective:
                            raw_slots["service_id"] = raw_facts_in_effective

                    slots = raw_slots

        # CRITICAL: Always populate plan object with all required fields
        # This ensures plan.stage and plan.action are always present (no silent failures)
        # HARD RULE: Include both intent and intent_name for session persistence
        populated_plan = {
            "intent": intent_name,
            # For session persistence - build_session_state_from_outcome reads plan.intent_name
            "intent_name": intent_name,
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
            logger.debug(
                f"Using raw service_id in outcome: {raw_service_id_for_outcome}")
        elif "service_id" in outcome_slots:
            # Keep existing service_id if no raw found (shouldn't happen but fail-safe)
            logger.debug(
                f"Using existing service_id in outcome: {outcome_slots.get('service_id')}")

        # Remove canonical from outcome slots (never expose canonical to tests/dialog)
        if "_canonical_service_id" in outcome_slots:
            del outcome_slots["_canonical_service_id"]
            logger.debug(
                f"Removed _canonical_service_id from outcome, using raw service_id: {outcome_slots.get('service_id')}")

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

        # ASSERTION: plan.intent_name must never be empty after successful planning
        if not populated_plan.get("intent_name") or populated_plan.get("intent_name") == "":
            logger.error(
                "[PLANNING_ASSERTION] CRITICAL: plan.intent_name is empty after planning! "
                "intent_name=%r, plan=%s, outcome.intent_name=%s",
                intent_name,
                json.dumps(populated_plan, default=str, ensure_ascii=True),
                result.get("outcome", {}).get("intent_name")
            )
            # Fail-safe: Use intent_name from variable if plan doesn't have it
            if intent_name and intent_name not in ("", "UNKNOWN"):
                populated_plan["intent_name"] = intent_name
                result["outcome"]["intent_name"] = intent_name
                result["outcome"]["plan"]["intent_name"] = intent_name
                logger.error(
                    "[PLANNING_ASSERTION] Recovered: Set plan.intent_name=%s from resolved intent_name",
                    intent_name
                )
            else:
                logger.error(
                    "[PLANNING_ASSERTION] FAILED: Cannot recover - intent_name is empty/invalid: %r",
                    intent_name
                )
        else:
            # Log success to confirm intent_name is present
            logger.debug(
                "[PLANNING_ASSERTION] SUCCESS: plan.intent_name=%s is present after planning",
                populated_plan.get("intent_name")
            )

        # PLANNING INVARIANT: Ephemeral intents must NOT leak into planning
        plan_intent_name = populated_plan.get("intent_name")
        if plan_intent_name and plan_intent_name not in ("", "UNKNOWN"):
            if not is_durable_intent(plan_intent_name):
                raise AssertionError(
                    f"Ephemeral intent '{plan_intent_name}' leaked into planning. "
                    f"Only durable intents may be persisted. "
                    f"Add '{plan_intent_name}' to DURABLE_INTENTS if it should be persistent."
                )

        # OUTCOME: Log final outcome structure
        outcome = result.get("outcome", {})
        outcome_slots = outcome.get("slots", {})
        outcome_missing_slots = outcome.get("missing_slots", [])
        logger.info("[OUTCOME] user_id=%s intent=%s stage=%s action=%s missing_slots=%s slots=%s",
                    user_id, intent_name, outcome.get(
                        "stage"), outcome.get("action"),
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

                # CRITICAL: Ensure intent_name is set for NEEDS_CLARIFICATION
                # NEEDS_CLARIFICATION must NEVER clear intent - preserve it for follow-up turns
                if "intent_name" not in result["outcome"] or not result["outcome"].get("intent_name"):
                    # Try to get intent_name from decision or effective_response
                    resolved_intent = decision.get("intent_name", "")
                    if not resolved_intent and effective_response:
                        resolved_intent = effective_response.get(
                            "_effective_intent")
                    if not resolved_intent and effective_response:
                        intent_obj = effective_response.get("intent", {})
                        if isinstance(intent_obj, dict):
                            resolved_intent = intent_obj.get("name", "")
                            # Skip UNKNOWN - use effective_intent instead
                            if resolved_intent == "UNKNOWN":
                                resolved_intent = effective_response.get(
                                    "_effective_intent", "")
                    # If still empty, preserve session intent
                    if not resolved_intent and session_state:
                        resolved_intent = session_state.get(
                            "intent_name") or session_state.get("intent")

                    if resolved_intent:
                        result["outcome"]["intent_name"] = resolved_intent
                        logger.info(
                            f"[NEEDS_CLARIFICATION] Set intent_name={resolved_intent} in outcome "
                            f"(from decision/effective_response/session)"
                        )
                    else:
                        logger.warning(
                            f"[NEEDS_CLARIFICATION] No intent available to set in outcome"
                        )
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
        # CRITICAL: Preserve effective_intent for NEEDS_CLARIFICATION (do NOT clear intent)
        # Intent is only cleared on terminal flows (CANCEL, COMPLETE), never on NEEDS_CLARIFICATION
        intent_name = decision.get("intent_name", "")
        # Fallback to effective_intent if decision.intent_name is empty (preserves session intent)
        if not intent_name and effective_response:
            effective_intent = effective_response.get("_effective_intent")
            if effective_intent:
                intent_name = effective_intent
                logger.info(
                    f"[NEEDS_CLARIFICATION] Preserved effective_intent={effective_intent} "
                    f"(decision.intent_name was empty)"
                )
        # If still empty, try to get from effective_response.intent.name
        if not intent_name and effective_response:
            intent_obj = effective_response.get("intent", {})
            if isinstance(intent_obj, dict):
                intent_from_response = intent_obj.get("name", "")
                if intent_from_response and intent_from_response != "UNKNOWN":
                    intent_name = intent_from_response
                    logger.info(
                        f"[NEEDS_CLARIFICATION] Using intent from effective_response: {intent_name}"
                    )
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
                # Use facts missing_slots (even if [])
                missing_slots = facts_missing

        # If not in facts, try effective_response
        if missing_slots is None and "missing_slots" in effective_response:
            response_missing = effective_response.get("missing_slots")
            if isinstance(response_missing, list):
                # Use response missing_slots (even if [])
                missing_slots = response_missing

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
        missing_slots = _normalize_modify_booking_missing_slots(
            missing_slots, effective_response)

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
        clarification_reason = _derive_clarification_reason_from_missing_slots(
            missing_slots)

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

        # CRITICAL: Always set intent_name in outcome for NEEDS_CLARIFICATION
        # NEEDS_CLARIFICATION must NEVER clear intent - preserve it for follow-up turns
        # If resolved_intent exists, use it; otherwise preserve existing session intent
        if "outcome" in result:
            if intent_name:
                # Use resolved intent (from decision or effective_response)
                result["outcome"]["intent_name"] = intent_name
                logger.info(
                    f"[NEEDS_CLARIFICATION] Set intent_name={intent_name} in outcome"
                )
            else:
                # No resolved intent - preserve session intent if available
                if session_state:
                    session_intent = session_state.get(
                        "intent_name") or session_state.get("intent")
                    if session_intent:
                        result["outcome"]["intent_name"] = session_intent
                        logger.info(
                            f"[NEEDS_CLARIFICATION] Preserved session intent_name={session_intent} "
                            f"(no resolved intent available)"
                        )
                    else:
                        # No session intent either - log warning but don't set empty string
                        logger.warning(
                            f"[NEEDS_CLARIFICATION] No intent available (decision or session) - "
                            f"intent_name will not be set in outcome"
                        )

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
                # CRITICAL: Plan must be in outcome (tests check outcome.plan.stage/action)
                "plan": plan
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
            (isinstance(slots_dict.get("datetime_range"), dict)
             and slots_dict["datetime_range"].get("start"))
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
                    date_str = str(date_range.get("start")
                                   or date_range.get("start_date"))
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
                            minute = int(time_parts[1]) if len(
                                time_parts) > 1 else 0

                            # Handle AM/PM
                            if "pm" in time_str.lower() and hour < 12:
                                hour += 12
                            elif "am" in time_str.lower() and hour == 12:
                                hour = 0

                            # Combine date and time
                            start_datetime = date_obj.replace(
                                hour=hour, minute=minute, second=0, microsecond=0)
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
                    fallback_date = str(date_range.get(
                        "start") or date_range.get("start_date"))
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
        logger.info(
            "Set has_datetime=true in facts.slots (date and time both present)")

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
                resolution = _resolve_service_id(
                    booking_services, catalog_services_for_resolution)
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
                        # Include all accumulated slots (service_id, date, time, etc.)
                        **slots,
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


def plan_message(
    text: str,
    user_id: str,
    session_state: Optional[Dict[str, Any]] = None,
    luma_client: Optional[LumaClient] = None,
    organization_client: Optional[OrganizationClient] = None,
    frozen_time: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Extract planning result from handle_message without triggering execution logic.

    This is a thin wrapper around handle_message with planning_only=True that returns
    a clean, structured planning result suitable for E2E testing.

    Args:
        text: User message text
        user_id: User identifier
        session_state: Optional session state for follow-up handling
        luma_client: Optional Luma client instance (creates default if None)
        organization_client: Optional organization client instance (creates default if None)
        frozen_time: Optional frozen time for testing (reserved for future time mocking support)

    Returns:
        Dictionary with planning result containing:
        - intent_name: Intent name
        - stage: Planning stage (e.g., "AVAILABILITY", "CONFIRM")
        - action: Action to execute (e.g., "SEARCH_AVAILABILITY", "CONFIRM_APPOINTMENT")
        - slots: Collected slots dictionary
        - missing_slots: List of missing required slots
        - time_constraint: Optional time constraint from Luma response (if present)
        - status: Planning status (READY, NEEDS_CLARIFICATION, AWAITING_CONFIRMATION)

    Raises:
        Any exceptions from handle_message are propagated.
    """
    # Call handle_message_legacy with planning_only=True to avoid execution logic
    result = handle_message_legacy(
        user_id=user_id,
        text=text,
        session_state=session_state,
        luma_client=luma_client,
        organization_client=organization_client,
        planning_only=True
    )

    # Extract planning result from outcome
    if not result.get("success", False):
        # Propagate errors as-is
        return result

    outcome = result.get("outcome", {})

    # Extract required fields from outcome
    planning_result = {
        "intent_name": outcome.get("intent_name", ""),
        "stage": outcome.get("stage"),
        "action": outcome.get("action"),
        "slots": outcome.get("slots", {}),
        "missing_slots": outcome.get("missing_slots", []),
        "status": outcome.get("status")
    }

    # Extract time_constraint from multiple possible sources
    # Priority: 1) effective_response (merged_luma_response), 2) raw_luma_response, 3) outcome.facts.context
    time_constraint = None
    merged_luma_response = result.get("_merged_luma_response", {})
    if isinstance(merged_luma_response, dict):
        # Check effective_response first (time_constraint is stored here during processing)
        time_constraint = merged_luma_response.get("time_constraint")

        # If not found, check raw_luma_response within effective_response
        if time_constraint is None:
            raw_luma_response = merged_luma_response.get(
                "_raw_luma_response", {})
            if isinstance(raw_luma_response, dict):
                time_constraint = raw_luma_response.get("time_constraint")

    # Fallback: Check outcome.facts.context
    if time_constraint is None:
        facts = outcome.get("facts", {})
        if isinstance(facts, dict):
            context = facts.get("context", {})
            if isinstance(context, dict):
                time_constraint = context.get("time_constraint")

    # Add time_constraint if present
    if time_constraint is not None:
        planning_result["time_constraint"] = time_constraint

    return planning_result
