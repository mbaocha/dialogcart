"""
Orchestration Layer

Control flow and decision making for conversation handling.

This module orchestrates the conversation flow by:
- Handling message entry
- Deriving org_id and domain
- Constructing catalog and tenant_context
- Calling Luma API
- Validating contracts
- Branching on needs_clarification
- Deciding outcome types (CLARIFY, BOOKING_CREATED, etc.)
- Calling business execution functions

Constraints:
- No copy, no templates, no WhatsApp formatting
- Must only return structured outcomes
"""

import logging
import json
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from core.orchestration.clients.luma_client import LumaClient
from core.orchestration.contracts.luma_contracts import assert_luma_contract
from core.orchestration.errors import ContractViolation, UpstreamError, UnsupportedIntentError
from core.orchestration.luma_response_processor import (
    process_luma_response,
    build_clarify_outcome_from_reason
)
from core.orchestration.clients.booking_client import BookingClient
from core.orchestration.clients.customer_client import CustomerClient
from core.orchestration.clients.catalog_client import CatalogClient
from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.cache.catalog_cache import catalog_cache
from core.orchestration.cache.org_domain_cache import org_domain_cache

logger = logging.getLogger(__name__)


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
    booking_client: Optional[BookingClient] = None,
    customer_client: Optional[CustomerClient] = None,
    catalog_client: Optional[CatalogClient] = None,
    organization_client: Optional[OrganizationClient] = None
) -> Dict[str, Any]:
    """
    Handle a user message - stateless orchestration.

    Flow:
    1. Call Luma (LumaClient.resolve)
    2. Assert response contract (assert_luma_contract)
    3. If success=false return {success:false, error:...}
    4. If needs_clarification=true return clarification payload with template_key + data
    5. Else (resolved) execute business flow:
       a. Get organization details
       b. Get or create customer
       c. Create booking
    6. Return {success:true, outcome:{type:"BOOKING_CREATED"|"CLARIFY", ...}}

    Args:
        user_id: User identifier
        text: User message text
        domain: Domain (default: "service")
        timezone: Timezone (default: "UTC")
        phone_number: Customer phone number (optional, for customer lookup/creation)
        email: Customer email (optional, for customer lookup/creation)
        customer_id: Customer ID (optional, if provided skips lookup/creation)
        organization_id: Organization ID (optional, defaults to ORG_ID env or 1) 
        luma_client: Luma client instance (creates default if None)
        booking_client: Booking client instance (creates default if None)
        customer_client: Customer client instance (creates default if None)
        catalog_client: Catalog discovery client (creates default if None) 

    Returns:
        Response dictionary with success and outcome
    """
    # Initialize default clients if not provided
    if luma_client is None:
        luma_client = LumaClient()
    if booking_client is None:
        booking_client = BookingClient()
    if customer_client is None:
        customer_client = CustomerClient()
    if catalog_client is None:
        catalog_client = CatalogClient()
    if organization_client is None:
        organization_client = OrganizationClient()

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
        if alias_map:
            tenant_context = {"aliases": alias_map}

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

    # Log sentence passed to Luma
    print(f"\n[LUMA REQUEST]")
    print(f"  Sentence: {text}")
    print(
        f"  Full payload: {json.dumps(luma_payload, indent=2, ensure_ascii=False)}")
    logger.info("Luma request payload: %s", json.dumps(
        luma_payload, ensure_ascii=False))

    try:
        luma_response = luma_client.resolve(
            user_id=user_id,
            text=text,
            domain=derived_domain,
            timezone=timezone,
            tenant_context=tenant_context
        )

        # Log raw Luma API response
        print(f"\n[LUMA RESPONSE]")
        print(
            f"  Raw response: {json.dumps(luma_response, indent=2, ensure_ascii=False)}")
        logger.info("Luma response: %s", json.dumps(
            luma_response, ensure_ascii=False))

    except UpstreamError as e:
        logger.error(f"Luma API error for user {user_id}: {str(e)}")
        return {
            "success": False,
            "error": "upstream_error",
            "message": str(e)
        }

    # Step 2: Assert contract
    try:
        assert_luma_contract(luma_response)
    except ContractViolation as e:
        logger.error(f"Contract violation for user {user_id}: {str(e)}")
        return {
            "success": False,
            "error": "contract_violation",
            "message": str(e)
        }

    # Step 3: If success=false return error
    if not luma_response.get("success", False):
        error_msg = luma_response.get("error", "Unknown error from Luma")
        logger.warning(
            f"Luma returned success=false for user {user_id}: {error_msg}")
        return {
            "success": False,
            "error": "luma_error",
            "message": error_msg
        }

    # Step 4: Process Luma response (interpret and decide CLARIFY vs EXECUTE)
    decision = process_luma_response(luma_response, derived_domain, user_id)

    if decision["type"] == "CLARIFY":
        return decision["outcome"]

    if decision["type"] == "ERROR":
        return {
            "success": False,
            "error": decision["error"],
            "message": decision["message"]
        }

    # Step 5: Execute business flow (decision["type"] == "EXECUTE")
    action_name = decision["action_name"]
    booking = decision["booking"]
    booking_type = booking.get("booking_type", "service")

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
                resolution = _resolve_service_id(booking.get(
                    "services", []), catalog_services_for_resolution)
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

            return {
                "success": True,
                "outcome": {
                    "type": "BOOKING_CREATED",
                    "booking_code": booking_code_extracted,
                    "status": status_extracted,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "total_amount": total_amount,
                    "reservation_fee": reservation_fee,
                    "booking_type": booking_type_resp,
                }
            }

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

            api_response = booking_client.update_booking(
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

            return {
                "success": True,
                "outcome": {
                    "type": "BOOKING_MODIFIED",
                    "booking_code": booking_code,
                    "status": status_extracted,
                    "booking": booking_data,
                },
            }

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

            api_response = booking_client.cancel_booking(
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
            return {
                "success": True,
                "outcome": {
                    "type": "BOOKING_CANCELLED",
                    "booking_code": booking_code,
                    "status": api_response.get("status", "cancelled")
                }
            }
        elif action_name == "booking.inquiry":
            booking_code = booking.get("booking_code") or booking.get("code")
            if not booking_code:
                raise ValueError(
                    "booking_code is required for booking inquiry")

            api_response = booking_client.get_booking(booking_code)
            booking_data = api_response.get("booking")
            if isinstance(api_response.get("data"), dict) and isinstance(api_response["data"].get("booking"), dict):
                booking_data = api_response["data"]["booking"]

            return {
                "success": True,
                "outcome": {
                    "type": "BOOKING_INQUIRY",
                    "booking_code": booking_code,
                    "booking": booking_data or api_response,
                },
            }
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


def _execute_booking_creation(
    user_id: str,
    booking: Dict[str, Any],
    customer_client: CustomerClient,
    booking_client: BookingClient,
    catalog_client: CatalogClient,
    organization_id: int,
    catalog_data: Optional[Dict[str, Any]] = None,
    phone_number: Optional[str] = None,
    email: Optional[str] = None,
    customer_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Execute booking creation flow.

    Flow:
    1. Get organization details
    2. Get or create customer
    3. Create booking

    Args:
        user_id: User identifier
        booking: Booking payload from Luma
        customer_client: Customer client instance
        booking_client: Booking client instance
        catalog_client: Catalog discovery client instance 
        organization_id: Organization identifier (from env by default) 
        catalog_data: Optional pre-fetched catalog data (to avoid re-fetch) 
        phone_number: Customer phone number from context (optional, used as fallback)
        email: Customer email from context (optional, used as fallback)
        customer_id: Customer ID (optional, if provided skips lookup/creation)

    Returns:
        Created booking response from API

    Raises:
        ValueError: If required fields are missing
        UpstreamError: On API failures
    """
    # Step 1: Fetch catalog via catalog cache (read-only discovery)
    # TODO: Replace env-based organization_id with channel/auth-derived organization_id
    if catalog_data is None:
        catalog_data = catalog_cache.get_catalog(
            organization_id, catalog_client, domain=booking.get("booking_type", "service"))
    catalog_services = catalog_data.get(
        "services", []) if isinstance(catalog_data, dict) else []
    rooms_catalog = catalog_data.get(
        "rooms", []) if isinstance(catalog_data, dict) else []

    # Step 2: Get or create customer (skip if customer_id provided)
    logger.info(
        f"DEBUG: customer_id={customer_id}, phone_number={phone_number}, email={email}")
    if customer_id:
        # Use provided customer_id directly
        logger.info(f"Using provided customer_id: {customer_id}")
        customer = {"customer_id": customer_id, "id": customer_id}
    else:
        # Extract customer info from booking payload
        customer_email = booking.get("customer", {}).get(
            "email") if isinstance(booking.get("customer"), dict) else None
        customer_phone = booking.get("customer", {}).get(
            "phone") if isinstance(booking.get("customer"), dict) else None

        # If customer info not in booking, try to extract from top-level booking fields
        if not customer_email:
            customer_email = booking.get("email")
        if not customer_phone:
            customer_phone = booking.get("phone")

        # FALLBACK: Use phone_number/email from context if not in booking payload
        if not customer_phone and phone_number:
            customer_phone = phone_number
        if not customer_email and email:
            customer_email = email

        customer = None
        if customer_email or customer_phone:
            customer = customer_client.get_customer(
                organization_id=organization_id,
                email=customer_email,
                phone=customer_phone
            )

        # Create customer if not found
        if customer is None:
            if not customer_email and not customer_phone:
                raise ValueError(
                    "customer_id, email, or phone is required. If customer_id is provided, email/phone are not needed.")

            customer = customer_client.create_customer(
                organization_id=organization_id,
                name="Guest",  # Placeholder name, can be updated later
                email=customer_email,
                phone=customer_phone
            )

    customer_id = customer.get("customer_id") or customer.get("id")
    if not customer_id:
        raise ValueError("customer_id not found in customer response")

    # Step 3: Create booking
    # Extract booking fields from Luma booking payload
    logger.info(f"Luma booking payload: {booking}")
    booking_type = booking.get("booking_type", "service")

    # Extract service/item information
    services = booking.get("services", [])
    item_id = None
    service_canonical = None
    room_type_id = booking.get("_resolved_room_type_id")
    resolved_extras = booking.get("_resolved_extras")

    if services:
        # Take first service if multiple provided
        first_service = services[0] if isinstance(services, list) else services
        if isinstance(first_service, dict):
            # Expect item_id resolved earlier; fall back to canonical/text
            item_id = first_service.get(
                "id") or booking.get("_resolved_item_id")
            service_canonical = first_service.get(
                "canonical") or first_service.get("text")

    # If service booking and still missing item_id, fail-safe clarification should have already occurred.
    # Retain guardrail error for non-service flows.
    if booking_type == "service" and not item_id:
        raise ValueError(
            f"item_id is required for booking creation. Service '{service_canonical}' could not be resolved.")

    # Convert item_id to int if it's a string that looks like a number
    # The API requires item_id to be a positive integer
    if item_id is not None:
        original_item_id = item_id
        try:
            item_id = int(item_id)
            if item_id <= 0:
                raise ValueError(
                    f"item_id must be a positive integer, got: {item_id}")
        except (ValueError, TypeError) as e:
            logger.error(
                f"item_id '{original_item_id}' is not a valid positive integer: {e}")
            raise ValueError(
                f"item_id must be a positive integer, but got: {original_item_id} (type: {type(original_item_id).__name__})")

    # Extract datetime_range
    datetime_range = booking.get("datetime_range")

    if booking_type == "service":
        # Service booking
        if not datetime_range or not isinstance(datetime_range, dict):
            raise ValueError("datetime_range is required for service bookings")

        start_time = datetime_range.get("start")
        if not start_time:
            raise ValueError("start time is required for service bookings")

        # Compute end_time from catalog duration (ignore incoming end_time)
        matched_service = None
        for svc in catalog_services:
            if isinstance(svc, dict) and svc.get("id") == item_id:
                matched_service = svc
                break

        duration_minutes = None
        if matched_service is not None:
            duration_val = matched_service.get("duration")
            if duration_val is not None:
                try:
                    duration_minutes = int(duration_val)
                except Exception:
                    duration_minutes = None

        if duration_minutes is None:
            raise ValueError(
                "duration is required for service bookings and was not found in catalog")

        try:
            start_dt = datetime.fromisoformat(start_time)
        except Exception as e:
            raise ValueError(f"Invalid start_time format: {start_time}") from e

        end_dt = start_dt + timedelta(minutes=duration_minutes)
        end_time = end_dt.isoformat()

        # Extract optional service booking fields
        staff_id = booking.get("staff_id")
        addons = booking.get("addons")

        logger.info(
            f"Creating service booking: org_id={organization_id}, customer_id={customer_id}, item_id={item_id}, start={start_time}, end={end_time}, duration={duration_minutes}m")

        return booking_client.create_booking(
            organization_id=organization_id,
            customer_id=customer_id,
            booking_type="service",
            item_id=item_id,
            start_time=start_time,
            end_time=end_time,
            staff_id=staff_id,
            addons=addons
        )
    elif booking_type == "reservation":
        # Reservation booking
        if not datetime_range or not isinstance(datetime_range, dict):
            raise ValueError(
                "datetime_range is required for reservation bookings")

        check_in = datetime_range.get("start")
        check_out = datetime_range.get("end")
        if not check_in or not check_out:
            raise ValueError(
                "check_in and check_out are required for reservation bookings")

        # Extract optional reservation booking fields
        guests = booking.get("guests", 1)
        extras = resolved_extras if resolved_extras is not None else booking.get(
            "extras")

        return booking_client.create_booking(
            organization_id=organization_id,
            customer_id=customer_id,
            booking_type="reservation",
            item_id=room_type_id or item_id,
            check_in=check_in,
            check_out=check_out,
            guests=guests,
            extras=extras
        )
    else:
        raise ValueError(f"Unsupported booking_type: {booking_type}")
