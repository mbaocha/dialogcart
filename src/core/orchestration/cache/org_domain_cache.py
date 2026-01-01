"""
Orchestration Layer - Organization Domain Cache

TTL-based cache for organization domain mappings.

- Fetches org details once to derive domain (service vs reservation).
- Caches per org_id with long TTL (default 6 hours).
- No per-message refresh; explicit refresh only.

This module provides context derivation through caching of organization
domain data. It is owned by the orchestration layer as it supports
context building for conversation orchestration.
"""

import json
import os
import time
from typing import Dict, Optional, Tuple

from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.errors import UpstreamError

REDIS_ENV_VAR = "REDIS_URL"
DEFAULT_TTL_SECONDS = 6 * 60 * 60  # 6 hours

# Explicit domain mapping by businessCategoryId
# TODO: Update these sets to match real category IDs when available.
SERVICE_CATEGORY_IDS = {1, "beauty_and_wellness"}
RESERVATION_CATEGORY_IDS = {2, "lodging", "hotel", "hospitality"}


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

    def _derive_domain(self, business_category_id: int) -> str:
        if business_category_id in SERVICE_CATEGORY_IDS:
            return "service"
        if business_category_id in RESERVATION_CATEGORY_IDS:
            return "reservation"
        raise UpstreamError(
            f"Unsupported businessCategoryId={business_category_id}; cannot derive domain"
        )

    def get_domain(
        self, org_id: int, org_client: OrganizationClient, force_refresh: bool = False
    ) -> Tuple[str, int]:
        if not force_refresh:
            cached = self._redis_get(org_id) or self._mem_get(org_id)
            if cached:
                return cached["domain"], cached["businessCategoryId"]

        details = org_client.get_details(org_id)
        data = details.get("data") if isinstance(details, dict) else None
        org = None
        if isinstance(details, dict) and isinstance(details.get("organization"), dict):
            org = details.get("organization")
        elif isinstance(data, dict) and isinstance(data.get("organization"), dict):
            org = data.get("organization")
        if not isinstance(org, dict):
            raise UpstreamError("Invalid organization details response: missing organization")
        business_category_id = org.get("businessCategoryId")
        if business_category_id is None:
            raise UpstreamError("businessCategoryId missing in organization details")

        domain = self._derive_domain(business_category_id)
        value = {"domain": domain, "businessCategoryId": business_category_id}
        self._mem_set(org_id, value)
        self._redis_set(org_id, value)
        return domain, business_category_id

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

