from __future__ import annotations
from typing import Any, Dict, List, Optional, Union
from langchain.tools import tool

from .service import CatalogService
from features.customer import _default_tenant_id

# create a single service instance for tool calls
_service = CatalogService()


def get_catalog_item(catalog_id: str) -> Dict[str, Any]:
    """Fetch a catalog item by its unique catalog_id."""
    tenant_id = _default_tenant_id()
    return _service.get_catalog_item(catalog_id, tenant_id=tenant_id)


def search_catalog(
    catalog_name: str | None = None,
    catalog_items: Dict[str, str] | None = None,
    *,
    threshold: float = 0.6,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Search for catalog items by name using GSI4_TitlePrefix for efficient prefix matching,
    with fallback to fuzzy matching if catalog_items is provided.

    Catalog-only interface. Uses new db/catalog.py GSI4 index for better performance.
    """
    tenant_id = _default_tenant_id()
    return _service.search_catalog(catalog_name, catalog_items, threshold, tenant_id=tenant_id)


def list_catalog_by_categories_formatted(
    limit_categories: int = 10,
    examples_per_category: int = 5
) -> str:
    """
    List catalog items grouped by category in bullet-point format.

    MANDATORY: Call this when the user asks for products/catalog/inventory.
    """
    tenant_id = _default_tenant_id()
    data = _service.list_catalog_by_categories_formatted(
        tenant_id=tenant_id,
        limit_categories=limit_categories,
        examples_per_category=examples_per_category,
    )
    if data.get("success"):
        bullet_list = data["data"]["text"]
        intro = "Here's a great selection of our available products at Bulkpot:"
        outro = "If you're interested in any of these items or would like to make an order, just let me know! I'm here to help! 😊"
        return f"{intro}\n\n{bullet_list}\n\n{outro}"
    else:
        return f"Error: {data.get('error', 'Unknown error')}"
