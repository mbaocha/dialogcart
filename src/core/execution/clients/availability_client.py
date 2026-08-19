"""
Availability API Client

Thin client for availability endpoints (reservation, services, staff, next available).
"""

from typing import Any, Dict, Optional

import httpx

from core.adapters.errors import AvailabilityRejectedError, UpstreamError
from core.execution.clients.base_client import BaseClient
from core.tracing.availability import (
    begin_availability_request,
    clear_availability_trace,
    finalize_availability_http_error,
    record_availability_http,
)


_AVAILABILITY_REJECTION_REASONS = {
    "BUSINESS_CLOSED": "business_closed",
}


def _availability_rejection_reason(detail: Any) -> str:
    """Normalize only recognized structured provider rejection codes."""
    if not isinstance(detail, dict):
        return "availability_rejected"
    code = detail.get("code")
    if not isinstance(code, str):
        return "availability_rejected"
    return _AVAILABILITY_REJECTION_REASONS.get(
        code.strip().upper(), "availability_rejected"
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
        org_param = params.get("organization_id")
        if org_param is None:
            raise ValueError("organization_id is required for availability request tracing")
        begin_availability_request(
            endpoint=path,
            method=method,
            organization_id=int(org_param),
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
            error_json = None
            try:
                error_json = e.response.json()
                error_text = str(error_json)
            except Exception:
                pass
            detail = error_json.get("detail") if isinstance(error_json, dict) else None
            message = error_json.get("message") if isinstance(error_json, dict) else None
            is_business_rejection = isinstance(detail, str) or (
                isinstance(detail, dict)
                and any(detail.get(key) for key in ("code", "type", "reason"))
            ) or (isinstance(message, str) and bool(message.strip()))
            if e.response.status_code == 422 and is_business_rejection:
                raise AvailabilityRejectedError(
                    reason=_availability_rejection_reason(detail)
                ) from e
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
