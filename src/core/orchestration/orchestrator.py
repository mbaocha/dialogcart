"""
Stateless Conversation Orchestrator

Pure stateless function that orchestrates conversation flow.
No persistence, no memory, no NLP logic.
"""

import logging
from typing import Dict, Any, Optional

from core.clients.luma_client import LumaClient
from core.contracts.luma_contracts import assert_luma_contract
from core.errors.exceptions import ContractViolation, UpstreamError, UnsupportedIntentError
from core.orchestration.router import get_template_key, get_action_name
from core.clients.booking_client import BookingClient
from core.clients.organization_client import OrganizationClient
from core.clients.customer_client import CustomerClient

logger = logging.getLogger(__name__)


def handle_message(
    user_id: str,
    text: str,
    domain: str = "service",
    timezone: str = "UTC",
    phone_number: Optional[str] = None,
    email: Optional[str] = None,
    customer_id: Optional[int] = None,
    luma_client: Optional[LumaClient] = None,
    booking_client: Optional[BookingClient] = None,
    organization_client: Optional[OrganizationClient] = None,
    customer_client: Optional[CustomerClient] = None
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
        luma_client: Luma client instance (creates default if None)
        booking_client: Booking client instance (creates default if None)
        organization_client: Organization client instance (creates default if None)
        customer_client: Customer client instance (creates default if None)

    Returns:
        Response dictionary with success and outcome
    """
    # Initialize default clients if not provided
    if luma_client is None:
        luma_client = LumaClient()
    if booking_client is None:
        booking_client = BookingClient()
    if organization_client is None:
        organization_client = OrganizationClient()
    if customer_client is None:
        customer_client = CustomerClient()

    # Step 1: Call Luma
    try:
        luma_response = luma_client.resolve(
            user_id=user_id,
            text=text,
            domain=domain,
            timezone=timezone
        )
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

    # Step 4: If needs_clarification=true return clarification payload
    if luma_response.get("needs_clarification", False):
        clarification = luma_response.get("clarification", {})
        reason = clarification.get("reason", "")
        template_key = get_template_key(reason, domain)

        logger.info(
            f"Clarification needed for user {user_id}: {reason} -> {template_key}"
        )

        return {
            "success": True,
            "outcome": {
                "type": "CLARIFY",
                "template_key": template_key,
                "data": clarification.get("data", {}),
                "booking": luma_response.get("booking")
            }
        }

    # Step 5: Else (resolved) execute business flow
    intent = luma_response.get("intent", {})
    intent_name = intent.get("name", "")
    action_name = get_action_name(intent_name)

    if not action_name:
        logger.warning(f"Unsupported intent for user {user_id}: {intent_name}")
        return {
            "success": False,
            "error": "unsupported_intent",
            "message": f"Intent {intent_name} is not supported"
        }

    booking = luma_response.get("booking", {})

    try:
        if action_name == "booking.create":
            # Execute full booking creation flow
            result = _execute_booking_creation(
                user_id=user_id,
                booking=booking,
                organization_client=organization_client,
                customer_client=customer_client,
                booking_client=booking_client,
                phone_number=phone_number,
                email=email,
                customer_id=customer_id
            )

            logger.info(f"Successfully created booking for user {user_id}")
            return {
                "success": True,
                "outcome": {
                    "type": "BOOKING_CREATED",
                    "booking_code": result.get("booking_code") or result.get("code"),
                    "status": result.get("status", "pending")
                }
            }

        elif action_name == "booking.modify":
            # MODIFY_BOOKING intent is not supported (no endpoint exists)
            raise UnsupportedIntentError(
                "MODIFY_BOOKING intent is not supported")

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

            # TODO: For testing - hardcode organization_id. Should be passed as parameter or derived from context.
            organization_id = 1

            api_response = booking_client.cancel_booking(
                booking_code=booking_code,
                organization_id=organization_id,
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
    organization_client: OrganizationClient,
    customer_client: CustomerClient,
    booking_client: BookingClient,
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
        organization_client: Organization client instance
        customer_client: Customer client instance
        booking_client: Booking client instance
        phone_number: Customer phone number from context (optional, used as fallback)
        email: Customer email from context (optional, used as fallback)
        customer_id: Customer ID (optional, if provided skips lookup/creation)

    Returns:
        Created booking response from API

    Raises:
        ValueError: If required fields are missing
        UpstreamError: On API failures
    """
    # TODO: For testing - hardcode organization_id. Should be passed as parameter or derived from context.
    organization_id = 1

    # Step 1: Get organization details (includes catalog for service lookup)
    org_details = organization_client.get_details(organization_id)
    catalog = org_details.get("catalog", {})
    # Catalog structure: { services: [...], room_types: [...], extras: [...] }
    catalog_services = catalog.get(
        "services", []) if isinstance(catalog, dict) else []
    catalog_room_types = catalog.get(
        "room_types", []) if isinstance(catalog, dict) else []

    logger.info(
        f"Catalog structure: services={len(catalog_services)}, room_types={len(catalog_room_types)}")
    if catalog_services:
        logger.debug(
            f"Sample services: {[s.get('name') for s in catalog_services[:3]]}")

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

    if services:
        # Take first service if multiple provided
        first_service = services[0] if isinstance(services, list) else services
        if isinstance(first_service, dict):
            # Try to get numeric ID first, fallback to canonical/text
            item_id = first_service.get("id")
            service_canonical = first_service.get(
                "canonical") or first_service.get("text")

    # If item_id is not numeric, try to look it up in the catalog by canonical name
    if item_id is None and service_canonical:
        logger.info(f"Looking up service '{service_canonical}' in catalog")
        # Use appropriate catalog based on booking type
        catalog_items = catalog_services if booking_type == "service" else catalog_room_types

        for item in catalog_items:
            # Check if item matches by canonical, name, or slug
            item_canonical = item.get("canonical") or item.get(
                "slug") or item.get("name", "").lower().replace(" ", "_")
            item_name = item.get("name", "").lower()
            service_canonical_lower = service_canonical.lower()

            # Try multiple matching strategies
            if (item_canonical == service_canonical or
                item_canonical == service_canonical_lower or
                item_name == service_canonical_lower or
                item.get("canonical", "").endswith(service_canonical) or
                service_canonical.endswith(item_canonical) or
                    item_name.replace(" ", "_") == service_canonical_lower):
                item_id = item.get("id")
                logger.info(
                    f"Found service '{service_canonical}' with id={item_id} (name: {item.get('name')})")
                break

        if item_id is None:
            available_names = [item.get("name") for item in catalog_items[:5]]
            logger.warning(
                f"Service '{service_canonical}' not found in catalog. Available {booking_type} items: {available_names}")

    if not item_id:
        raise ValueError(
            f"item_id is required for booking creation. Service '{service_canonical}' not found in catalog or no numeric ID provided.")

    # Convert item_id to int if it's a string that looks like a number
    # The API requires item_id to be a positive integer
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
        end_time = datetime_range.get("end")
        if not start_time or not end_time:
            raise ValueError(
                "start and end times are required for service bookings")

        # Extract optional service booking fields
        staff_id = booking.get("staff_id")
        addons = booking.get("addons")

        logger.info(
            f"Creating service booking: org_id={organization_id}, customer_id={customer_id}, item_id={item_id}, start={start_time}, end={end_time}")

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
    elif booking_type in ("room", "lodging"):
        # Room booking
        if not datetime_range or not isinstance(datetime_range, dict):
            raise ValueError("datetime_range is required for room bookings")

        check_in = datetime_range.get("start")
        check_out = datetime_range.get("end")
        if not check_in or not check_out:
            raise ValueError(
                "check_in and check_out are required for room bookings")

        # Extract optional room booking fields
        guests = booking.get("guests", 1)
        extras = booking.get("extras")

        return booking_client.create_booking(
            organization_id=organization_id,
            customer_id=customer_id,
            booking_type=booking_type,
            item_id=item_id,
            check_in=check_in,
            check_out=check_out,
            guests=guests,
            extras=extras
        )
    else:
        raise ValueError(f"Unsupported booking_type: {booking_type}")
