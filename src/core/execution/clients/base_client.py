"""
Base HTTP Client

Shared base class for thin HTTP clients used for business execution.
"""

import os
from typing import Dict, Any, Optional
import httpx

from core.orchestration.errors import UpstreamError


class BaseClient:
    """Base HTTP client with common error handling."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        env_var: str = "INTERNAL_API_BASE_URL",
        default_url: Optional[str] = None,
        timeout: float = 30.0
    ):
        """
        Initialize base client.

        Args:
            base_url: API base URL (overrides env var)
            env_var: Environment variable name for base URL
            default_url: Default base URL if not provided and env var is not set
            timeout: Request timeout in seconds

        Raises:
            ValueError: If base_url is not provided and env_var is not set
        """
        if base_url:
            self.base_url = base_url
        else:
            env_value = os.getenv(env_var)
            if env_value:
                self.base_url = env_value
            elif default_url is not None:
                self.base_url = default_url
            else:
                raise ValueError(
                    f"Base URL is required. Either provide 'base_url' parameter "
                    f"or set environment variable '{env_var}'"
                )

        self.base_url = self.base_url.rstrip("/")
        self.timeout = timeout
        # Create a single httpx client instance for reuse
        self._client = httpx.Client(timeout=timeout)

    def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make HTTP request and return parsed JSON.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (will be appended to base_url)
            json: JSON payload for POST/PUT requests
            params: Query parameters for GET requests

        Returns:
            Parsed JSON response

        Raises:
            UpstreamError: On network failures or HTTP errors
        """
        url = f"{self.base_url}{path}"

        try:
            response = self._client.request(
                method=method,
                url=url,
                json=json,
                params=params
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            error_text = e.response.text[:500] if e.response.text else ""
            # Try to parse JSON error for better error messages
            try:
                error_json = e.response.json()
                error_text = str(error_json)
            except Exception:
                pass
            raise UpstreamError(
                f"API returned error {status_code}: {error_text}"
            ) from e
        except httpx.RequestError as e:
            raise UpstreamError(
                f"API request failed: {str(e)}"
            ) from e
        except Exception as e:
            raise UpstreamError(
                f"Unexpected error calling API: {str(e)}"
            ) from e

    def _request_allow_404(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Make HTTP request and return parsed JSON, or None on 404.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (will be appended to base_url)
            json: JSON payload for POST/PUT requests
            params: Query parameters for GET requests

        Returns:
            Parsed JSON response, or None if 404

        Raises:
            UpstreamError: On network failures or HTTP errors (except 404)
        """
        url = f"{self.base_url}{path}"

        try:
            response = self._client.request(
                method=method,
                url=url,
                json=json,
                params=params
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            status_code = e.response.status_code
            error_text = e.response.text[:200] if e.response.text else ""
            raise UpstreamError(
                f"API returned error {status_code}: {error_text}"
            ) from e
        except httpx.RequestError as e:
            raise UpstreamError(
                f"API request failed: {str(e)}"
            ) from e
        except Exception as e:
            raise UpstreamError(
                f"Unexpected error calling API: {str(e)}"
            ) from e

    def __del__(self):
        """Close httpx client on cleanup."""
        if hasattr(self, "_client"):
            try:
                self._client.close()
            except Exception:
                pass

