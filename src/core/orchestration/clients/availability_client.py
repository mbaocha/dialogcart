"""
Availability API Client

Thin client for availability endpoints (reservation, services, staff, next available).
"""

from typing import Any, Dict, Optional

from core.orchestration.clients.base_client import BaseClient


class AvailabilityClient(BaseClient):
    """HTTP client for availability endpoints."""

    def __init__(self, base_url: Optional[str] = None):
        super().__init__(
            base_url=base_url,
            env_var="INTERNAL_API_BASE_URL",
            default_url="http://localhost:3000",
        )

    def get_reservation_availability(
        self,
        organization_id: int,
        *,
        start_date: str,
        end_date: str,
        check_in: Optional[str] = None,
        check_out: Optional[str] = None,
        channel: Optional[str] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "organization_id": organization_id,
            "startDate": start_date,
            "endDate": end_date,
        }
        if check_in:
            params["check_in"] = check_in
        if check_out:
            params["check_out"] = check_out
        if channel:
            params["channel"] = channel
        if extra_params:
            params.update(extra_params)
        return self._request("GET", "/api/internal/availability/reservation", params=params)

    def get_service_availability(
        self,
        organization_id: int,
        *,
        service_id: int,
        date: str,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "organization_id": organization_id,
            "service_id": service_id,
            "date": date,
        }
        if extra_params:
            params.update(extra_params)
        return self._request("GET", "/api/internal/availability/services", params=params)

    def get_staff_availability(
        self,
        organization_id: int,
        *,
        date: str,
        service_id: Optional[int] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "organization_id": organization_id,
            "date": date,
        }
        if service_id is not None:
            params["service_id"] = service_id
        if extra_params:
            params.update(extra_params)
        return self._request("GET", "/api/internal/availability/staff", params=params)


