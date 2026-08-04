"""
Orchestration Layer - Organization Domain Cache

TTL-based cache for organization → business category → booking domain.

Resolution path (exactly one):
  Organization.businessCategoryId
    → business_category (schema owner)
    → booking_domain (from business schema; workflow owner)

- Fetches org details once; caches per org_id (default TTL 6 hours).
- No per-message refresh; explicit refresh only.
"""

import json
import os
import time
from typing import Any, Dict, Optional, Tuple

from core.adapters.clients.organization_client import OrganizationClient
from core.adapters.errors import UpstreamError
from core.config.business_category_loader import get_booking_domain, is_configured_category

REDIS_ENV_VAR = "REDIS_URL"
DEFAULT_TTL_SECONDS = 6 * 60 * 60  # 6 hours

# Map organization businessCategoryId (int or string alias) → business_category key.
BUSINESS_CATEGORY_IDS: Dict[Any, str] = {
    1: "beauty_salon",
    "beauty_and_wellness": "beauty_salon",
    "beauty_salon": "beauty_salon",
    3: "car_service",
    "car_service": "car_service",
    2: "hotel",
    "lodging": "hotel",
    "hotel": "hotel",
    "hospitality": "hotel",
}


class OrgDomainCache:
    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._mem_cache: Dict[int, Dict[str, object]] = {}
        self._redis = None
        redis_url = os.getenv(REDIS_ENV_VAR)
        if redis_url:
            try:
                import redis  # type: ignore

                self._redis = redis.from_url(redis_url)
            except Exception:
                self._redis = None

    def _mem_get(self, org_id: int) -> Optional[Dict[str, object]]:
        entry = self._mem_cache.get(org_id)
        if not entry:
            return None
        expires_at = entry.get("expires_at")
        if expires_at is None or expires_at < time.time():
            self._mem_cache.pop(org_id, None)
            return None
        return entry.get("value")

    def _mem_set(self, org_id: int, value: Dict[str, object]) -> None:
        self._mem_cache[org_id] = {
            "value": value,
            "expires_at": time.time() + self.ttl_seconds,
        }

    def _redis_get(self, org_id: int) -> Optional[Dict[str, object]]:
        if not self._redis:
            return None
        try:
            raw = self._redis.get(f"org_domain:{org_id}")
            if not raw:
                return None
            return json.loads(raw)
        except Exception:
            return None

    def _redis_set(self, org_id: int, value: Dict[str, object]) -> None:
        if not self._redis:
            return
        try:
            self._redis.setex(
                f"org_domain:{org_id}", self.ttl_seconds, json.dumps(value)
            )
        except Exception:
            pass

    def _resolve_business_category(self, business_category_id: Any) -> str:
        category = BUSINESS_CATEGORY_IDS.get(business_category_id)
        if not category or not is_configured_category(category):
            raise UpstreamError(
                f"Unsupported businessCategoryId={business_category_id}; "
                f"cannot resolve business_category"
            )
        booking_domain = get_booking_domain(category)
        if not booking_domain:
            raise UpstreamError(
                f"business_category={category!r} missing valid booking_domain"
            )
        return category

    def resolve(
        self,
        org_id: int,
        org_client: OrganizationClient,
        force_refresh: bool = False,
    ) -> Tuple[str, str, Any]:
        """Return (business_category, booking_domain, business_category_id)."""
        if not force_refresh:
            cached = self._redis_get(org_id) or self._mem_get(org_id)
            if cached:
                category = cached.get("business_category")
                booking_domain = cached.get("booking_domain") or cached.get("domain")
                cat_id = cached.get("businessCategoryId")
                if (
                    isinstance(category, str)
                    and isinstance(booking_domain, str)
                    and cat_id is not None
                ):
                    return category, booking_domain, cat_id

        details = org_client.get_details(org_id)
        data = details.get("data") if isinstance(details, dict) else None
        org = None
        if isinstance(details, dict) and isinstance(details.get("organization"), dict):
            org = details.get("organization")
        elif isinstance(data, dict) and isinstance(data.get("organization"), dict):
            org = data.get("organization")
        if not isinstance(org, dict):
            raise UpstreamError(
                "Invalid organization details response: missing organization"
            )
        business_category_id = org.get("businessCategoryId")
        if business_category_id is None:
            raise UpstreamError("businessCategoryId missing in organization details")

        business_category = self._resolve_business_category(business_category_id)
        booking_domain = get_booking_domain(business_category)
        assert booking_domain is not None  # validated in _resolve_business_category

        value = {
            "business_category": business_category,
            "booking_domain": booking_domain,
            # Legacy cache key used by older readers / tests.
            "domain": booking_domain,
            "businessCategoryId": business_category_id,
        }
        self._mem_set(org_id, value)
        self._redis_set(org_id, value)
        return business_category, booking_domain, business_category_id

    def get_domain(
        self, org_id: int, org_client: OrganizationClient, force_refresh: bool = False
    ) -> Tuple[str, Any]:
        """Return (booking_domain, business_category_id) for workflow/catalog callers."""
        _category, booking_domain, business_category_id = self.resolve(
            org_id, org_client, force_refresh=force_refresh
        )
        return booking_domain, business_category_id

    def clear(self, org_id: Optional[int] = None) -> None:
        if org_id is None:
            self._mem_cache.clear()
            if self._redis:
                try:
                    for key in self._redis.scan_iter("org_domain:*"):
                        self._redis.delete(key)
                except Exception:
                    pass
        else:
            self._mem_cache.pop(org_id, None)
            if self._redis:
                try:
                    self._redis.delete(f"org_domain:{org_id}")
                except Exception:
                    pass


org_domain_cache = OrgDomainCache()
