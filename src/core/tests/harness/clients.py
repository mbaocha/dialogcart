"""Test doubles for Luma and catalog clients used across core tests."""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional

from core.adapters.clients.catalog_client import CatalogClient
from core.adapters.nlu import LumaClient


def normalize_script_key(text: str) -> str:
    """Canonical script key: strip + casefold for deterministic lookup."""
    return text.strip().lower()


def _normalize_scripts(
    scripts: Dict[str, Dict[str, Any]],
    *,
    key_fn: Callable[[str], str],
) -> Dict[str, Dict[str, Any]]:
    """Build a normalised script map; raise on colliding keys."""
    normalised: Dict[str, Dict[str, Any]] = {}
    originals: Dict[str, str] = {}
    for original_key, value in scripts.items():
        key = key_fn(original_key)
        if key in normalised:
            raise ValueError(
                "Duplicate ScriptedLuma script keys after normalisation: "
                f"normalised_key={key!r}, first_original={originals[key]!r}, "
                f"conflicting_original={original_key!r}"
            )
        originals[key] = original_key
        normalised[key] = value
    return normalised


def apply_service_ambiguity_resolution(
    slm: Dict[str, Any],
    tenant_context: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Mirror ``NLUPipeline._resolve_service_ambiguity`` without importing Stage-1.

    Uses ``nlu.catalog.resolve_service`` only — no Anthropic import side effects.
    """
    from nlu.catalog import resolve_service

    aliases = tenant_context.get("aliases", {})
    if not aliases:
        return slm

    service_term = slm.get("service_term")
    facts = slm.get("facts", {})
    if not isinstance(facts, dict):
        facts = {}

    if not service_term:
        extracted = facts.get("service_id")
        if isinstance(extracted, str) and extracted.strip():
            service_term = extracted.strip()

    ctx = conversation_context if isinstance(conversation_context, dict) else {}
    awaiting_service_id = "service_id" in ctx.get("missing_slots", [])

    prior_text: Optional[str] = None
    candidate_keys: Optional[List[str]] = None
    resolved_service_id: Optional[str] = None
    if ctx:
        cands = ctx.get("service_candidates")
        if isinstance(cands, list) and cands:
            candidate_keys = cands
        if awaiting_service_id:
            turns = ctx.get("turns") or []
            joined = " ".join(t.get("user", "") for t in turns).strip()
            prior_text = joined or None
        raw_resolved = ctx.get("resolved_service_id")
        if isinstance(raw_resolved, str) and raw_resolved:
            resolved_service_id = raw_resolved

    resolved = resolve_service(
        service_term=service_term,
        aliases=aliases,
        prior_text=prior_text,
        awaiting_service_id=awaiting_service_id,
        candidate_keys=candidate_keys,
        resolved_service_id=resolved_service_id,
    )
    return {
        **slm,
        "facts": {**facts, "service_id": resolved["service_id"]},
        "service_candidates": resolved["service_candidates"],
    }


class ScriptedLumaClient(LumaClient):  # noqa: N801
    __test__ = False

    """LumaClient that returns pre-scripted responses keyed by normalised user text.

    Keys are normalised on registration and lookup (strip + lower by default).
    On a miss, raises ``AssertionError`` unless ``fallback`` or
    ``allow_live_fallback=True`` is set — never silently calls live Luma by default.
    """

    def __init__(
        self,
        scripts: Dict[str, Dict[str, Any]],
        *,
        fallback: Optional[LumaClient] = None,
        normalize_key: Optional[Callable[[str], str]] = None,
        allow_live_fallback: bool = False,
    ):
        super().__init__()
        self.normalize_key = normalize_key or normalize_script_key
        self.scripts = _normalize_scripts(scripts, key_fn=self.normalize_key)
        self.fallback = fallback
        self.allow_live_fallback = allow_live_fallback
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
        entity_schema: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
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
                entity_schema=entity_schema,
                **kwargs,
            )
            self.last_response = response
            return response
        if self.allow_live_fallback:
            response = super().resolve(
                user_id,
                text,
                domain,
                timezone,
                tenant_context,
                conversation_context=conversation_context,
                entity_schema=entity_schema,
                **kwargs,
            )
            self.last_response = response
            return response
        available_keys = sorted(self.scripts.keys())
        raise AssertionError(
            f"No scripted Luma response for {text!r}. "
            f"Normalised key: {key!r}. "
            f"Available keys: {available_keys!r}"
        )


class NluServiceResolutionScriptedLumaClient(ScriptedLumaClient):  # noqa: N801
    __test__ = False

    """Scripted Luma that applies production NLU service post-process.

    Stage-2 scripts may set ``facts.service_id`` without ``service_term``
    (AVAILABILITY shape). Running service ambiguity resolution reproduces the
    production overwrite of current-turn Flexi by session ``resolved_service_id``.
    """

    def resolve(
        self,
        user_id: str,
        text: str,
        domain: str = "service",
        timezone: str = "UTC",
        tenant_context: Optional[Dict[str, Any]] = None,
        conversation_context: Optional[Dict[str, Any]] = None,
        entity_schema: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        response = super().resolve(
            user_id,
            text,
            domain,
            timezone,
            tenant_context,
            conversation_context=conversation_context,
            entity_schema=entity_schema,
            **kwargs,
        )
        intent = response.get("intent") or {}
        intent_name = intent.get("name") if isinstance(intent, dict) else intent
        if intent_name != "AVAILABILITY":
            return response

        facts = response.get("facts") if isinstance(response.get("facts"), dict) else {}
        slm = {
            "intent": "AVAILABILITY",
            "facts": dict(facts),
            "service_term": response.get("service_term"),
            "service_candidates": list(response.get("service_candidates") or []),
        }
        resolved = apply_service_ambiguity_resolution(
            slm,
            tenant_context if isinstance(tenant_context, dict) else {},
            conversation_context,
        )
        response = {
            **response,
            "facts": resolved.get("facts") or facts,
            "service_candidates": resolved.get("service_candidates") or [],
        }
        slots = response.get("slots")
        if isinstance(slots, dict):
            service_id = (resolved.get("facts") or {}).get("service_id")
            if service_id:
                response["slots"] = {**slots, "service_id": service_id}
        self.last_response = response
        return response


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
        test_now: Optional[str] = None,
        entity_schema: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
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
            test_now=test_now,
            entity_schema=entity_schema,
            **kwargs,
        )
        self.last_response = response
        return response


class TestCatalogClient(CatalogClient):  # noqa: N801
    __test__ = False

    """CatalogClient that returns catalog entries derived from test aliases.

    ``test_aliases`` maps display name → id for the configured primary catalog
    collection. Optional ``collections`` supplies additional configured catalogs.
    Legacy callers may omit ``catalog_collection_keys`` and retain domain defaults.
    """

    def __init__(
        self,
        test_aliases: Optional[Dict[str, str]] = None,
        domain: str = "service",
        collections: Optional[Dict[str, Dict[str, Any]]] = None,
        business_category_id: Any = None,
        service_records: Optional[List[Dict[str, Any]]] = None,
        catalog_collection_keys: Optional[List[str]] = None,
        primary_catalog_collection: Optional[str] = None,
    ):
        super().__init__()
        self.test_aliases = test_aliases or {}
        self.domain = domain
        self.collections = collections or {}
        self.service_records = copy.deepcopy(service_records or [])
        # Domain-based defaults are retained only for legacy callers that do not
        # provide the configured bookable-item catalog.
        default_collection = "services" if domain == "service" else "room_types"
        self.catalog_collection_keys = list(
            catalog_collection_keys or [default_collection]
        )
        self.primary_catalog_collection = (
            primary_catalog_collection or default_collection
        )
        if business_category_id is not None:
            self.business_category_id = business_category_id
        else:
            self.business_category_id = 1 if domain == "service" else 2

    def _items_from_phrase_map(
        self, phrase_map: Dict[str, Any], *, duration: Optional[int] = None
    ) -> list:
        items = []
        for alias_name, canonical_key in phrase_map.items():
            entry: Dict[str, Any] = {
                "name": alias_name,
                "canonical": canonical_key,
                "service_family_id": canonical_key,
                "is_active": True,
            }
            try:
                entry["id"] = int(canonical_key)
            except (TypeError, ValueError):
                entry["id"] = canonical_key
            if duration is not None:
                entry["duration"] = duration
            items.append(entry)
        return items

    def _primary_catalog_items(self, *, reservation: bool = False) -> List[Dict[str, Any]]:
        if self.service_records:
            records = []
            for raw_record in self.service_records:
                record = copy.deepcopy(raw_record)
                # Aliases are NLU input, not business catalog metadata.
                record.pop("aliases", None)
                canonical = record.get("canonical", record.get("id"))
                record.setdefault("canonical", canonical)
                record.setdefault("service_family_id", canonical)
                record.setdefault("is_active", True)
                if reservation:
                    record.setdefault("canonical_key", canonical)
                records.append(record)
            return records

        # Legacy phrase-map behavior remains the default for existing tests.
        if not reservation:
            return self._items_from_phrase_map(self.test_aliases, duration=60)
        return [
            {
                "name": alias_name,
                "canonical_key": canonical_key,
                "canonical": canonical_key,
                "is_active": True,
            }
            for alias_name, canonical_key in self.test_aliases.items()
        ]

    def get_services(self, organization_id: int) -> Dict[str, Any]:
        services = self._primary_catalog_items()
        payload: Dict[str, Any] = {
            "catalog_last_updated_at": "2026-01-01T00:00:00Z",
            "business_category_id": self.business_category_id,
            self.primary_catalog_collection: services,
        }
        for collection_name, phrase_map in self.collections.items():
            if collection_name == self.primary_catalog_collection:
                continue
            if not isinstance(phrase_map, dict):
                continue
            payload[collection_name] = self._items_from_phrase_map(phrase_map)
        return payload

    def get_reservation(self, organization_id: int) -> Dict[str, Any]:
        rooms = self._primary_catalog_items(reservation=True)
        payload: Dict[str, Any] = {
            "catalog_last_updated_at": "2026-01-01T00:00:00Z",
            "business_category_id": self.business_category_id,
            self.primary_catalog_collection: rooms,
            "extras": [],
        }
        for collection_name, phrase_map in self.collections.items():
            if collection_name in (self.primary_catalog_collection, "extras"):
                continue
            if not isinstance(phrase_map, dict):
                continue
            payload[collection_name] = self._items_from_phrase_map(phrase_map)
        return payload


def stub_catalog_client(
    *,
    aliases: Optional[Dict[str, str]] = None,
    domain: str = "service",
) -> TestCatalogClient:
    """In-memory catalog client for handle_message / planning tests (no HTTP)."""
    return TestCatalogClient(
        test_aliases=aliases or {"haircut": "haircut"},
        domain=domain,
    )
