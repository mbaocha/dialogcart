"""
Turn planner — single-turn NLU merge, planning, and outcome construction.

Invoked via plan_message() in planning_service.py (planning_only=True).
"""

import copy
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.orchestration.cache.catalog_cache import catalog_cache
from core.orchestration.cache.org_domain_cache import org_domain_cache
from core.orchestration.clients.catalog_client import CatalogClient
from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.errors import (
    ContractViolation,
    UnsupportedIntentError,
    UpstreamError,
)
from core.orchestration.nlu import (
    LumaClient,
    assert_luma_contract,
    build_clarify_outcome_from_reason,
    process_luma_response,
)
from core.orchestration.nlu.conversation_memory import update_conversation
from core.orchestration.persistence.durable_intents import is_durable_intent

logger = logging.getLogger(__name__)
turn_logger = logging.getLogger("core.turn_log")


def plan_turn(
    user_id: str,
    text: str,
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
    planning_only: bool = False,
) -> Dict[str, Any]:
    """Run one planning turn (NLU through plan to outcome).

    Domain is derived exclusively from organization_id (via org domain cache);
    callers must not pass a domain argument.
    """
    # Phase 1: import from neutral modules — breaks the circular dependency
    # with orchestrator.py (turn_planner no longer imports from orchestrator).
    from core.engine.outcome_builder import _build_planning_outcome, build_outcome_from_decision
    from core.config.org_resolver import _get_org_id_from_env
    from core.rendering.response_renderer import _inject_rendering_text, _inject_system_text

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
            from core.orchestration.clients.customer_client import CustomerClient
            from core.orchestration.execution.clients.booking_client import (
                BookingClient,
            )

            booking_client = BookingClient()
            customer_client = CustomerClient()
        except (ImportError, AttributeError):
            # Execution clients may not be available in planning-only builds
            # This is fine - they won't be used in planning-only mode anyway
            pass

    # TODO: Replace env-based organization_id with channel/auth-derived organization_id
    resolved_org_id = (
        organization_id if organization_id is not None else _get_org_id_from_env()
    )

    # Derive domain from organization details (cached, long TTL)
    derived_domain, _ = org_domain_cache.get_domain(
        resolved_org_id, organization_client, force_refresh=False
    )

    # Step 1: Prepare tenant_context aliases (service/reservation)
    catalog_data_for_alias: Optional[Dict[str, Any]] = None
    tenant_context = None
    if derived_domain in ("service", "reservation"):
        catalog_data_for_alias = catalog_cache.get_catalog(
            resolved_org_id, catalog_client, domain=derived_domain
        )
        alias_map: Dict[str, Any] = {}
        if derived_domain == "service":
            services_for_alias = (
                catalog_data_for_alias.get("services", [])
                if isinstance(catalog_data_for_alias, dict)
                else []
            )
            for svc in services_for_alias:
                if not isinstance(svc, dict) or svc.get("is_active") is False:
                    continue
                name = svc.get("name")
                if not name:
                    continue
                item_id = svc.get("id")
                if item_id is not None:
                    try:
                        alias_map[name.lower()] = int(item_id)
                        continue
                    except (TypeError, ValueError):
                        pass
                canonical_key = (
                    svc.get("service_family_id")
                    or svc.get("canonical")
                    or svc.get("slug")
                    or name.lower().replace(" ", "_")
                )
                if not canonical_key:
                    continue
                # Construct full canonical path if it's a short form (no dot)
                # Luma expects format: "category.family_id" (e.g., "beauty_and_wellness.haircut")
                if "." not in str(canonical_key):
                    # Short form canonical - prefix with category for service domain
                    canonical_key = f"beauty_and_wellness.{canonical_key}"
                alias_map[name.lower()] = canonical_key
        else:
            rooms_for_alias = (
                catalog_data_for_alias.get("rooms", [])
                if isinstance(catalog_data_for_alias, dict)
                else []
            )
            for rt in rooms_for_alias:
                if not isinstance(rt, dict) or rt.get("is_active") is False:
                    continue
                name = rt.get("name")
                if not name:
                    continue
                canonical_key = (
                    rt.get("canonical_key")
                    or rt.get("canonical")
                    or rt.get("slug")
                    or name.lower().replace(" ", "_")
                )
                if not canonical_key:
                    continue
                alias_map[name.lower()] = canonical_key
            extras_for_alias = (
                catalog_data_for_alias.get("extras", [])
                if isinstance(catalog_data_for_alias, dict)
                else []
            )
            for ex in extras_for_alias:
                if not isinstance(ex, dict) or ex.get("is_active") is False:
                    continue
                name = ex.get("name")
                if not name:
                    continue
                canonical_key = (
                    ex.get("canonical")
                    or ex.get("slug")
                    or name.lower().replace(" ", "_")
                )
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
    # Build conversation context from session memory for follow-up resolution
    from core.orchestration.nlu.conversation_memory import (
        build_conversation_context,
        update_conversation,
    )
    conversation_context = build_conversation_context(session_state)

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
    logger.info(
        "Luma request payload: %s", json.dumps(
            luma_payload, ensure_ascii=False)
    )

    # Store raw response for attachment to effective_response (must be accessible after try block)
    raw_luma_response_deep_copy = None
    luma_response = None  # Initialize to None to handle exception cases

    try:
        from core.tracing.decision_trace import measure_stage
    except ImportError:
        from contextlib import contextmanager

        @contextmanager
        def measure_stage(_stage: str):  # type: ignore[misc]
            yield

    try:
        with measure_stage("luma"):
            luma_response = luma_client.resolve(
                user_id=user_id,
                text=text,
                domain=derived_domain,
                timezone=timezone,
                tenant_context=tenant_context,
                conversation_context=conversation_context,
            )

        # Store raw response for attachment to effective_response (must be accessible after try block)
        raw_luma_response_deep_copy = copy.deepcopy(luma_response)

        # LOG 2 — LUMA OUTPUT (right after luma.resolve)
        luma_intent_obj = luma_response.get("intent", {})
        luma_intent = (
            luma_intent_obj.get("name", "") if isinstance(
                luma_intent_obj, dict) else ""
        )
        luma_slots = luma_response.get("slots", {})
        luma_missing_slots = luma_response.get("missing_slots", [])
        try:
            from core.tracing.decision_trace import is_decision_trace_enabled

            _trace_on = is_decision_trace_enabled()
        except ImportError:
            _trace_on = False
        if not _trace_on:
            turn_logger.info(
                json.dumps(
                    {
                        "turn": "LUMA",
                        "intent": luma_intent,
                        "slots": luma_slots,
                        "missing_slots": luma_missing_slots,
                    },
                    ensure_ascii=True,
                    default=str,
                )
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

        if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("DEBUG_SLOT_MERGE") == "1":
            if raw_luma_slots:
                missing_slots = set(raw_luma_slots.keys()) - set(
                    effective_turn_slots.keys()
                )
                if missing_slots:
                    error_msg = (
                        f"INVARIANT VIOLATION: raw_luma_response.slots contains slots that are missing from effective_turn_slots! "
                        f"user_id={user_id}, missing_slots={list(missing_slots)}, "
                        f"raw_luma_slots={list(raw_luma_slots.keys())}, "
                        f"session_slots={list(session_slots_for_merge.keys())}, "
                        f"effective_turn_slots={list(effective_turn_slots.keys())}"
                    )
                    logger.debug(f"[EFFECTIVE_TURN_SLOTS] {error_msg}")
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
            weekday_keywords = [
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
                "next monday",
                "next tuesday",
                "next wednesday",
                "next thursday",
                "next friday",
                "next saturday",
                "next sunday",
            ]
            if any(w in text_l for w in weekday_keywords):
                try:
                    response_str = json.dumps(
                        luma_response, indent=2, default=str, ensure_ascii=False)
                except Exception:
                    response_str = repr(luma_response)
                logger.debug(
                    "[DEBUG_LUMA_WEEKDAY] text=%r response=%s", text, response_str)

    except UpstreamError as e:
        logger.error(
            f"[LUMA_ERROR_FALLBACK] Luma API error for user {user_id}: {str(e)}"
        )
        # SESSION LIFECYCLE RULE: For durable intents, reuse session state as-is on Luma error
        # Do not recompute slots or missing_slots - preserve session state exactly as-is
        if session_state:
            # CRITICAL: Check intent_name first, then fall back to intent
            session_intent_str = session_state.get("intent_name") or (
                session_state.get("intent")
                if isinstance(session_state.get("intent"), str)
                else (
                    session_state.get("intent", {}).get("name", "")
                    if isinstance(session_state.get("intent"), dict)
                    else ""
                )
            )

            # Check if session intent is durable
            is_durable = False
            if session_intent_str:
                try:
                    is_durable = is_durable_intent(session_intent_str)
                except (ImportError, Exception) as e:
                    logger.warning(
                        f"[LUMA_ERROR_FALLBACK] Failed to check durable status: {e}"
                    )

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
                final_status = (
                    "NEEDS_CLARIFICATION" if session_missing_slots else session_status
                )

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
                        "executable_actions": (
                            [session_action] if session_action else []
                        ),
                    },
                    "facts": {
                        "slots": session_slots,
                        "missing_slots": session_missing_slots,
                    },
                }

                return {"success": True, "outcome": outcome}

            # For other intents, preserve session and return planning response (existing behavior)
            if session_state.get("status") == "NEEDS_CLARIFICATION":
                # Return planning response derived from session state
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}

                # Compute missing_slots from session intent and slots
                from core.planning.orchestration.missing_slots import (
                    compute_missing_slots,
                )

                missing_slots = (
                    compute_missing_slots(session_intent_str, session_slots)
                    if session_intent_str
                    else []
                )

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
                        "status": "NEEDS_CLARIFICATION" if missing_slots else "READY",
                    },
                }
        return {"success": False, "error": "upstream_error", "message": str(e)}

    # SESSION LIFECYCLE RULE: For durable intents, reuse session state as-is on empty/null Luma response
    # Do not recompute slots or missing_slots - preserve session state exactly as-is
    if not luma_response or not isinstance(luma_response, dict):
        logger.error(
            f"[LUMA_ERROR_FALLBACK] Luma returned None or invalid response for user {user_id}"
        )
        if session_state:
            # CRITICAL: Check intent_name first, then fall back to intent
            session_intent_str = session_state.get("intent_name") or (
                session_state.get("intent")
                if isinstance(session_state.get("intent"), str)
                else (
                    session_state.get("intent", {}).get("name", "")
                    if isinstance(session_state.get("intent"), dict)
                    else ""
                )
            )

            # Check if session intent is durable
            is_durable = False
            if session_intent_str:
                try:
                    is_durable = is_durable_intent(session_intent_str)
                except (ImportError, Exception) as e:
                    logger.warning(
                        f"[LUMA_ERROR_FALLBACK] Failed to check durable status: {e}"
                    )

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
                final_status = (
                    "NEEDS_CLARIFICATION" if session_missing_slots else session_status
                )

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
                        "executable_actions": (
                            [session_action] if session_action else []
                        ),
                    },
                    "facts": {
                        "slots": session_slots,
                        "missing_slots": session_missing_slots,
                    },
                }

                return {"success": True, "outcome": outcome}

            # For other intents, preserve session and return planning response (existing behavior)
            if session_state.get("status") == "NEEDS_CLARIFICATION":
                # Return planning response derived from session state
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}

                # Compute missing_slots from session intent and slots
                from core.planning.orchestration.missing_slots import (
                    compute_missing_slots,
                )

                missing_slots = (
                    compute_missing_slots(session_intent_str, session_slots)
                    if session_intent_str
                    else []
                )

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
                        "status": "NEEDS_CLARIFICATION" if missing_slots else "READY",
                    },
                }
        return {
            "success": False,
            "error": "upstream_error",
            "message": "Luma returned empty response",
        }

    # Step 2: Assert contract (fact-only: only requires intent.name)
    # Contract validation ensures intent.name exists for planning
    # Missing facts/slots are NOT errors - planner computes missing_slots
    try:
        assert_luma_contract(luma_response)
    except ContractViolation as e:
        logger.error(
            f"[LUMA_ERROR_FALLBACK] Contract violation for user {user_id}: {str(e)}"
        )
        # SESSION LIFECYCLE RULE: For durable intents, reuse session state as-is on contract violation
        # Do not recompute slots or missing_slots - preserve session state exactly as-is
        if session_state:
            # CRITICAL: Check intent_name first, then fall back to intent
            session_intent_str = session_state.get("intent_name") or (
                session_state.get("intent")
                if isinstance(session_state.get("intent"), str)
                else (
                    session_state.get("intent", {}).get("name", "")
                    if isinstance(session_state.get("intent"), dict)
                    else ""
                )
            )

            # Check if session intent is durable
            is_durable = False
            if session_intent_str:
                try:
                    is_durable = is_durable_intent(session_intent_str)
                except (ImportError, Exception) as e:
                    logger.warning(
                        f"[LUMA_ERROR_FALLBACK] Failed to check durable status: {e}"
                    )

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
                final_status = (
                    "NEEDS_CLARIFICATION" if session_missing_slots else session_status
                )

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
                        "executable_actions": (
                            [session_action] if session_action else []
                        ),
                    },
                    "facts": {
                        "slots": session_slots,
                        "missing_slots": session_missing_slots,
                    },
                }

                return {"success": True, "outcome": outcome}

            # For other intents, preserve session and return planning response (existing behavior)
            if session_state.get("status") == "NEEDS_CLARIFICATION":
                # Return planning response derived from session state
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}

                # Compute missing_slots from session intent and slots
                from core.planning.orchestration.missing_slots import (
                    compute_missing_slots,
                )

                missing_slots = (
                    compute_missing_slots(session_intent_str, session_slots)
                    if session_intent_str
                    else []
                )

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
                        "status": "NEEDS_CLARIFICATION" if missing_slots else "READY",
                    },
                }
        return {"success": False, "error": "contract_violation", "message": str(e)}

    # FACT-ONLY CONTRACT: No success flag check
    # Planning proceeds as long as intent.name exists (validated by assert_luma_contract)
    # Missing slots are NOT errors - planner will compute missing_slots from intent_planning.yaml

    # Step 3.5: Determine effective intent and construct effective_response
    # Intent override MUST happen BEFORE process_luma_response, planner, and allowed action checks
    log_transaction_id = f" transaction_id={transaction_id}" if transaction_id else ""

    # Guard: Ensure luma_response is valid before proceeding
    if not luma_response or not isinstance(luma_response, dict):
        logger.error(
            f"Invalid luma_response after error handling for user {user_id}: {luma_response}"
        )
        return {
            "success": False,
            "error": "invalid_luma_response",
            "message": "Luma response is None or invalid",
        }

    # Extract Luma intent for logging (before intent resolution)
    luma_intent_obj = luma_response.get("intent", {})
    luma_intent_name = (
        luma_intent_obj.get("name", "") if isinstance(
            luma_intent_obj, dict) else ""
    )

    # Resolve effective intent using new intent_resolution module
    from core.planning.orchestration.intent_resolution import resolve_effective_intent

    # CRITICAL: Preserve original session_state before intent resolution
    # This ensures session (with intent_name + slots) is available for merge_luma_with_session
    original_session_state = session_state
    effective_intent, session_reset_occurred = resolve_effective_intent(
        luma_response, session_state, user_id, transaction_id
    )

    # Track the source of session_reset_occurred
    session_reset_occurred_source = "resolve_effective_intent"
    logger.debug(
        f"[SESSION_RESET_WRITER] RECEIVED_FROM_RESOLVER value={session_reset_occurred} "
        f"source={session_reset_occurred_source} "
        f"effective_intent={effective_intent} "
        f"user_id={user_id}{log_transaction_id}"
    )

    # DEBUG: Log result immediately after resolve_effective_intent returns
    logger.debug(
        f"[ORCHESTRATOR] AFTER resolve_effective_intent: effective_intent={effective_intent} "
        f"session_reset_occurred={session_reset_occurred} "
        f"will_null_session_state={session_reset_occurred} "
        f"user_id={user_id}{log_transaction_id}"
    )

    # Update session_state if it was reset (for downstream logic)
    # BUT preserve original_session_state for merge decision - merge needs access to original session
    # CRITICAL: UNKNOWN → concrete intent transitions do NOT reset (intent materialization, not destructive switch)
    # Only true intent switches (e.g., CREATE_APPOINTMENT → DETAILS) reset the session
    # CRITICAL: Do NOT null session_state - capability reconciliation needs session facts even after intent reset
    # The session must remain visible for the entire turn, regardless of merge eligibility or intent reset
    # We preserve original_session_state for merge, but session_state must remain available for:
    # - process_luma_response (needs session for canonical_facts)
    # - capability gating (needs session.facts.payment_satisfied)
    # - TurnSnapshot building (needs session facts)
    if session_reset_occurred:
        # DEBUG: Log why session_state would be nulled (but we're NOT nulling it anymore)
        import traceback

        # Last 2 frames before this one
        call_stack = "".join(traceback.format_stack()[-3:-1])
        logger.debug(
            f"[ORCHESTRATOR] SESSION_RESET_OCCURRED=True (but preserving session_state for capability reconciliation) "
            f"effective_intent={effective_intent} "
            f"original_session_intent={original_session_state.get('intent_name') if original_session_state else None} "
            f"call_stack={call_stack} "
            f"user_id={user_id}{log_transaction_id}"
        )
        # DO NOT null session_state - preserve it for capability reconciliation
        # session_state = None  # REMOVED: This breaks capability reconciliation when should_merge_session is False

    # SINGLE SOURCE OF TRUTH: Ensure effective_intent is never None/empty when durable session intent exists
    # This is the authoritative intent that will be used throughout planning
    if not effective_intent or effective_intent == "UNKNOWN":
        # Fallback to durable session intent if available
        if session_state and not session_reset_occurred:
            session_intent = session_state.get("intent_name") or session_state.get(
                "intent"
            )
            session_intent_str = (
                session_intent
                if isinstance(session_intent, str)
                else (
                    session_intent.get("name", "")
                    if isinstance(session_intent, dict)
                    else ""
                )
            )
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
    confirm_booking_continuation = False
    # Rule: Any intent with durable=false may be recognized, but must:
    # - STOP before planning (do not call build_decision_plan)
    # - NOT be persisted to session (do not update session.intent_name or slots)
    # - Preserve any existing durable session state
    # Only durable intents may reach build_decision_plan
    # CRITICAL: Check AFTER UNKNOWN recovery to ensure we catch non-durable intents even after recovery

    # Confirmation gate: classify once (accept / reject / revise / none).
    from core.session.confirmation_gate import (
        ConfirmationGateTurn,
        classify_confirmation_gate_turn,
        detect_booking_revision,
    )
    from core.session.invalidation import InvalidationTrigger, apply_invalidation

    _gate_session = (
        original_session_state
        if isinstance(original_session_state, dict)
        else (session_state if isinstance(session_state, dict) else {})
    )
    _gate_booking_intent = _gate_session.get("intent_name") or ""
    if isinstance(_gate_booking_intent, dict):
        _gate_booking_intent = _gate_booking_intent.get("name") or ""
    gate_action = classify_confirmation_gate_turn(luma_response, _gate_session)
    revision_summary_for_turn = None
    try:
        from core.session.confirmation_gate import get_confirmation_state, is_confirmation_gate_open
        from core.tracing.confirmation import (
            emit_confirmation_classify_trace,
            emit_confirmation_gate_open_trace,
        )

        _gate_open = is_confirmation_gate_open(_gate_session)
        _gate_intent = str(_gate_booking_intent)
        _gate_open_id = emit_confirmation_gate_open_trace(
            session_state=_gate_session,
            gate_open=_gate_open,
            intent_name=_gate_intent,
            confirmation_state=get_confirmation_state(_gate_session),
            status=_gate_session.get("status"),
        )
        emit_confirmation_classify_trace(
            gate_action=gate_action.value,
            gate_open=_gate_open,
            raw_intent=luma_intent_name or "",
            has_revision=bool(
                detect_booking_revision(luma_response, _gate_session).any
            )
            if _gate_open
            else False,
            gate_open_id=_gate_open_id,
        )
    except ImportError:
        pass
    logger.info(
        f"[CONFIRMATION_GATE] action={gate_action.value} "
        f"raw_intent={luma_intent_name!r} booking_intent={_gate_booking_intent!r} "
        f"user_id={user_id}{log_transaction_id}"
    )

    if gate_action == ConfirmationGateTurn.REJECT and _gate_booking_intent:
        try:
            from core.rendering.booking_confirmation_renderer import (
                render_booking_confirmation_rejected,
            )

            cleared = apply_invalidation(
                dict(_gate_session),
                InvalidationTrigger.REJECT_CONFIRMATION,
                reason="reject",
            )
            session_slots = dict(cleared.get("slots") or {})
            missing_slots = (
                ["time"]
                if session_slots.get("service_id") and session_slots.get("date")
                else [
                    s
                    for s in ("service_id", "date", "time")
                    if not session_slots.get(s)
                ]
            )
            reject_text = render_booking_confirmation_rejected()
            merged = {
                "intent": {"name": _gate_booking_intent},
                "_effective_intent": _gate_booking_intent,
                "slots": session_slots,
                "missing_slots": missing_slots,
                "booking": {},
                "_booking_confirmation_rejected": True,
            }
            for key in (
                "date_proposal",
                "presented_availability",
                "last_execution_result",
                "availability_fingerprint",
            ):
                if cleared.get(key) is not None:
                    merged[key] = cleared.get(key)
            logger.info(
                f"[CONFIRMATION_GATE] REJECT cleared pending for "
                f"{_gate_booking_intent!r} (user_id={user_id}{log_transaction_id})"
            )
            return {
                "success": True,
                "text": reject_text,
                "outcome": {
                    "status": "NEEDS_CLARIFICATION",
                    "intent_name": _gate_booking_intent,
                    "slots": session_slots,
                    "missing_slots": missing_slots,
                    "booking": {},
                    "facts": {
                        "slots": session_slots,
                        "missing_slots": missing_slots,
                    },
                },
                "_merged_luma_response": merged,
            }
        except Exception as reject_exc:
            logger.warning(
                f"[CONFIRMATION_GATE] REJECT handling failed: {reject_exc}. "
                f"Falling through."
            )

    if gate_action == ConfirmationGateTurn.REVISE and _gate_booking_intent:
        # Field-aware invalidation: time-only keeps presented list; date/service
        # drop availability artifacts so planner re-searches.
        # Capture revision summary before apply mutates session (for acknowledgements).
        cleared = dict(_gate_session)
        revision = detect_booking_revision(luma_response, _gate_session)
        if revision.any:
            revision_summary_for_turn = revision.to_summary()
            apply_invalidation(
                cleared,
                InvalidationTrigger.BOOKING_REVISION,
                revision=revision,
                reason="gate_revise",
            )
            logger.info(
                f"[CONFIRMATION_GATE] REVISE fields="
                f"service={revision.service} date={revision.date} time={revision.time} "
                f"for {_gate_booking_intent!r} (user_id={user_id}{log_transaction_id})"
            )
        else:
            apply_invalidation(
                cleared,
                InvalidationTrigger.REVISE_FALLBACK,
                reason="revise_fallback",
            )
            logger.info(
                f"[CONFIRMATION_GATE] REVISE fallback clear_time for "
                f"{_gate_booking_intent!r} (user_id={user_id}{log_transaction_id})"
            )
        original_session_state = cleared
        session_state = cleared
        effective_intent = _gate_booking_intent

    if gate_action == ConfirmationGateTurn.ACCEPT and _gate_booking_intent:
        effective_intent = _gate_booking_intent
        confirm_booking_continuation = True
        logger.info(
            f"[CONFIRMATION_GATE] ACCEPT for {_gate_booking_intent!r} "
            f"(user_id={user_id}{log_transaction_id})"
        )

    if effective_intent and effective_intent != "UNKNOWN":
        try:
            from core.policy.intent_policy import get_intent_durable

            is_durable = get_intent_durable(effective_intent)

            if not is_durable:
                # Legacy CONFIRM_ACTION path when gate classifier did not fire
                # (e.g. gate closed). Prefer classify_confirmation_gate_turn.
                from core.session.confirmation_gate import get_confirmation_state

                _session_for_confirm = (
                    session_state if isinstance(session_state, dict) else {}
                )
                _session_booking_intent = _session_for_confirm.get(
                    "intent_name", "")
                _session_confirmation = get_confirmation_state(
                    _session_for_confirm)
                if (
                    not confirm_booking_continuation
                    and effective_intent == "CONFIRM_ACTION"
                    and _session_booking_intent
                    and (
                        _session_for_confirm.get("status") == "READY"
                        or _session_confirmation == "pending"
                    )
                ):
                    try:
                        from core.policy.intent_policy import get_intent_durable as _gid

                        if _gid(_session_booking_intent):
                            logger.info(
                                f"[CONFIRM_ACTION] Rerouting to session booking intent "
                                f"{_session_booking_intent!r} (session READY) "
                                f"(user_id={user_id}{log_transaction_id})"
                            )
                            effective_intent = _session_booking_intent
                            confirm_booking_continuation = True
                            is_durable = True
                    except Exception:
                        pass

                # Booking refinement: CORRECTION, AVAILABILITY, CHECK_AVAILABILITY within an
                # active durable session. User is refining slots (date/time/service), not starting
                # a new flow. Unlike CONFIRM_ACTION, valid in any session status.
                _BOOKING_REFINEMENT_INTENTS = frozenset(
                    {"CORRECTION", "AVAILABILITY", "CHECK_AVAILABILITY"}
                )
                if (
                    not is_durable
                    and effective_intent in _BOOKING_REFINEMENT_INTENTS
                    and _session_booking_intent
                ):
                    try:
                        from core.policy.intent_policy import get_intent_durable as _gid

                        if _gid(_session_booking_intent):
                            logger.info(
                                f"[{effective_intent}] Rerouting to session booking intent "
                                f"{_session_booking_intent!r} "
                                f"(user_id={user_id}{log_transaction_id})"
                            )
                            effective_intent = _session_booking_intent
                            is_durable = True
                    except Exception:
                        pass  # If check fails, fall through to NON_DURABLE_INTENT

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

                    slots = (
                        facts_to_slots(
                            facts_obj,
                            intent_name=effective_intent,
                            source_text=text,
                        )
                        if isinstance(facts_obj, dict)
                        else {}
                    )

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
                    from core.routing.handler_router import resolve_handler

                    _handler = resolve_handler(effective_intent)
                    _base_outcome = {
                        "intent_name": effective_intent,
                        "slots": slots,
                        "missing_slots": [],
                        "facts": {
                            "slots": slots,
                            "missing_slots": [],
                            "context": luma_response.get("context", {}),
                        },
                    }
                    if _handler == "rag":
                        return {
                            "success": True,
                            "outcome": {
                                **_base_outcome,
                                "status": "HANDLER_DELEGATED",
                                "active_handler": _handler,
                                "search_query": luma_response.get("search_query"),
                            },
                        }
                    return {
                        "success": True,
                        "outcome": {**_base_outcome, "status": "NON_DURABLE_INTENT"},
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
    if confirm_booking_continuation:
        effective_response["_confirm_booking_continuation"] = True
    # Turn-only metadata for revision acknowledgements (not persisted as business state).
    if revision_summary_for_turn:
        effective_response["_revision_summary"] = revision_summary_for_turn

    # Attach updated conversation memory so session_merge can persist it.
    # Records the effective intent (post-recovery) and search_query for this turn.
    _updated_session = update_conversation(
        session_state or {},
        user_text=text,
        intent=effective_intent or "UNKNOWN",
        search_query=luma_response.get("search_query"),
    )
    effective_response["_conversation"] = _updated_session.get("conversation")

    # FACT-ONLY: Promote facts to slots BEFORE any other processing
    # This ensures facts.service_id, facts.times, etc. are available for planning
    from core.orchestration.luma_facts_adapter import (
        facts_to_slots,
        merge_promoted_luma_slots,
    )

    facts_obj = luma_response.get("facts", {})
    # Pass effective_intent for date_range promotion (CREATE_RESERVATION with 2+ dates)
    promoted_slots = (
        facts_to_slots(
            facts_obj,
            intent_name=effective_intent,
            source_text=text,
        )
        if isinstance(facts_obj, dict)
        else {}
    )

    # Merge session-merged top-level slots with nested facts.slots.
    # merge_luma_with_session writes durable slots (e.g. date from prior UNKNOWN turn)
    # to luma_response["slots"]; facts.slots is often partial and must not replace them.
    merged_authoritative_slots = luma_response.get("slots") or {}
    if not isinstance(merged_authoritative_slots, dict):
        merged_authoritative_slots = {}
    nested_from_facts: Dict[str, Any] = {}
    if isinstance(facts_obj, dict) and "slots" in facts_obj:
        nested_from_facts = facts_obj.get("slots") or {}
        if not isinstance(nested_from_facts, dict):
            nested_from_facts = {}
    nested_slots = {**nested_from_facts, **merged_authoritative_slots}

    # Merge promoted slots; strip date keys when Fix 4 applies (flexible + same-turn service)
    effective_response["slots"] = merge_promoted_luma_slots(
        nested_slots,
        promoted_slots,
        luma_response.get("date_constraint"),
        facts_obj if isinstance(facts_obj, dict) else None,
    )

    # Preserve the raw user text for follow-up slot promotion
    effective_response["_source_text"] = text

    # Extract constraints from luma_response for planning
    time_constraint = luma_response.get("time_constraint")
    if time_constraint:
        effective_response["time_constraint"] = time_constraint
    date_constraint = luma_response.get("date_constraint")
    if date_constraint:
        effective_response["date_constraint"] = date_constraint

    from core.orchestration.temporal_proposal import (
        extract_nlu_proposals,
        merge_session_proposals,
    )

    _proposal_session = original_session_state if original_session_state else {}
    _nlu_proposals = extract_nlu_proposals(luma_response)
    _merged_proposals = merge_session_proposals(
        _proposal_session,
        _nlu_proposals["date_proposal"],
        _nlu_proposals["time_proposal"],
    )
    effective_response["date_proposal"] = _merged_proposals["date_proposal"]
    effective_response["time_proposal"] = _merged_proposals["time_proposal"]

    # Normalize service_id using tenant aliases (e.g., "suite" -> "room", "deluxe" -> "room")
    # CRITICAL: Preserve raw tenant value while storing canonical for planning
    if (
        tenant_context
        and "aliases" in tenant_context
        and "service_id" in effective_response["slots"]
    ):
        aliases = tenant_context["aliases"]
        raw_service_id = effective_response["slots"]["service_id"]
        if isinstance(raw_service_id, str) and raw_service_id.lower() in aliases:
            mapped = aliases[raw_service_id.lower()]
            if isinstance(mapped, int):
                effective_response["slots"]["_catalog_item_id"] = mapped
                logger.debug(
                    "Resolved catalog item id for planning: %s -> %s",
                    raw_service_id,
                    mapped,
                )
            else:
                canonical_service_id = mapped
                logger.info(
                    f"Normalized service_id: {raw_service_id} -> {canonical_service_id} (via tenant aliases)"
                )
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
            "intent_name"
        ) or original_session_state.get("intent")
        session_status_for_trace = original_session_state.get("status")
    logger.debug(
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
    from core.session.effective_slots import _compute_effective_collected_slots
    from core.session.merge import merge_luma_with_session

    # Initialize prior_slots for logging (used later)
    prior_slots = []
    prior_intent = None
    prior_missing = []

    # If session exists and not reset, merge slots from session
    # SESSION LIFECYCLE RULE: Merge for NEEDS_CLARIFICATION sessions OR READY sessions with durable intents
    # CRITICAL: Use original_session_state (preserved before reset) for merge decision and merge call
    # This ensures session (with intent_name + slots) is always available even if session_state was set to None
    session_for_merge = (
        original_session_state if original_session_state else session_state
    )
    session_status_for_merge = (
        session_for_merge.get("status") if session_for_merge else None
    )
    # CRITICAL: Session stores intent_name, not intent. Check intent_name first, then fall back to intent.
    session_intent_for_merge = (
        session_for_merge.get("intent_name") if session_for_merge else None
    )
    if not session_intent_for_merge and session_for_merge:
        # Fallback to intent (for backward compatibility)
        session_intent_for_merge = session_for_merge.get("intent")
    session_intent_str_for_merge = (
        session_intent_for_merge
        if isinstance(session_intent_for_merge, str)
        else (
            session_intent_for_merge.get("name", "")
            if isinstance(session_intent_for_merge, dict)
            else ""
        )
    )

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

    # SESSION MERGE GATING: durable-flow based (status does not decide eligibility).
    # Merge when a durable booking flow is active, or pre-intent slots await
    # materialization, unless session_reset_occurred (true intent switch).

    # DEBUG: Log merge decision inputs before computing should_merge_session
    session_for_merge_intent = (
        session_for_merge.get("intent_name") if session_for_merge else None
    )
    session_for_merge_slots = (
        list(session_for_merge.get("slots", {}).keys()
             ) if session_for_merge else []
    )

    # CRITICAL: Final check - who last set session_reset_occurred?
    logger.debug(
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

    from core.session.merge import should_merge_session_context

    should_merge_session = should_merge_session_context(
        session_for_merge,
        session_reset_occurred=session_reset_occurred,
    )

    try:
        from core.tracing.merge import emit_merge_eligibility_trace

        merge_ctx = session_for_merge if isinstance(
            session_for_merge, dict) else {}
        session_intent_for_eligibility = merge_ctx.get("intent_name") or ""
        if isinstance(session_intent_for_eligibility, dict):
            session_intent_for_eligibility = session_intent_for_eligibility.get(
                "name") or ""
        emit_merge_eligibility_trace(
            eligible=should_merge_session,
            session_reset_occurred=session_reset_occurred,
            intent_name=str(session_intent_for_eligibility),
            has_session_slots=bool(merge_ctx.get("slots")),
        )
    except ImportError:
        pass

    logger.debug(
        f"[ORCHESTRATOR] BEFORE should_merge_session: session_reset_occurred={session_reset_occurred} "
        f"session_for_merge_intent={session_for_merge_intent} "
        f"session_for_merge_slots={session_for_merge_slots} "
        f"session_status_for_merge={session_status_for_merge} "
        f"is_session_intent_durable={is_session_intent_durable} "
        f"should_merge_session={should_merge_session} "
        f"user_id={user_id}{log_transaction_id}"
    )

    # DEBUG: Log final merge decision
    logger.debug(
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

        try:
            from core.tracing.decision_trace import measure_stage
        except ImportError:
            from contextlib import contextmanager

            @contextmanager
            def measure_stage(_stage: str):  # type: ignore[misc]
                yield

        with measure_stage("merge"):
            effective_response = merge_luma_with_session(
                effective_response, session_for_merge, planning_only=planning_only
            )

        # CRITICAL: If CONFIRM_* intent was treated as continuation of durable session intent,
        # set confirmation_state to "confirmed" in the booking object
        # NOTE: Use original luma_intent_name (from line 1446) BEFORE merge, not after merge
        # because merge_luma_with_session changes effective_response['intent']['name'] to session intent
        # HARDENED: Set confirmation_state when user confirms an in-progress booking.
        # AWAITING_CONFIRMATION: explicit confirm prompt path.
        # READY + CONFIRM_ACTION reroute: "yes" after availability search (mock-booking flows).
        from core.session.confirmation_gate import (
            get_confirmation_state,
            set_confirmation_state,
        )

        _session_confirmation = get_confirmation_state(session_for_merge)
        _confirm_continuation = (
            luma_intent_name
            and luma_intent_name.startswith("CONFIRM_")
            and effective_intent != luma_intent_name
            and is_session_intent_durable
            and (
                session_status_for_merge == "AWAITING_CONFIRMATION"
                or _session_confirmation == "pending"
                or (
                    session_status_for_merge == "READY"
                    and luma_intent_name == "CONFIRM_ACTION"
                )
            )
        )
        if _confirm_continuation:
            # CONFIRM_* was treated as continuation - set confirmation_state
            set_confirmation_state(effective_response, "confirmed")
            logger.info(
                f"[CONFIRM_CONTINUATION] Set confirmation_state=confirmed for CONFIRM_* continuation "
                f"of durable intent {effective_intent}, session.status={session_status_for_merge}, user_id={user_id}"
            )

        # AFTER_MERGE: Log right after session merge
        effective_collected_slots = effective_response.get(
            "_effective_collected_slots", {}
        )
        after_merge_log = {
            "trace": "AFTER_MERGE",
            "intent": effective_response.get("intent"),
            "slots": effective_response.get("slots"),
            "effective_collected_slots": effective_collected_slots,
            "modification_context": effective_response.get("_modification_context"),
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
                effective_response, planning_only=planning_only
            )

            # AFTER_MERGE: Log right after computing effective_collected_slots (first turn)
            effective_collected_slots = effective_response.get(
                "_effective_collected_slots", {}
            )
            after_merge_log = {
                "trace": "AFTER_MERGE",
                "intent": effective_response.get("intent"),
                "slots": effective_response.get("slots"),
                "effective_collected_slots": effective_collected_slots,
                "modification_context": effective_response.get("_modification_context"),
            }
            # INVARIANT CHECK: missing_slots must be computed for first turns
            if "missing_slots" not in effective_response:
                # This should never happen if _compute_effective_collected_slots worked correctly
                logger.error(
                    f"[MISSING_SLOTS] VIOLATION: missing_slots not computed in _compute_effective_collected_slots! "
                    f"user_id={user_id}, effective_response_keys={list(effective_response.keys())}"
                )
                # Fail-safe: compute missing_slots now
                from core.planning.orchestration.missing_slots import (
                    compute_missing_slots,
                )

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

    from core.tracing.invariant_trace import trace_stage
    from core.tracing.stage_checks import check_merge

    trace_stage(
        "merge",
        lambda: check_merge(
            effective_response=effective_response,
            session_state=session_state,
            prior_intent=prior_intent if isinstance(
                prior_intent, str) else None,
        ),
        allowed_mutations=["slots", "missing_slots", "intent", "facts"],
        forbidden_mutations=["session.durable_slots_unless_invalidation"],
        state_snapshot={
            "effective_intent": effective_intent_name,
            "missing_slots": effective_response.get("missing_slots"),
            "slot_keys": sorted(effective_response.get("slots", {}).keys()),
        },
    )

    # Verify intent before processing
    # Guard: effective_response must be a dict
    if not effective_response or not isinstance(effective_response, dict):
        logger.error(
            f"effective_response is None or not a dict: {effective_response}")
        return {
            "success": False,
            "error": "internal_error",
            "message": "Invalid effective_response",
        }

    final_intent_check = effective_response.get("intent", {}).get("name", "")
    if (
        final_intent_check == "UNKNOWN"
        and session_state
        and session_state.get("status") == "NEEDS_CLARIFICATION"
    ):
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
    if (
        not planning_intent or planning_intent == "UNKNOWN"
    ) and effective_intent_for_planning:
        planning_intent = effective_intent_for_planning
        effective_response["intent"] = {"name": planning_intent}
        logger.info(
            f"[INTENT_PRESERVATION] Recovered intent for planning: {planning_intent} "
            f"(was UNKNOWN/empty, user_id={user_id}{log_transaction_id})"
        )

    # If still UNKNOWN/empty and session has durable intent, recover from session
    if (
        (not planning_intent or planning_intent == "UNKNOWN")
        and session_state
        and not session_reset_occurred
    ):
        session_intent = session_state.get("intent")
        session_intent_str = (
            session_intent
            if isinstance(session_intent, str)
            else (
                session_intent.get("name", "")
                if isinstance(session_intent, dict)
                else ""
            )
        )
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
    if (
        (not planning_intent or planning_intent == "UNKNOWN")
        and session_state
        and not session_reset_occurred
    ):
        session_intent = session_state.get("intent")
        session_intent_str = (
            session_intent
            if isinstance(session_intent, str)
            else (
                session_intent.get("name", "")
                if isinstance(session_intent, dict)
                else ""
            )
        )
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

    # CRITICAL: session_state is now preserved even when session_reset_occurred is True
    # This ensures capability reconciliation can access session facts (e.g., payment_satisfied)
    # The session must be visible for the entire turn, regardless of merge eligibility or intent reset
    # No restoration needed - session_state is never nulled anymore

    # PHASE 1 INSTRUMENTATION: Log intent state BEFORE process_luma_response
    session_intent_for_trace = None
    session_status_for_trace = None
    if session_state:
        session_intent_for_trace = session_state.get(
            "intent_name"
        ) or session_state.get("intent")
        session_status_for_trace = session_state.get("status")
    logger.debug(
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
    assert isinstance(
        missing_slots_before_plan, list
    ), f"missing_slots must be a list before planning, got {type(missing_slots_before_plan)}: {missing_slots_before_plan}"

    # LUMA_RAW: Log raw Luma response (facts, intent, source_text)
    raw_luma_response = effective_response.get("_raw_luma_response", {})
    raw_luma_facts = (
        raw_luma_response.get("facts", {})
        if isinstance(raw_luma_response, dict)
        else {}
    )
    raw_luma_intent = (
        raw_luma_response.get("intent", {})
        if isinstance(raw_luma_response, dict)
        else {}
    )
    raw_luma_intent_name = (
        raw_luma_intent.get("name", "") if isinstance(
            raw_luma_intent, dict) else ""
    )
    source_text = effective_response.get("_source_text", text)
    # LUMA_RAW: Log raw Luma response (facts, intent, source_text)
    raw_luma_response = effective_response.get("_raw_luma_response", {})
    raw_luma_facts = (
        raw_luma_response.get("facts", {})
        if isinstance(raw_luma_response, dict)
        else {}
    )
    raw_luma_intent = (
        raw_luma_response.get("intent", {})
        if isinstance(raw_luma_response, dict)
        else {}
    )
    raw_luma_intent_name = (
        raw_luma_intent.get("name", "") if isinstance(
            raw_luma_intent, dict) else ""
    )
    source_text = effective_response.get("_source_text", text)
    logger.info(
        "[LUMA_RAW] user_id=%s facts=%s intent=%s source_text=%s",
        user_id,
        json.dumps(raw_luma_facts, default=str, ensure_ascii=True),
        raw_luma_intent_name,
        source_text,
    )

    # Add organization data to facts for capability evaluation
    # This allows capability conditions to read org.payment_required, etc.
    if organization_client and organization_id:
        try:
            org_details = organization_client.get_details(organization_id)
            if isinstance(org_details, dict):
                # Organization data may be at top level or under "organization" key
                org_data = org_details.get("organization") or org_details
                if org_data and isinstance(org_data, dict):
                    # Ensure facts structure exists
                    if "facts" not in effective_response:
                        effective_response["facts"] = {}
                    if not isinstance(effective_response["facts"], dict):
                        effective_response["facts"] = {}
                    # Add org data to facts["org"]
                    effective_response["facts"]["org"] = org_data
        except Exception as e:
            # If org fetch fails, log but don't crash
            # Capability evaluation will handle missing org data gracefully
            logger.debug(
                f"Failed to fetch organization data for capability evaluation: {e}"
            )

    try:
        from core.tracing.decision_trace import measure_stage as _measure_stage
    except ImportError:
        from contextlib import contextmanager

        @contextmanager
        def _measure_stage(_stage: str):  # type: ignore[misc]
            yield

    with _measure_stage("planner"):
        decision = process_luma_response(
            effective_response,
            derived_domain,
            user_id,
            session_state=session_state,
            organization_id=organization_id,
        )

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
        # NOTE: CREATE_APPOINTMENT action selection is now handled by fingerprint gating in plan_builder
        # Do not override planner's decision here
        if missing_slots:
            stage = "AVAILABILITY"
            action = "SEARCH_AVAILABILITY"
        else:
            stage = "CONFIRM"
            # Apply action selection rules for complete slots
            if intent_name == "CREATE_RESERVATION":
                action = "FINALIZE_RESERVATION"
            else:
                action = None

        minimal_plan = {
            "status": "NEEDS_CLARIFICATION" if missing_slots else "READY",
            "allowed_actions": [],
            "blocked_actions": [],
            "awaiting": None,
            "executable_actions": [],
            "stage": stage,
            "action": action,
        }

        # Build minimal decision
        decision = {
            "intent_name": intent_name,
            "plan": minimal_plan,
            "facts": {
                "slots": slots,
                "missing_slots": missing_slots,
                "context": effective_response.get("context", {}),
            },
        }
        logger.warning(
            f"Created minimal decision due to process_luma_response failure: intent={intent_name}, missing_slots={missing_slots}"
        )

    # HYDRATE ORGANIZATION FACTS INTO DECISION (for capability gating)
    # This MUST happen BEFORE: capability gating, PLAN_FINAL, SESSION_SAVE
    # This ensures decision.facts.org is populated before any status override or session persistence
    # Hard rules:
    # - Do NOT guard this behind intent checks
    # - Do NOT rely on planner to fetch org
    # - Do NOT save session before this runs
    if decision and isinstance(decision.get("facts"), dict):
        facts = decision["facts"]
        # Only hydrate if org data is missing from decision.facts
        if "org" not in facts or not isinstance(facts.get("org"), dict):
            org_data = None
            org_id_to_fetch = None
            org_id_source = None

            # Priority 1: Try to get org data from effective_response.facts.org (if already present)
            if effective_response and isinstance(effective_response.get("facts"), dict):
                effective_org = effective_response["facts"].get("org")
                if isinstance(effective_org, dict):
                    org_data = effective_org
                    # Also check if effective_response.facts.org.id exists for org_id derivation
                    if not org_id_to_fetch and "id" in effective_org:
                        org_id_to_fetch = effective_org.get("id")
                        org_id_source = "effective_response.facts.org.id"

            # Priority 2: Extract org_id from decision.booking.organization_id
            if not org_id_to_fetch and decision.get("booking"):
                booking = decision.get("booking", {})
                if isinstance(booking, dict):
                    org_id_to_fetch = booking.get("organization_id")
                    if org_id_to_fetch:
                        org_id_source = "decision.booking.organization_id"

            # Priority 3: Extract org_id from decision.facts.slots.organization_id
            if not org_id_to_fetch and isinstance(facts.get("slots"), dict):
                org_id_to_fetch = facts["slots"].get("organization_id")
                if org_id_to_fetch:
                    org_id_source = "decision.facts.slots.organization_id"

            # Priority 4: Use organization_id parameter (from handle_message)
            if not org_id_to_fetch and organization_id:
                org_id_to_fetch = organization_id
                org_id_source = "handle_message.organization_id"

            # Fetch org data if we have an org_id and don't already have org_data
            if not org_data and org_id_to_fetch and organization_client:
                try:
                    logger.info(
                        f"[ORG_HYDRATION] Fetching organization data for org_id={org_id_to_fetch} "
                        f"(source={org_id_source})"
                    )
                    org_details = organization_client.get_details(
                        org_id_to_fetch)
                    if isinstance(org_details, dict):
                        # Organization data may be at top level or under "organization" key
                        org_data = org_details.get(
                            "organization") or org_details
                        logger.info(
                            f"[ORG_HYDRATION] Successfully fetched organization data: "
                            f"payment_required={org_data.get('payment_required') if isinstance(org_data, dict) else 'N/A'}"
                        )
                except Exception as e:
                    # If org fetch fails, log but don't crash
                    logger.warning(
                        f"[ORG_HYDRATION] Failed to fetch organization data for org_id={org_id_to_fetch}: {e}"
                    )

            # Inject org_data into decision.facts if we have it
            if org_data and isinstance(org_data, dict):
                facts["org"] = org_data
                logger.info(
                    f"[ORG_HYDRATION] Injected organization facts into decision.facts.org "
                    f"(org_id={org_id_to_fetch or 'from_effective_response'}, "
                    f"payment_required={org_data.get('payment_required')})"
                )
            else:
                logger.debug(
                    f"[ORG_HYDRATION] No organization data available to inject "
                    f"(org_id_to_fetch={org_id_to_fetch}, organization_client={organization_client is not None})"
                )

    # Extract decision plan
    plan = decision.get("plan", {})
    plan_status = plan.get("status", "READY")
    allowed_actions = plan.get("allowed_actions", [])
    blocked_actions = plan.get("blocked_actions", [])
    awaiting = plan.get("awaiting")

    # Planner is authoritative - no override logic needed
    # Execution eligibility is now driven by action-level policy in the execution gate

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
                slots.get("date_range", {}).get("start")
            )
            has_datetime_range = isinstance(slots.get("datetime_range"), dict) and bool(
                slots.get("datetime_range", {}).get("start")
            )

            # Check if sufficient temporal information exists
            has_sufficient_temporal = (
                (has_date and has_time)
                or (has_date_range and has_time)  # date + time
                or has_datetime_range  # date_range + time  # datetime_range
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
                    f"Set has_datetime=true in facts.slots (planning invariant: READY with temporal info)"
                )

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
        if (
            missing_slots is None
            and effective_response.get("missing_slots") is not None
        ):
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
                            "facts", {}
                        ).get("service_id")
                        if raw_facts_in_effective:
                            raw_slots["service_id"] = raw_facts_in_effective

                    slots = raw_slots

        # CRITICAL: Always extract stage and action from decision.plan (authoritative source)
        # This ensures plan.stage and plan.action are always present in the outcome
        # Do not rely on plan_message() or other sources - decision.plan is the single source of truth
        decision_plan = decision.get("plan", {}) if decision else {}
        if not isinstance(decision_plan, dict):
            decision_plan = {}

        # Extract stage and action from decision.plan (always use this source)
        stage = decision_plan.get("stage")
        action = decision_plan.get("action")

        # CRITICAL: Always populate plan object with all required fields
        # This ensures plan.stage and plan.action are always present (no silent failures)
        # HARD RULE: Include both intent and intent_name for session persistence
        populated_plan = {
            "intent": intent_name,
            # For session persistence - build_session_state_from_outcome reads plan.intent_name
            "intent_name": intent_name,
            "stage": stage,  # Always from decision.plan
            "action": action,  # Always from decision.plan
            "missing_slots": missing_slots,
            "slots": slots,
            "status": plan_status,
            "executable_actions": plan.get("executable_actions", []),
            "allowed_actions": plan.get("allowed_actions", []),
            "blocked_actions": plan.get("blocked_actions", []),
        }

        # CAPABILITY GATING: Override plan status if payment is required but not satisfied
        # This happens in post-planning finalization (right before outcome is built)
        # Check organization payment requirements and payment satisfaction status
        org_data = None
        # Check decision.facts.org first (if process_luma_response copied it)
        if decision and isinstance(decision.get("facts"), dict):
            org_data = decision["facts"].get("org")
        # Fall back to effective_response.facts.org (where org data is added before process_luma_response)
        if (
            not org_data
            and effective_response
            and isinstance(effective_response.get("facts"), dict)
        ):
            org_data = effective_response["facts"].get("org")

        payment_required = False
        if org_data and isinstance(org_data, dict):
            payment_required = org_data.get("payment_required", False)

        payment_satisfied = False
        # INSTRUMENTATION: Log all three data sources to identify which one is missing payment_satisfied
        decision_facts_payment_satisfied = None
        session_facts_payment_satisfied = None
        outcome_facts_payment_satisfied = None

        # Check decision.facts.payment_satisfied first
        if decision and isinstance(decision.get("facts"), dict):
            decision_facts_payment_satisfied = decision["facts"].get(
                "payment_satisfied"
            )
            payment_satisfied = (
                decision_facts_payment_satisfied
                if decision_facts_payment_satisfied
                else False
            )

        # Check session_state.facts.payment_satisfied
        if session_state and isinstance(session_state.get("facts"), dict):
            session_facts_payment_satisfied = session_state["facts"].get(
                "payment_satisfied"
            )

        # Check effective_response.facts.payment_satisfied (for test scenarios)
        if effective_response and isinstance(effective_response.get("facts"), dict):
            outcome_facts_payment_satisfied = effective_response["facts"].get(
                "payment_satisfied"
            )

        # CRITICAL FIX: Session facts are authoritative for reconciliation
        # When session_state exists, use session_state["facts"] as the primary source
        # This ensures capability completion facts (payment_satisfied) from previous turns are respected
        if session_state and isinstance(session_state.get("facts"), dict):
            session_payment_satisfied = session_state["facts"].get(
                "payment_satisfied")
            if session_payment_satisfied is not None:
                payment_satisfied = session_payment_satisfied
                logger.info(
                    f"[CAPABILITY_GATING] Using session_state.facts.payment_satisfied={payment_satisfied} "
                    f"(session facts are authoritative for reconciliation)"
                )
        elif not payment_satisfied:
            # Fall back to decision.facts if session_state doesn't have it
            if decision_facts_payment_satisfied is not None:
                payment_satisfied = decision_facts_payment_satisfied
            # Fall back to effective_response.facts if neither session nor decision have it
            elif outcome_facts_payment_satisfied is not None:
                payment_satisfied = outcome_facts_payment_satisfied

        # INSTRUMENTATION: Log all three sources for debugging
        logger.debug(
            f"[CAPABILITY_GATING_INSTRUMENTATION] payment_satisfied sources: "
            f"decision.facts={decision_facts_payment_satisfied}, "
            f"session_state.facts={session_facts_payment_satisfied}, "
            f"effective_response.facts={outcome_facts_payment_satisfied}, "
            f"FINAL={payment_satisfied}"
        )

        # Debug logging
        logger.debug(
            f"Capability gating check: org_data={org_data is not None}, "
            f"payment_required={payment_required}, payment_satisfied={payment_satisfied}, "
            f"plan_status={plan_status}"
        )

        # Override plan status if payment is required but not satisfied
        # This override applies even if planner returns READY
        if payment_required and not payment_satisfied:
            logger.info(
                f"Capability gating: payment_required=True, payment_satisfied=False. "
                f"Overriding plan.status from '{plan_status}' to 'AWAITING_CAPABILITY'"
            )
            plan_status = "AWAITING_CAPABILITY"
            populated_plan["status"] = "AWAITING_CAPABILITY"
            populated_plan["awaiting"] = "PAYMENT"
            # Lowercase adapter key
            populated_plan["active_capability"] = "payment"

            # Update decision.plan so that build_outcome_from_decision() uses the correct values
            if decision and isinstance(decision.get("plan"), dict):
                decision["plan"]["status"] = "AWAITING_CAPABILITY"
                decision["plan"]["awaiting"] = "PAYMENT"
                # Lowercase adapter key
                decision["plan"]["active_capability"] = "payment"
        elif payment_required and payment_satisfied:
            # CRITICAL: When payment is satisfied, clear active_capability to prevent re-entering payment capability
            # This ensures "paid" sessions do not re-enter AWAITING_CAPABILITY state
            logger.info(
                f"Capability gating: payment_required=True, payment_satisfied=True. "
                f"Clearing active_capability to prevent re-entry into payment capability"
            )
            # Clear active_capability from plan
            populated_plan["active_capability"] = None
            if "active_capability" in populated_plan:
                del populated_plan["active_capability"]

            # Update decision.plan to clear active_capability
            if decision and isinstance(decision.get("plan"), dict):
                decision["plan"]["active_capability"] = None
                if "active_capability" in decision["plan"]:
                    del decision["plan"]["active_capability"]

        # CRITICAL: Ensure outcome always uses raw service_id (not canonical)
        # Dialog output, outcome.slots, outcome.facts.slots MUST use raw tenant value
        from core.orchestration.temporal_proposal import (
            has_bound_booking_datetime,
            strip_unconfirmed_temporal_slots,
        )

        outcome_slots = strip_unconfirmed_temporal_slots(
            slots.copy() if isinstance(slots, dict) else {},
            intent_name,
            session_state,
            confirmed=has_bound_booking_datetime(
                slots, session_state, effective_response
            ),
        )
        slots = outcome_slots
        populated_plan["slots"] = outcome_slots

        intentionally_dropped_slots = set()
        if effective_response and isinstance(effective_response, dict):
            intentionally_dropped_slots = (
                effective_response.get("_intentionally_dropped_slots") or set()
            )
        service_candidates = None
        if session_state and isinstance(session_state, dict):
            service_candidates = session_state.get("service_candidates")

        skip_session_service_reinject = bool(
            "service_id" in intentionally_dropped_slots
            or (service_candidates and len(service_candidates) > 0)
        )

        # Get raw service_id from session or effective_response
        # Priority: 1) session.slots["service_id"], 2) effective_response slots, 3) current slots
        raw_service_id_for_outcome = None
        if not skip_session_service_reinject and session_state and isinstance(
            session_state, dict
        ):
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
                f"Using raw service_id in outcome: {raw_service_id_for_outcome}"
            )
        elif "service_id" in outcome_slots:
            # Keep existing service_id if no raw found (shouldn't happen but fail-safe)
            logger.debug(
                f"Using existing service_id in outcome: {outcome_slots.get('service_id')}"
            )

        # Remove canonical from outcome slots (never expose canonical to tests/dialog)
        if "_canonical_service_id" in outcome_slots:
            del outcome_slots["_canonical_service_id"]
            logger.debug(
                f"Removed _canonical_service_id from outcome, using raw service_id: {outcome_slots.get('service_id')}"
            )

        # Construct flattened planning response with both structures:
        # 1. Flattened fields at outcome level (for test compatibility)
        # 2. Complete plan object (for observability and debugging)
        # 3. Facts object for backward compatibility (snapshot builder reads outcome.facts.*)
        # CRITICAL: Start from decision.facts to preserve capability facts (e.g., payment_satisfied)
        # Do NOT create a new facts dict - this would discard capability completion markers
        decision_facts = decision.get("facts", {}) if decision else {}
        if not isinstance(decision_facts, dict):
            decision_facts = {}

        # Build outcome facts: preserve all decision facts, overlay missing_slots and slots
        outcome_facts = {
            # Preserve capability facts (payment_satisfied, payment_reference, org, etc.)
            **decision_facts,
            "missing_slots": missing_slots,  # Overlay with computed missing_slots
            # Overlay with computed slots (use raw service_id only)
            "slots": outcome_slots,
        }

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
                # Preserve decision facts (includes capability completion markers)
                "facts": outcome_facts,
            },
        }

        # Add active_capability if present in populated_plan (from capability gating)
        if populated_plan.get("active_capability"):
            result["outcome"]["active_capability"] = populated_plan["active_capability"]

        # Store effective Luma response for session building (for test snapshots)
        if effective_response:
            result["_merged_luma_response"] = effective_response

        # Store decision for plan_message to access (for early return paths in handle_message)
        result["_decision"] = decision

        # DEBUG LOG: Finalization point (after all overrides, before PLAN_FINAL)
        # Track fingerprint-based availability resolution for debugging
        stored_fp = (
            session_state.get(
                "availability_fingerprint") if session_state else None
        )
        last_exec_result = (
            session_state.get(
                "last_execution_result") if session_state else None
        )
        from core.orchestration.availability_fingerprint import (
            build_availability_fingerprint_slots,
            compute_availability_fingerprint,
        )

        fp_slots = (
            build_availability_fingerprint_slots(
                slots or {},
                intent_name=intent_name,
                organization_id=organization_id,
                luma_response=effective_response,
                session_state=session_state,
            )
            if slots
            else {}
        )
        current_fp = (
            compute_availability_fingerprint(fp_slots, intent_name=intent_name)
            if fp_slots
            else None
        )
        logger.debug(
            f"[FINALIZATION_DECISION] BEFORE PLAN_FINAL (after all overrides): "
            f"intent={intent_name}, "
            f"plan.status={populated_plan.get('status')}, plan.stage={populated_plan.get('stage')}, plan.action={populated_plan.get('action')}, "
            f"session.intent_name={session_state.get('intent_name') if session_state else None}, "
            f"session.status={session_state.get('status') if session_state else None}, "
            f"session.stage={session_state.get('stage') if session_state else None}, "
            f"session.action={session_state.get('action') if session_state else None}, "
            f"availability_fingerprint_stored={stored_fp}, "
            f"availability_fingerprint_current={current_fp}, "
            f"last_execution_result={last_exec_result}, "
            f"slots service_id={slots.get('service_id') if isinstance(slots, dict) else None}, "
            f"date={slots.get('date') if isinstance(slots, dict) else None}, "
            f"time={slots.get('time') if isinstance(slots, dict) else None}, "
            f"org_id={fp_slots.get('organization_id') if isinstance(fp_slots, dict) else None}"
        )

        # GUARD LOG: Final plan values before return
        logger.debug(
            "[PLAN_FINAL] stage=%s action=%s missing=%s slots=%s",
            populated_plan["stage"],
            populated_plan["action"],
            populated_plan["missing_slots"],
            populated_plan["slots"],
        )

        # Capability runner invocation lives in core.orchestration.api.capability_boundary
        # (message.py API boundary). Planning only emits AWAITING_CAPABILITY.

        # ASSERTION: plan.intent_name must never be empty after successful planning
        if (
            not populated_plan.get("intent_name")
            or populated_plan.get("intent_name") == ""
        ):
            logger.error(
                "[PLANNING_ASSERTION] CRITICAL: plan.intent_name is empty after planning! "
                "intent_name=%r, plan=%s, outcome.intent_name=%s",
                intent_name,
                json.dumps(populated_plan, default=str, ensure_ascii=True),
                result.get("outcome", {}).get("intent_name"),
            )
            # Fail-safe: Use intent_name from variable if plan doesn't have it
            if intent_name and intent_name not in ("", "UNKNOWN"):
                populated_plan["intent_name"] = intent_name
                result["outcome"]["intent_name"] = intent_name
                result["outcome"]["plan"]["intent_name"] = intent_name
                logger.error(
                    "[PLANNING_ASSERTION] Recovered: Set plan.intent_name=%s from resolved intent_name",
                    intent_name,
                )
            else:
                logger.error(
                    "[PLANNING_ASSERTION] FAILED: Cannot recover - intent_name is empty/invalid: %r",
                    intent_name,
                )
        else:
            # Log success to confirm intent_name is present
            logger.debug(
                "[PLANNING_ASSERTION] SUCCESS: plan.intent_name=%s is present after planning",
                populated_plan.get("intent_name"),
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
        logger.info(
            "[OUTCOME] user_id=%s intent=%s stage=%s action=%s missing_slots=%s slots=%s",
            user_id,
            intent_name,
            outcome.get("stage"),
            outcome.get("action"),
            outcome_missing_slots,
            json.dumps(outcome_slots, default=str, ensure_ascii=True),
        )

        # Inject system rendering text (greeting/welcome) BEFORE clarification injection
        # Only if NOT NEEDS_CLARIFICATION, NOT AWAITING_CAPABILITY, NOT EXECUTED
        if plan_status not in (
            "NEEDS_CLARIFICATION",
            "AWAITING_CAPABILITY",
            "EXECUTED",
        ):
            _inject_system_text(result, decision)

        # Inject rendering text for clarification states (ONLY for NEEDS_CLARIFICATION)
        if plan_status == "NEEDS_CLARIFICATION":
            _inject_rendering_text(result, decision, session_state)

        # Do NOT return early for AWAITING_* statuses - let the handler below process them
        if plan_status not in ("AWAITING_CONFIRMATION", "AWAITING_CAPABILITY"):
            return result

    # Handle AWAITING_* statuses (AWAITING_CONFIRMATION, AWAITING_CAPABILITY, etc.)
    # Generic handler that mirrors plan status and awaiting without special-casing
    if plan_status in ("AWAITING_CONFIRMATION", "AWAITING_CAPABILITY"):
        # Build outcome from decision using unified helper
        outcome_dict = build_outcome_from_decision(decision)

        # Override status and awaiting to mirror plan (AWAITING_* statuses are special)
        outcome_dict["status"] = plan_status
        outcome_dict["awaiting"] = awaiting

        # Include _raw_luma_response in facts for test snapshots (preserved from effective_response)
        if effective_response and "_raw_luma_response" in effective_response:
            facts = outcome_dict.get("facts", {})
            if not isinstance(facts, dict):
                facts = {}
            facts["_raw_luma_response"] = effective_response["_raw_luma_response"]
            outcome_dict["facts"] = facts

        # Add booking for AWAITING_CONFIRMATION (backward compatibility)
        facts = outcome_dict.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}

        confirmation_text = None
        if plan_status == "AWAITING_CONFIRMATION":
            booking = decision.get("booking", {})
            outcome_dict["booking"] = booking
            from core.rendering.booking_confirmation_renderer import (
                prefix_with_revision_acknowledgement,
                render_booking_confirmation_prompt,
            )

            confirm_slots = outcome_dict.get("slots") or facts.get("slots", {})
            if isinstance(confirm_slots, dict):
                confirmation_text = render_booking_confirmation_prompt(
                    confirm_slots)
                revision_summary = None
                if isinstance(effective_response, dict):
                    revision_summary = effective_response.get(
                        "_revision_summary")
                confirmation_text = prefix_with_revision_acknowledgement(
                    confirmation_text, revision_summary
                )

        # Add active_capability for AWAITING_CAPABILITY
        # STRICTLY resolve from decision.plan.active_capability (authoritative source from capability gating)
        # Capability gating sets decision["plan"]["active_capability"] = "payment" (line 2808)
        # Do NOT depend on session_state (may be None on first turn) or facts
        active_capability = None
        if plan_status == "AWAITING_CAPABILITY":
            # Capability gating ALWAYS injects decision.plan.active_capability when setting AWAITING_CAPABILITY
            # This is the single source of truth for active capability
            plan = decision.get("plan", {})
            if isinstance(plan, dict):
                active_capability = plan.get("active_capability")
            if active_capability:
                outcome_dict["active_capability"] = active_capability

        result = {"success": True, "outcome": outcome_dict}
        if confirmation_text:
            result["text"] = confirmation_text
        # Store effective Luma response for session building
        result["_merged_luma_response"] = effective_response

        # Store decision for plan_message to access
        result["_decision"] = decision

        # AWAITING_* statuses must never render clarification text at top-level
        # Capability/awaiting UI owns the text for these statuses
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
                if "intent_name" not in result["outcome"] or not result["outcome"].get(
                    "intent_name"
                ):
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
                                    "_effective_intent", ""
                                )
                    # If still empty, preserve session intent
                    if not resolved_intent and session_state:
                        resolved_intent = session_state.get(
                            "intent_name"
                        ) or session_state.get("intent")

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

            # Inject rendering text at top level for clarification states
            _inject_rendering_text(result, decision, session_state)

            result["_merged_luma_response"] = effective_response
            return result
        if "error" in decision:
            return {
                "success": False,
                "error": decision["error"],
                "message": decision.get("message", "An error occurred"),
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
        assert isinstance(
            missing_slots, list
        ), f"missing_slots must be a list, got {type(missing_slots)}: {missing_slots}"

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
        from core.orchestration.nlu.luma_response_processor import (
            _normalize_modify_booking_missing_slots,
        )

        missing_slots = _normalize_modify_booking_missing_slots(
            missing_slots, effective_response
        )

        # INVARIANT CHECK: After normalization, missing_slots must still be a list
        assert isinstance(
            missing_slots, list
        ), f"missing_slots must be a list after normalization, got {type(missing_slots)}: {missing_slots}"

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
        from core.orchestration.nlu.luma_response_processor import (
            _derive_clarification_reason_from_missing_slots,
        )

        # Derive clarification reason from missing slots
        clarification_reason = _derive_clarification_reason_from_missing_slots(
            missing_slots
        )

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
            facts=facts,
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
                        "intent_name"
                    ) or session_state.get("intent")
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

        # Inject rendering text at top level for clarification states
        _inject_rendering_text(result, decision, session_state)

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
            status="READY",
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
                "plan": plan,
            },
        }

        # Promote stage/action/missing_slots/slots to top-level outcome (required by tests)
        # Tests assert outcome.stage / outcome.action / outcome.missing_slots / outcome.slots directly
        # Always copy stage/action from plan, even if None
        result["outcome"]["stage"] = stage
        result["outcome"]["action"] = action
        result["outcome"]["missing_slots"] = missing_slots
        result["outcome"]["slots"] = slots

        # READY must remain silent per phase 1 contract (no clarification rendering here)

        # Store effective Luma response for session building
        result["_merged_luma_response"] = effective_response
        return result

    # This should never be reached - all statuses handled above
    logger.error(f"Unexpected plan_status: {plan_status} for user {user_id}")
    return {
        "success": False,
        "error": "internal_error",
        "message": f"Unexpected plan status: {plan_status}",
    }
