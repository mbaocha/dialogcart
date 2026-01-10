"""
Test Execution Backend

Deterministic execution backend for E2E tests.
Returns fake booking data without calling external APIs.

This backend implements the same interface as the real execution backend
but returns deterministic test responses.
"""

import logging
from typing import Dict, Any, Optional, Literal, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class TestExecutionBackend:
    """
    Test execution backend that returns deterministic fake bookings.
    
    This backend never calls external APIs and always returns successful
    responses with predictable data. Used for E2E testing.
    """
    
    # Counter for generating unique booking codes
    _booking_counter = 0
    
    # Default test values for missing execution-required fields
    DEFAULT_TEST_ITEM_ID = 999
    DEFAULT_TEST_DURATION_MINUTES = 60
    
    @classmethod
    def _generate_booking_code(cls) -> str:
        """Generate a deterministic test booking code."""
        cls._booking_counter += 1
        return f"TEST-BOOKING-{cls._booking_counter:03d}"
    
    @classmethod
    def inject_missing_execution_fields(
        cls,
        booking_type: Literal["service", "reservation"],
        item_id: Optional[int] = None,
        duration_minutes: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Inject missing execution-required fields with deterministic test values.
        
        This method ensures E2E tests can run deterministically even when
        Luma doesn't provide all required fields (e.g., item_id from catalog resolution).
        
        Args:
            booking_type: Type of booking ("service" or "reservation")
            item_id: Service or room item identifier (may be None)
            duration_minutes: Service duration in minutes (may be None for service bookings)
            **kwargs: Other execution parameters (passed through)
            
        Returns:
            Dictionary with all execution parameters, with missing required fields injected
        """
        # Inject item_id if missing
        if item_id is None or item_id == 0:
            item_id = cls.DEFAULT_TEST_ITEM_ID
            logger.debug(
                f"[TEST MODE] Injected missing item_id: {item_id} for {booking_type} booking"
            )
        
        # For service bookings, inject duration if missing
        if booking_type == "service" and (duration_minutes is None or duration_minutes <= 0):
            duration_minutes = cls.DEFAULT_TEST_DURATION_MINUTES
            logger.debug(
                f"[TEST MODE] Injected missing duration_minutes: {duration_minutes} for service booking"
            )
        
        return {
            "booking_type": booking_type,
            "item_id": item_id,
            "duration_minutes": duration_minutes,
            **kwargs
        }
    
    @classmethod
    def create_booking(
        cls,
        organization_id: int,
        customer_id: int,
        booking_type: Literal["service", "reservation"],
        item_id: int,
        *,
        # Service booking parameters
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        staff_id: Optional[int] = None,
        addons: Optional[List[Dict[str, Any]]] = None,
        # Reservation booking parameters
        check_in: Optional[str] = None,
        check_out: Optional[str] = None,
        guests: int = 1,
        extras: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Create a fake booking (test mode only).
        
        Returns a deterministic booking response without calling external APIs.
        
        Args:
            organization_id: Organization identifier
            customer_id: Customer identifier
            booking_type: Type of booking ("service" or "reservation")
            item_id: Service or room item identifier (optional, will be injected if missing)
            start_time: Service booking start time (ISO-8601 with timezone)
            end_time: Service booking end time (ISO-8601 with timezone)
            staff_id: Staff member ID for service bookings (optional)
            addons: Service booking addons (optional)
            check_in: Reservation check-in time (ISO-8601 with timezone)
            check_out: Reservation check-out time (ISO-8601 with timezone)
            guests: Number of guests for reservations (default: 1)
            extras: Reservation extras (optional)
            
        Returns:
            Fake booking data with deterministic structure
        """
        # Inject missing execution-required fields
        injected = cls.inject_missing_execution_fields(
            booking_type=booking_type,
            item_id=item_id
        )
        item_id = injected["item_id"]
        
        booking_code = cls._generate_booking_code()
        
        # Parse times for response
        if booking_type == "service":
            if not start_time or not end_time:
                raise ValueError(
                    "start_time and end_time are required for service bookings")
            starts_at = start_time
            ends_at = end_time
        else:  # reservation
            if not check_in or not check_out:
                raise ValueError(
                    "check_in and check_out are required for reservation bookings")
            starts_at = check_in
            ends_at = check_out
        
        logger.info(
            f"[TEST MODE] Creating fake booking: code={booking_code}, "
            f"type={booking_type}, org_id={organization_id}, customer_id={customer_id}"
        )
        
        # Return response in the same format as real API
        return {
            "booking_code": booking_code,
            "code": booking_code,  # Alternative field name
            "status": "pending",
            "booking": {
                "booking_code": booking_code,
                "code": booking_code,
                "id": cls._booking_counter,
                "status": "pending",
                "booking_type": booking_type,
                "organization_id": organization_id,
                "customer_id": customer_id,
                "item_id": item_id,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "total_amount": 0,
                "reservation_fee": 0 if booking_type == "reservation" else None,
                "type": booking_type,
            },
            "data": {
                "booking": {
                    "booking_code": booking_code,
                    "code": booking_code,
                    "id": cls._booking_counter,
                    "status": "pending",
                    "booking_type": booking_type,
                    "organization_id": organization_id,
                    "customer_id": customer_id,
                    "item_id": item_id,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "total_amount": 0,
                    "reservation_fee": 0 if booking_type == "reservation" else None,
                    "type": booking_type,
                }
            }
        }
    
    @classmethod
    def get_booking(cls, booking_code: str) -> Dict[str, Any]:
        """
        Get a fake booking by code (test mode only).
        
        Returns a deterministic booking response without calling external APIs.
        
        Args:
            booking_code: Booking code identifier
            
        Returns:
            Fake booking data
        """
        logger.info(f"[TEST MODE] Getting fake booking: code={booking_code}")
        
        # Return response in the same format as real API
        return {
            "booking": {
                "booking_code": booking_code,
                "code": booking_code,
                "id": 1,
                "status": "pending",
                "booking_type": "service",
                "organization_id": 1,
                "customer_id": 1,
            },
            "data": {
                "booking": {
                    "booking_code": booking_code,
                    "code": booking_code,
                    "id": 1,
                    "status": "pending",
                    "booking_type": "service",
                    "organization_id": 1,
                    "customer_id": 1,
                }
            }
        }
    
    @classmethod
    def update_booking(
        cls,
        booking_code: str,
        organization_id: int,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Update a fake booking (test mode only).
        
        Returns a deterministic booking response without calling external APIs.
        
        Args:
            booking_code: Booking code identifier
            organization_id: Organization identifier
            updates: Update fields
            
        Returns:
            Fake updated booking data
        """
        logger.info(
            f"[TEST MODE] Updating fake booking: code={booking_code}, "
            f"updates={updates}"
        )
        
        # Merge updates into base booking
        # Start with base fields, then apply updates (updates take precedence)
        base_booking = {
            "booking_code": booking_code,
            "code": booking_code,
            "id": 1,
            "status": "updated",  # Default status for updates
            "booking_type": "service",
            "organization_id": organization_id,
        }
        # Apply updates (they override base fields)
        base_booking.update(updates)
        # Ensure status reflects update operation
        if "status" not in updates:
            base_booking["status"] = "updated"
        
        return {
            "booking": base_booking,
            "data": {
                "booking": base_booking
            }
        }
    
    @classmethod
    def cancel_booking(
        cls,
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
        Cancel a fake booking (test mode only).
        
        Returns a deterministic cancellation response without calling external APIs.
        
        Args:
            booking_code: Booking code identifier
            organization_id: Organization identifier
            cancellation_type: Type of cancellation
            reason: Cancellation reason (optional)
            notes: Additional notes (optional)
            refund_method: Refund method (optional)
            notify_customer: Whether to notify customer (optional)
            
        Returns:
            Fake cancellation result
        """
        logger.info(
            f"[TEST MODE] Cancelling fake booking: code={booking_code}, "
            f"type={cancellation_type}"
        )
        
        return {
            "status": "cancelled",
            "booking_code": booking_code,
            "code": booking_code,
        }
    
    @classmethod
    def reset_counter(cls) -> None:
        """
        Reset the booking counter (useful for test isolation).
        
        This is a test utility method, not part of the execution interface.
        """
        cls._booking_counter = 0

