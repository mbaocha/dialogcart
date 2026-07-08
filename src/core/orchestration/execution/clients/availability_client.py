"""
Availability API Client

Thin client for availability endpoints (reservation, services, staff, next available).
"""

from typing import Any, Dict, Optional

import httpx

from core.orchestration.errors import UpstreamError
from core.orchestration.execution.clients.base_client import BaseClient
from core.tracing.availability import (
    begin_availability_request,
    clear_availability_trace,
    finalize_availability_http_error,
    record_availability_http,
)


class AvailabilityClient(BaseClient):
    """HTTP client for availability endpoints."""

    def __init__(self, base_url: Optional[str] = None):
        super().__init__(
            base_url=base_url,
            env_var="INTERNAL_API_BASE_URL",
            default_url="http://localhost:3000",
        )

    def _request_traced(
        self,
        method: str,
        path: str,
        *,
        params: Dict[str, Any],
        trace_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """HTTP request with availability.request / HTTP metadata capture."""
        begin_availability_request(
            endpoint=path,
            method=method,
            organization_id=int(params.get("organization_id") or 0),
            params=params,
            **trace_kwargs,
        )
        url = f"{self.base_url}{path}"
        try:
            response = self._client.request(
                method=method, url=url, params=params
            )
            raw_body = response.text or ""
            record_availability_http(
                http_status=response.status_code,
                raw_body=raw_body,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            error_text = e.response.text[:500] if e.response.text else ""
            record_availability_http(
                http_status=e.response.status_code,
                raw_body=e.response.text or "",
            )
            finalize_availability_http_error(
                http_status=e.response.status_code,
                raw_body=e.response.text or "",
            )
            try:
                error_json = e.response.json()
                error_text = str(error_json)
            except Exception:
                pass
            raise UpstreamError(
                f"API returned error {e.response.status_code}: {error_text}"
            ) from e
        except httpx.RequestError as e:
            clear_availability_trace()
            raise UpstreamError(f"API request failed: {str(e)}") from e
        except Exception as e:
            clear_availability_trace()
            raise UpstreamError(f"Unexpected error calling API: {str(e)}") from e

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
        return self._request_traced(
            "GET",
            "/api/internal/availability/reservation",
            params=params,
            trace_kwargs={
                "start_date": start_date,
                "end_date": end_date,
            },
        )

    def get_service_availability(
        self,
        organization_id: int,
        *,
        service_id: int,
        date: Optional[str] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "organization_id": organization_id,
            "service_id": service_id,
        }
        if date:
            params["date"] = date
        if extra_params:
            params.update(extra_params)
        return self._request_traced(
            "GET",
            "/api/internal/availability/services",
            params=params,
            trace_kwargs={
                "service_id": service_id,
                "date": date,
            },
        )

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
