"""Test doubles for Luma and catalog clients used across core tests."""

from typing import Any, Dict, Optional

from core.orchestration.clients.catalog_client import CatalogClient
from core.orchestration.nlu import LumaClient


class TestLumaClient(LumaClient):  # noqa: N801
    __test__ = False

    """LumaClient that injects tenant_context aliases for deterministic NLU tests."""

    def __init__(self, test_aliases: Optional[Dict[str, str]] = None):
        super().__init__()
        self.test_aliases = test_aliases or {}
        self.last_response: Optional[Dict[str, Any]] = None

    def resolve(
        self,
        user_id: str,
        text: str,
        domain: str = "service",
        timezone: str = "UTC",
        tenant_context: Optional[Dict[str, Any]] = None,
        conversation_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self.test_aliases:
            if tenant_context is None:
                tenant_context = {}
            tenant_context = {**tenant_context, "aliases": self.test_aliases}

        response = super().resolve(
            user_id,
            text,
            domain,
            timezone,
            tenant_context,
            conversation_context=conversation_context,
        )
        self.last_response = response
        return response


class TestCatalogClient(CatalogClient):  # noqa: N801
    __test__ = False

    """CatalogClient that returns catalog entries derived from test aliases."""

    def __init__(
        self, test_aliases: Optional[Dict[str, str]] = None, domain: str = "service"
    ):
        super().__init__()
        self.test_aliases = test_aliases or {}
        self.domain = domain

    def get_services(self, organization_id: int) -> Dict[str, Any]:
        services = []
        for alias_name, canonical_key in self.test_aliases.items():
            services.append(
                {
                    "name": alias_name,
                    "canonical": canonical_key,
                    "service_family_id": canonical_key,
                    "is_active": True,
                    "duration": 60,
                }
            )
        return {
            "catalog_last_updated_at": "2026-01-01T00:00:00Z",
            "business_category_id": 1,
            "services": services,
        }

    def get_reservation(self, organization_id: int) -> Dict[str, Any]:
        rooms = []
        for alias_name, canonical_key in self.test_aliases.items():
            rooms.append(
                {
                    "name": alias_name,
                    "canonical_key": canonical_key,
                    "canonical": canonical_key,
                    "is_active": True,
                }
            )
        return {
            "catalog_last_updated_at": "2026-01-01T00:00:00Z",
            "business_category_id": 2,
            "room_types": rooms,
            "extras": [],
        }
