# Common validation utilities shared across all validators

from typing import Any, Dict, List, Optional, Tuple
from features.catalog.service import CatalogService
from utils.coreutil import search_in_list


def coerce_positive_int(value: Any, default: int = 1) -> int:
    # Coerce a value to a positive integer
    try:
        v = int(value)
        return v if v > 0 else default
    except Exception:
        return default


def ensure_products(state) -> Dict[str, str]:
    # Deprecated: use ensure_catalog_items instead
    return ensure_catalog_items(state)

def ensure_catalog_items(state) -> Dict[str, str]:
    # Ensure we have a flat map of catalog items: {catalog_id: catalog_name}
    catalog_items = getattr(state, "catalog_items", None)
    if not catalog_items:
        tenant_id = getattr(state, "tenant_id", None)
        if not tenant_id:
            try:
                from features.customer import _default_tenant_id
                tenant_id = _default_tenant_id()
            except Exception:
                tenant_id = "demo-tenant-001"
        catalog_items = CatalogService().list_catalog_flat(tenant_id)
    return catalog_items


def resolve_product(
    product_name: Optional[str],
    products: Dict[str, str],
) -> Tuple[bool, Optional[str], List[str], Optional[str]]:
    # Resolve a product name to a product ID
    if not product_name:
        return False, None, [], "missing_product_name"

    matches = search_in_list(product_name, products, fallback_to_fuzzy=True) or []
    print("[DEBUG] search_in_list matching_ids={}".format(matches))
    
    if len(matches) == 0:
        return False, None, [], "product_not_found"
    if len(matches) > 1:
        return False, None, matches[:10], "ambiguous_product"
    
    return True, matches[0], [], None 


def resolve_catalog_item(
    catalog_name: Optional[str],
    catalog_items: Dict[str, str],
) -> Tuple[bool, Optional[str], List[str], Optional[str]]:
    # Resolve a catalog item name to a catalog ID
    if not catalog_name:
        return False, None, [], "missing_catalog_name"

    matches = search_in_list(catalog_name, catalog_items, fallback_to_fuzzy=True) or []
    print("[DEBUG] search_in_list matching_ids={}".format(matches))
    
    if len(matches) == 0:
        return False, None, [], "catalog_item_not_found"
    if len(matches) > 1:
        return False, None, matches[:10], "ambiguous_catalog_item"
    
    return True, matches[0], [], None