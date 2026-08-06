"""Resolve tenant SKU strings to catalog item ids for API execution.

Planning and NLU keep slots.service_id as the tenant alias key (e.g. "premium haircut").
Execution maps that key to a numeric catalog id immediately before availability/booking calls.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union


CatalogId = Union[int, str]


def build_sku_to_catalog_id(
    catalog_data: Optional[Dict[str, Any]],
    domain: str = "service",
) -> Dict[str, int]:
    """Build lowercase SKU name → catalog item id from cached catalog payload."""
    if not isinstance(catalog_data, dict):
        return {}

    sku_map: Dict[str, int] = {}

    if domain == "service":
        for svc in catalog_data.get("services") or []:
            if not isinstance(svc, dict) or svc.get("is_active") is False:
                continue
            name = svc.get("name")
            item_id = svc.get("id")
            if name and item_id is not None:
                try:
                    sku_map[str(name).lower()] = int(item_id)
                except (TypeError, ValueError):
                    continue
        return sku_map

    for collection_key in ("rooms", "room_types"):
        for item in catalog_data.get(collection_key) or []:
            if not isinstance(item, dict) or item.get("is_active") is False:
                continue
            name = item.get("name")
            item_id = item.get("id")
            if name and item_id is not None:
                try:
                    sku_map[str(name).lower()] = int(item_id)
                except (TypeError, ValueError):
                    continue
        if sku_map:
            break

    for extra in catalog_data.get("extras") or []:
        if not isinstance(extra, dict) or extra.get("is_active") is False:
            continue
        name = extra.get("name")
        item_id = extra.get("id")
        if name and item_id is not None:
            try:
                sku_map[str(name).lower()] = int(item_id)
            except (TypeError, ValueError):
                continue

    return sku_map


def resolve_catalog_item_id(
    sku: Optional[CatalogId],
    sku_to_catalog_id: Optional[Dict[str, int]] = None,
) -> Optional[int]:
    """Map a slot SKU to a numeric catalog id; return None when unmapped."""
    if sku is None:
        return None
    if isinstance(sku, int):
        return sku
    if isinstance(sku, str) and sku.isdigit():
        return int(sku)

    if not isinstance(sku, str) or not sku_to_catalog_id:
        return None

    mapped = sku_to_catalog_id.get(sku.lower())
    if mapped is not None:
        return mapped
    return None


def load_sku_to_catalog_id_for_org(
    organization_id: int,
    organization_client: Any = None,
    *,
    catalog_client: Any = None,
) -> Dict[str, int]:
    """Load cached catalog and build SKU → id map for an organization."""
    from core.adapters.cache.catalog_cache import catalog_cache
    from core.adapters.cache.org_domain_cache import org_domain_cache
    from core.adapters.clients.catalog_client import CatalogClient
    from core.adapters.clients.organization_client import OrganizationClient

    client = organization_client or OrganizationClient()
    domain, _ = org_domain_cache.get_domain(
        organization_id, client, force_refresh=False
    )
    if domain not in ("service", "reservation"):
        return {}

    catalog = catalog_cache.get_catalog(
        organization_id,
        catalog_client or CatalogClient(),
        domain=domain,
    )
    return build_sku_to_catalog_id(catalog, domain)
