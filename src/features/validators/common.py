# Common validation utilities shared across all validators

from typing import Any, Dict, List, Optional, Tuple
from features.product.service import ProductService
from utils.coreutil import search_in_list


def coerce_positive_int(value: Any, default: int = 1) -> int:
    # Coerce a value to a positive integer
    try:
        v = int(value)
        return v if v > 0 else default
    except Exception:
        return default


def ensure_products(state) -> Dict[str, str]:
    # Ensure we have a flat map of products: {product_id: product_name}
    products = getattr(state, "products", None)
    if not products:
        products = ProductService().list_products_flat()
    return products


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