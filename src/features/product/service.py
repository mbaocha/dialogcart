from __future__ import annotations
from typing import Any, Dict, List, Optional, Union
from decimal import Decimal

from .repo import ProductRepo
from .presenter import categories_bulleted, DEFAULT_CATEGORY_EMOJI
from utils.response import standard_response

class ProductService:
    """
    Pure business logic. Returns standard_response(...) to match current behavior.
    """

    def __init__(self, repo: Optional[ProductRepo] = None):
        self.repo = repo or ProductRepo()

    # ---- Commands ----
    def create_product(
        self,
        *,
        name: str,
        unit: str,
        price: float,
        allowed_quantities: Optional[Union[Dict[str, Any], List[int]]] = None,
        available_quantity: Optional[float] = None,
        category: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            item = self.repo.create({
                "name": name,
                "unit": unit,
                "price": price,
                "allowed_quantities": allowed_quantities,
                "available_quantity": available_quantity,
                "category": category,
                "description": description,
            })
            return standard_response(True, data=item)
        except ValueError as e:
            return standard_response(False, error=str(e))

    # ---- Queries ----
    def get_product(self, product_id: str) -> Dict[str, Any]:
        item = self.repo.get(product_id)
        return standard_response(True, data=item) if item else standard_response(False, error="Product not found")

    def list_products(self, limit: int = 100) -> Dict[str, Any]:
        items = self.repo.list(limit=limit)
        return standard_response(True, data=items)
    
    def list_products_flat(self, limit: int = 10_000) -> Dict[str, str]:
        """Returns a dictionary mapping product ID to product name."""
        res = self.list_products(limit=limit)      # returns standard_response
        if isinstance(res, dict) and res.get("success"):
            products = res["data"]
            return {product["id"]: product["name"] for product in products}
        return {}

    def search_products(self, product_name: str, products: Dict[str, str] = None, threshold: float = 0.6) -> Dict[str, Any]:
        """
        Search for products by name using exact, partial, and fuzzy matching.
        
        Args:
            product_name: The product name to search for
            products: Dictionary mapping product_id to product_name (optional, for enhanced search)
            threshold: Minimum similarity score for fuzzy matching (0.0 to 1.0), defaults to 0.6
            
        Returns:
            Standard response with list of matching products or error message
        """
        # If products dict is provided, use enhanced search with fuzzy matching
        if products:
            try:
                from utils.coreutil import search_in_list
                
                # Use search_in_list to find matching product IDs with custom threshold
                matching_ids = search_in_list(product_name, products, fallback_to_fuzzy=True, threshold=threshold)
                
                if not matching_ids:
                    return standard_response(False, error=f"No products found matching '{product_name}'")
                
                # Fetch full product details for each match
                matched_products = []
                for product_id in matching_ids:
                    product = self.repo.get(product_id)
                    if product:
                        status = product.get('status', 'enabled')
                        if status != 'enabled':
                            continue
                        matched_products.append(product)
                
                if not matched_products:
                    return standard_response(False, error="Failed to retrieve product details")
                
                return standard_response(True, data=matched_products)
                
            except Exception as e:
                return standard_response(False, error=f"Error searching similar products: {str(e)}")
        
        # Fallback to basic database search if no products dict provided
        items = self.repo.search(product_name)
        return standard_response(True, data=items)

    # ---- Aggregation / views ----
    def list_products_by_categories(self) -> Dict[str, Any]:
        """
        Matches your previous function:
        - list all
        - filter enabled & available_quantity > 0
        - group by category (default 'Uncategorized')
        - sort within category
        - prefix key label with emoji (from product[category_emoji] or default)
        """
        try:
            all_products = self.repo.list(limit=10_000)
            categories: Dict[str, List[Dict[str, Any]]] = {}

            for product in all_products:
                status = product.get('status', 'enabled')
                if status != 'enabled':
                    continue

                available_qty = product.get('available_quantity', 0)
                if isinstance(available_qty, Decimal):
                    available_qty = float(available_qty)
                if (available_qty or 0) <= 0:
                    continue

                category = product.get('category', 'Uncategorized')
                categories.setdefault(category, []).append(product)

            for category in list(categories.keys()):
                categories[category] = sorted(categories[category], key=lambda x: x.get("name", ""))

            result: Dict[str, List[Dict[str, Any]]] = {}
            for category, products in categories.items():
                cat_emoji = DEFAULT_CATEGORY_EMOJI
                if products:
                    cat_emoji = products[0].get('category_emoji', DEFAULT_CATEGORY_EMOJI)
                result[f"{cat_emoji} {category}"] = products

            return standard_response(True, data=result)
        except Exception as e:
            return standard_response(False, error=str(e))

    def list_products_by_categories_formatted(
        self,
        *,
        limit_categories: int = 10,
        examples_per_category: int = 2
    ) -> Dict[str, Any]:
        try:
            base = self.list_products_by_categories()
            if not base.get("success"):
                return base
            categories = base["data"]  # keys already include emoji
            beautified = categories_bulleted(
                categories=categories,
                limit_categories=limit_categories,
                examples_per_category=examples_per_category,
                default_category_emoji=DEFAULT_CATEGORY_EMOJI,
            )
            return standard_response(True, data=beautified)
        except Exception as e:
            return standard_response(False, error=str(e))


