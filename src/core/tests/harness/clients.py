"""Test doubles for Luma and catalog clients used across core tests."""

import copy
from typing import Any, Callable, Dict, Optional

from core.adapters.clients.catalog_client import CatalogClient
from core.adapters.nlu import LumaClient


class ScriptedLumaClient(LumaClient):  # noqa: N801
    __test__ = False

    """LumaClient that returns pre-scripted responses keyed by exact user text."""

    def __init__(
        self,
        scripts: Dict[str, Dict[str, Any]],
        *,
        fallback: Optional[LumaClient] = None,
        normalize_key: Optional[Callable[[str], str]] = None,
    ):
        super().__init__()
        self.scripts = scripts
        self.fallback = fallback
        self.normalize_key = normalize_key or (lambda text: text.strip().lower())
        self.last_text: Optional[str] = None
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
        self.last_text = text
        key = self.normalize_key(text)
        if key in self.scripts:
            response = copy.deepcopy(self.scripts[key])
            self.last_response = response
            return response
        if self.fallback is not None:
            response = self.fallback.resolve(
                user_id,
                text,
                domain,
                timezone,
                tenant_context,
                conversation_context=conversation_context,
            )
            self.last_response = response
            return response
        return super().resolve(
            user_id,
            text,
            domain,
            timezone,
            tenant_context,
            conversation_context=conversation_context,
        )


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
