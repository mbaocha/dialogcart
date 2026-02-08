"""
Luma HTTP Client

Thin HTTP client for calling Luma /resolve endpoint.
"""

import os
from typing import Dict, Any, Optional
import httpx

from core.orchestration.errors import UpstreamError


class LumaClient:
    """HTTP client for Luma service."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 30.0):
        """
        Initialize Luma client.

        Args:
            base_url: Luma service base URL. Defaults to LUMA_BASE_URL env var.
            timeout: Request timeout in seconds
        """
        if base_url:
            self.base_url = base_url
        else:
            env_value = os.getenv("LUMA_BASE_URL")
            if env_value:
                self.base_url = env_value
            else:
                self.base_url = "http://localhost:9001"

        self.base_url = self.base_url.rstrip("/")
        self.timeout = timeout
        # Create a single httpx client instance for reuse
        self._client = httpx.Client(timeout=timeout)

    def resolve(
        self,
        user_id: str,
        text: str,
        domain: str = "service",
        timezone: str = "UTC",
        tenant_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Call Luma /resolve endpoint.

        Args:
            user_id: User identifier (required)
            text: User message text (required)
            domain: Domain (optional, default: "service")
            timezone: Timezone (optional, default: "UTC")
            tenant_context: Optional tenant context with aliases (optional)

        Returns:
            Luma response dictionary

        Raises:
            UpstreamError: On network failures or HTTP errors
        """
        url = f"{self.base_url}/resolve"

        payload = {
            "user_id": user_id,
            "text": text,
            "domain": domain,
            "timezone": timezone
        }
        if tenant_context:
            payload["tenant_context"] = tenant_context

        try:
            response = self._client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            error_text = e.response.text[:200] if e.response.text else ""
            raise UpstreamError(
                f"Luma API returned error {status_code}: {error_text}"
            ) from e
        except httpx.RequestError as e:
            raise UpstreamError(
                f"Luma API request failed: {str(e)}"
            ) from e
        except Exception as e:
            raise UpstreamError(
                f"Unexpected error calling Luma API: {str(e)}"
            ) from e

    def notify_execution(
        self,
        user_id: str,
        booking_id: str,
        domain: str = "service"
    ) -> Dict[str, Any]:
        """
        Notify Luma about booking execution completion.
        
        Updates the booking_lifecycle state to EXECUTED in Luma's memory.

        Args:
            user_id: User identifier (required)
            booking_id: Booking identifier (required)
            domain: Domain (optional, default: "service")

        Returns:
            Response dictionary with success status

        Raises:
            UpstreamError: On network failures or HTTP errors
        """
        url = f"{self.base_url}/notify_execution"

        payload = {
            "user_id": user_id,
            "booking_id": booking_id,
            "booking_lifecycle": "EXECUTED",
            "domain": domain
        }

        try:
            response = self._client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            error_text = e.response.text[:200] if e.response.text else ""
            # For 404, this endpoint doesn't exist in Luma (non-critical lifecycle update)
            # Return a graceful response instead of raising an error
            if status_code == 404:
                return {
                    "success": False,
                    "error": "endpoint_not_found",
                    "message": "Luma /notify_execution endpoint not available (404). This is a non-critical lifecycle update."
                }
            raise UpstreamError(
                f"Luma API returned error {status_code}: {error_text}"
            ) from e
        except httpx.RequestError as e:
            raise UpstreamError(
                f"Luma API request failed: {str(e)}"
            ) from e
        except Exception as e:
            raise UpstreamError(
                f"Unexpected error calling Luma API: {str(e)}"
            ) from e

    def __del__(self):
        """Close httpx client on cleanup."""
        if hasattr(self, "_client"):
            try:
                self._client.close()
            except Exception:
                pass
