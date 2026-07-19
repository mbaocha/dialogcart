"""
Turn planner — canonical staged planning pipeline entry.

Delegates to core.planning.pipeline.run_planning_pipeline.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.adapters.cache.catalog_cache import catalog_cache
from core.adapters.cache.org_domain_cache import org_domain_cache
from core.adapters.clients.catalog_client import CatalogClient
from core.adapters.clients.organization_client import OrganizationClient
from core.adapters.nlu import LumaClient
from core.planning.pipeline.orchestrator import run_planning_pipeline

logger = logging.getLogger(__name__)
turn_logger = logging.getLogger("core.turn_log")


def _build_tenant_context(
    organization_id: int,
    derived_domain: str,
    catalog_client: CatalogClient,
) -> Optional[Dict[str, Any]]:
    if derived_domain not in ("service", "reservation"):
        return None
    catalog_data = catalog_cache.get_catalog(
        organization_id, catalog_client, domain=derived_domain
    )
    alias_map: Dict[str, Any] = {}
    if derived_domain == "service":
        services = (
            catalog_data.get("services", [])
            if isinstance(catalog_data, dict)
            else []
        )
        for svc in services:
            if not isinstance(svc, dict) or svc.get("is_active") is False:
                continue
            name = svc.get("name")
            if not name:
                continue
            item_id = svc.get("id")
            if item_id is not None:
                try:
                    alias_map[name.lower()] = int(item_id)
                    continue
                except (TypeError, ValueError):
                    pass
            canonical_key = (
                svc.get("service_family_id")
                or svc.get("canonical")
                or svc.get("slug")
                or name.lower().replace(" ", "_")
            )
            if not canonical_key:
                continue
            if "." not in str(canonical_key):
                canonical_key = f"beauty_and_wellness.{canonical_key}"
            alias_map[name.lower()] = canonical_key
    else:
        for collection, key_fn in (
            ("rooms", lambda rt: rt.get("canonical_key") or rt.get("canonical") or rt.get("slug")),
            ("extras", lambda ex: ex.get("canonical") or ex.get("slug")),
        ):
            items = (
                catalog_data.get(collection, [])
                if isinstance(catalog_data, dict)
                else []
            )
            for item in items:
                if not isinstance(item, dict) or item.get("is_active") is False:
                    continue
                name = item.get("name")
                if not name:
                    continue
                canonical_key = key_fn(item) or name.lower().replace(" ", "_")
                alias_map[name.lower()] = canonical_key

    tenant_context: Dict[str, Any] = {"booking_mode": derived_domain}
    if alias_map:
        tenant_context["aliases"] = alias_map
    return tenant_context


def plan_turn(
    user_id: str,
    text: str,
    organization_id: int,
    timezone: str = "UTC",
    phone_number: Optional[str] = None,
    email: Optional[str] = None,
    customer_id: Optional[int] = None,
    luma_client: Optional[LumaClient] = None,
    catalog_client: Optional[CatalogClient] = None,
    organization_client: Optional[OrganizationClient] = None,
    verbose: bool = False,
    session_state: Optional[Dict[str, Any]] = None,
    transaction_id: Optional[str] = None,
    planning_only: bool = False,
    apply_domain_filter: bool = True,
) -> Dict[str, Any]:
    """Run one planning turn through the canonical staged pipeline."""
    _ = (phone_number, email, customer_id, verbose)

    if luma_client is None:
        luma_client = LumaClient()
    if catalog_client is None:
        catalog_client = CatalogClient()
    if organization_client is None:
        organization_client = OrganizationClient()

    derived_domain, _ = org_domain_cache.get_domain(
        organization_id, organization_client, force_refresh=False
    )
    tenant_context = _build_tenant_context(
        organization_id, derived_domain, catalog_client
    )

    return run_planning_pipeline(
        user_id=user_id,
        text=text,
        organization_id=organization_id,
        derived_domain=derived_domain,
        timezone=timezone,
        tenant_context=tenant_context,
        session_state=session_state,
        luma_client=luma_client,
        organization_client=organization_client,
        transaction_id=transaction_id,
        planning_only=planning_only,
        apply_domain_filter=apply_domain_filter,
    )
