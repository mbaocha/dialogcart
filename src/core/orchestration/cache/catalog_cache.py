"""
Orchestration Layer - Catalog Cache

TTL-based cache for tenant catalog offerings.

This module provides context derivation through caching of catalog data.
It is owned by the orchestration layer as it supports context building
for conversation orchestration.

- Read-through cache: fetches via CatalogClient on miss/expiry.
- Prefers Redis if available (REDIS_URL), falls back to in-memory.
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.orchestration.clients.catalog_client import CatalogClient

DEFAULT_TTL_SECONDS = 60
REDIS_ENV_VAR = "REDIS_URL"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CatalogCache:
    """TTL cache for tenant catalogs, with Redis preferred and in-memory fallback."""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        # cache key: (org_id, domain)
        self._mem_cache: Dict[tuple[int, str], Dict[str, Any]] = {}
        self._redis = None
        redis_url = os.getenv(REDIS_ENV_VAR)
        if redis_url:
            try:
                import redis  # type: ignore

                self._redis = redis.from_url(redis_url)
            except Exception:
                self._redis = None

    def _mem_get(self, org_id: int, domain: str) -> Optional[Dict[str, Any]]:
        entry = self._mem_cache.get((org_id, domain))
        if not entry:
            return None
        expires_at = entry.get("expires_at")
        if expires_at is None or expires_at < time.time():
            # Expired
            self._mem_cache.pop((org_id, domain), None)
            return None
        return entry.get("value")

    def _mem_set(self, org_id: int, domain: str, value: Dict[str, Any]) -> None:
        self._mem_cache[(org_id, domain)] = {
            "value": value,
            "expires_at": time.time() + self.ttl_seconds,
        }

    def _redis_get(self, org_id: int, domain: str) -> Optional[Dict[str, Any]]:
        if not self._redis:
            return None
        try:
            raw = self._redis.get(f"catalog:{org_id}:{domain}")
            if not raw:
                return None
            return json.loads(raw)
        except Exception:
            return None

    def _redis_set(self, org_id: int, domain: str, value: Dict[str, Any]) -> None:
        if not self._redis:
            return
        try:
            self._redis.setex(
                f"catalog:{org_id}:{domain}", self.ttl_seconds, json.dumps(value)
            )
        except Exception:
            # Fail silently; fallback will still have the data in-memory
            pass

    def get_cached(self, org_id: int, domain: str) -> Optional[Dict[str, Any]]:
        """Get catalog from cache if present and not expired."""
        # Redis first
        cached = self._redis_get(org_id, domain)
        if cached:
            return cached
        # Memory fallback
        return self._mem_get(org_id, domain)

    def set_cached(self, org_id: int, domain: str, value: Dict[str, Any]) -> None:
        """Store catalog in cache (both redis and memory)."""
        self._mem_set(org_id, domain, value)
        self._redis_set(org_id, domain, value)

    def get_catalog(
        self,
        org_id: int,
        catalog_client: CatalogClient,
        domain: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Read-through getter with optional force refresh.

        Returns catalog payload for the specified domain:
        {
            "catalog_last_updated_at": str | None,
            "services": [...],   # only for service domain
            "room_types": [...], # only for reservation domain
            "extras": [...],     # only for reservation domain
            "fetched_at": iso8601
        }
        """
        if not force_refresh:
            cached = self.get_cached(org_id, domain)
            if cached:
                return cached

        if domain == "service":
            payload_raw = catalog_client.get_services(org_id) or {}
            payload = payload_raw.get("data", payload_raw) or {}
            combined = {
                "catalog_last_updated_at": payload.get("catalog_last_updated_at"),
                "services": payload.get("services", []),
                "room_types": [],
                "extras": [],
                "fetched_at": _utc_now_iso(),
            }
        else:
            payload_raw = catalog_client.get_reservation(org_id) or {}
            payload = payload_raw.get("data", payload_raw) or {}
            combined = {
                "catalog_last_updated_at": payload.get("catalog_last_updated_at"),
                "services": [],
                "room_types": payload.get("room_types", []),
                "extras": payload.get("extras", []),
                "fetched_at": _utc_now_iso(),
            }

        self.set_cached(org_id, domain, combined)
        return combined


# Module-level cache instance (shared)
catalog_cache = CatalogCache()

