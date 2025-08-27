from __future__ import annotations
from typing import Any, Dict, List, Optional, Union
from langchain.tools import tool

from .service import ProductService

# create a single service instance for tool calls
_service = ProductService()


def get_product(product_id: str) -> Dict[str, Any]:
    """Fetch a product by its unique product_id."""
    return _service.get_product(product_id)

def search_products(product_name: str, products: Dict[str, str] = None, threshold: float = 0.6) -> Dict[str, Any]:
    """Search for products by name using exact, partial, and fuzzy matching."""
    return _service.search_products(product_name, products, threshold)

def list_products_by_categories_formatted(
    limit_categories: int = 10,
    examples_per_category: int = 5
) -> str:
    """
    List products grouped by category in bullet-point format.

    MANDATORY: Call this when the user asks for products/catalog/inventory.
    """
    data = _service.list_products_by_categories_formatted(
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
