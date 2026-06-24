"""Mock booking and availability clients wired to core.tests.mocks."""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from unittest.mock import Mock

from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.execution.clients.availability_client import AvailabilityClient
from core.orchestration.execution.clients.booking_client import BookingClient
from core.tests.mocks import (
    mock_cancel_booking,
    mock_confirm_booking,
    mock_create_booking,
    mock_get_service_availability,
)


def create_mock_availability_client(
    frozen_time: Optional[datetime] = None,
) -> Mock:
    """Mock AvailabilityClient using tests.mocks.mock_get_service_availability."""
    mock_availability_client = Mock(spec=AvailabilityClient)

    def mock_get_availability(
        organization_id=None,
        service_id=None,
        date=None,
        time_constraint=None,
        extra_params=None,
        **kwargs,
    ):
        service_id_int = 1
        if service_id is not None:
            if isinstance(service_id, str) and service_id.isdigit():
                service_id_int = int(service_id)
            elif isinstance(service_id, int):
                service_id_int = service_id

        resolved_date = date
        if date and isinstance(date, str):
            date_lower = date.lower().strip()
            if date_lower == "tomorrow":
                if frozen_time:
                    resolved_date = (frozen_time + timedelta(days=1)).strftime(
                        "%Y-%m-%d"
                    )
                else:
                    resolved_date = "2026-01-16"
            elif date_lower in ("today", "now"):
                if frozen_time:
                    resolved_date = frozen_time.strftime("%Y-%m-%d")
                else:
                    resolved_date = "2026-01-15"
            elif (
                len(date_lower) == 10 and date_lower[4] == "-" and date_lower[7] == "-"
            ):
                resolved_date = date
            else:
                if frozen_time:
                    resolved_date = (frozen_time + timedelta(days=1)).strftime(
                        "%Y-%m-%d"
                    )
                else:
                    resolved_date = "2026-01-16"

        if not resolved_date:
            if frozen_time:
                resolved_date = (frozen_time + timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                resolved_date = "2026-01-16"

        return mock_get_service_availability(
            organization_id=organization_id or 1,
            service_id=service_id_int,
            date=resolved_date,
            time_constraint=time_constraint,
            extra_params=extra_params,
            **kwargs,
        )

    mock_availability_client.get_service_availability.side_effect = mock_get_availability
    return mock_availability_client


def create_mock_booking_client() -> Mock:
    """Stateful mock BookingClient backed by core.tests.mocks."""
    mock_booking_client = Mock(spec=BookingClient)
    created_bookings: Dict[str, Dict[str, Any]] = {}
    most_recent_booking: Optional[Dict[str, Any]] = None

    def mock_booking(
        organization_id=None,
        customer_id=None,
        booking_type=None,
        item_id=None,
        start_time=None,
        end_time=None,
        **kwargs,
    ):
        return mock_create_booking(
            organization_id=organization_id or 1,
            customer_id=customer_id or 1,
            booking_type=booking_type or "service",
            item_id=item_id or 1,
            start_time=start_time,
            end_time=end_time,
            **kwargs,
        )

    def get_booking_wrapper(booking_id: str, **kwargs):
        if booking_id in created_bookings:
            return created_bookings[booking_id]
        if most_recent_booking:
            return most_recent_booking
        return {
            "id": booking_id,
            "booking_id": booking_id,
            "booking_code": booking_id,
            "status": "confirmed",
            "service_id": 1,
            "item_id": 1,
        }

    def get_most_recent_booking_wrapper(
        organization_id=None, customer_id=None, **kwargs
    ):
        return most_recent_booking

    def create_booking_wrapper(*args, **kwargs):
        nonlocal most_recent_booking
        result = mock_booking(*args, **kwargs)
        if isinstance(result, dict):
            booking_obj = (
                result.get("booking")
                if isinstance(result.get("booking"), dict)
                else result
            )
            booking_code = booking_obj.get("booking_code") or booking_obj.get("id")
            booking_id = booking_obj.get("booking_id") or booking_obj.get("id")
            key = str(booking_code or booking_id or "")
            if key:
                created_bookings[key] = result
                most_recent_booking = result
        return result

    def cancel_booking_wrapper(booking_code: str, **kwargs):
        if booking_code in created_bookings:
            created_bookings[booking_code]["status"] = "cancelled"
        return mock_cancel_booking(booking_code=booking_code, **kwargs)

    def confirm_booking_wrapper(booking_code: str, organization_id=None, **kwargs):
        return mock_confirm_booking(
            booking_code=booking_code, organization_id=organization_id or 1, **kwargs
        )

    def update_booking_wrapper(
        booking_code: str, organization_id=None, updates=None, **kwargs
    ):
        updates = updates or {}
        existing = get_booking_wrapper(booking_code)
        booking_obj = existing.get("booking") if isinstance(existing, dict) else existing
        if not isinstance(booking_obj, dict):
            booking_obj = {"booking_code": booking_code, "status": "confirmed"}
        if updates.get("start_time"):
            booking_obj["starts_at"] = updates["start_time"]
        if updates.get("end_time"):
            booking_obj["ends_at"] = updates["end_time"]
        updated = {"booking": booking_obj, "status": "confirmed"}
        created_bookings[str(booking_code)] = updated
        return updated

    mock_booking_client.create_booking.side_effect = create_booking_wrapper
    mock_booking_client.get_booking.side_effect = get_booking_wrapper
    mock_booking_client.get_most_recent_booking = get_most_recent_booking_wrapper
    mock_booking_client.cancel_booking.side_effect = cancel_booking_wrapper
    mock_booking_client.confirm_booking.side_effect = confirm_booking_wrapper
    mock_booking_client.update_booking.side_effect = update_booking_wrapper
    return mock_booking_client


def create_mock_organization_client(
    business_category_id: int = 1,
) -> Mock:
    """Mock OrganizationClient with a service-domain org by default."""
    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": business_category_id}
    }
    return mock_org_client
