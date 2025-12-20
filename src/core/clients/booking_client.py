"""
Booking API Client

Thin HTTP client for calling booking internal API.
"""

from typing import Dict, Any, Optional, Literal, List

from core.clients.base_client import BaseClient


class BookingClient(BaseClient):
    """HTTP client for booking internal API."""

    def __init__(self, base_url: Optional[str] = None):
        """
        Initialize booking client.

        Args:
            base_url: API base URL. Defaults to INTERNAL_API_BASE_URL env var.
        """
        super().__init__(
            base_url=base_url,
            env_var="INTERNAL_API_BASE_URL",
            default_url="http://localhost:3000"
        )

    def create_booking(
        self,
        organization_id: int,
        customer_id: int,
        booking_type: Literal["service", "room", "lodging"],
        item_id: int,
        *,
        # Service booking parameters
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        staff_id: Optional[int] = None,
        addons: Optional[List[Dict[str, Any]]] = None,
        # Room booking parameters
        check_in: Optional[str] = None,
        check_out: Optional[str] = None,
        guests: int = 1,
        extras: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new booking.

        Args:
            organization_id: Organization identifier
            customer_id: Customer identifier
            booking_type: Type of booking ("service", "room", or "lodging")
            item_id: Service or room item identifier
            start_time: Service booking start time (ISO-8601 with timezone)
            end_time: Service booking end time (ISO-8601 with timezone)
            staff_id: Staff member ID for service bookings (optional)
            addons: Service booking addons (optional)
            check_in: Room booking check-in time (ISO-8601 with timezone)
            check_out: Room booking check-out time (ISO-8601 with timezone)
            guests: Number of guests for room bookings (default: 1)
            extras: Room booking extras (optional)

        Returns:
            Created booking data as dict

        Raises:
            UpstreamError: On network failures or HTTP errors
            ValueError: If required parameters are missing for the booking type
        """
        if booking_type == "service":
            if not start_time or not end_time:
                raise ValueError(
                    "start_time and end_time are required for service bookings")
            payload = {
                "organization_id": organization_id,
                "customer_id": customer_id,
                "booking_type": "service",
                "item_id": item_id,
                "start_time": start_time,
                "end_time": end_time,
            }
            if staff_id is not None:
                payload["staff_id"] = staff_id
            if addons:
                payload["addons"] = addons
        elif booking_type in ("room", "lodging"):
            if not check_in or not check_out:
                raise ValueError(
                    "check_in and check_out are required for room bookings")
            payload = {
                "organization_id": organization_id,
                "customer_id": customer_id,
                "booking_type": booking_type,
                "item_id": item_id,
                "check_in": check_in,
                "check_out": check_out,
                "guests": guests,
            }
            if extras:
                payload["extras"] = extras
        else:
            raise ValueError(f"Invalid booking_type: {booking_type}")

        path = "/api/internal/bookings"
        # Debug: Log payload before sending
        import logging
        logger = logging.getLogger(__name__)
        logger.debug("Creating booking with payload: %s", payload)
        return self._request("POST", path, json=payload)

    def get_booking(self, booking_code: str) -> Dict[str, Any]:
        """
        Get booking by booking code.

        Args:
            booking_code: Booking code identifier

        Returns:
            Booking data as dict

        Raises:
            UpstreamError: On network failures or HTTP errors
        """
        path = f"/api/internal/bookings/{booking_code}"
        return self._request("GET", path)

    def cancel_booking(
        self,
        booking_code: str,
        organization_id: int,
        cancellation_type: Literal["cancelled", "no_show", "rescheduled", "user_initiated"],
        *,
        reason: Optional[str] = None,
        notes: Optional[str] = None,
        refund_method: Optional[str] = None,
        notify_customer: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Cancel a booking.

        Args:
            booking_code: Booking code identifier
            organization_id: Organization identifier
            cancellation_type: Type of cancellation
            reason: Cancellation reason (optional)
            notes: Additional notes (optional)
            refund_method: Refund method (optional)
            notify_customer: Whether to notify customer (optional)

        Returns:
            Cancellation result as dict

        Raises:
            UpstreamError: On network failures or HTTP errors
        """
        payload = {
            "organization_id": organization_id,
            "cancellation_type": cancellation_type,
        }
        if reason is not None:
            payload["reason"] = reason
        if notes is not None:
            payload["notes"] = notes
        if refund_method is not None:
            payload["refundMethod"] = refund_method
        if notify_customer is not None:
            payload["notifyCustomer"] = notify_customer

        path = f"/api/internal/bookings/{booking_code}/cancel"
        return self._request("POST", path, json=payload)
