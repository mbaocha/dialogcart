from __future__ import annotations
from typing import Any, Dict, List, Optional, Union
from decimal import Decimal

from .repo import CatalogRepo
from .presenter import categories_bulleted, DEFAULT_CATEGORY_EMOJI
from utils.response import standard_response

class CatalogService:
    """
    Pure business logic. Returns standard_response(...) to match current behavior.
    """

    def __init__(self, repo: Optional[CatalogRepo] = None):
        self.repo = repo or CatalogRepo()

    # ---- Commands ----
    def create_catalog_item(
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
            # This path is not wired to Dynamo-backed repo in this codebase; keeping placeholder
            item = {
                "id": name.lower().replace(" ", "-"),
                "name": name,
                "unit": unit,
                "price": price,
                "allowed_quantities": allowed_quantities,
                "available_quantity": available_quantity,
                "category": category,
                "description": description,
            }
            return standard_response(True, data=item)
        except ValueError as e:
            return standard_response(False, error=str(e))

    # ---- Queries ----
    def get(self, tenant_id: str, catalog_id: str) -> Optional[Dict[str, Any]]:
        return self.repo.get(tenant_id, catalog_id)

    def get_catalog_item(self, catalog_id: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        if not tenant_id:
            return standard_response(False, error="tenant_id is required")
        item = self.repo.get(tenant_id, catalog_id)
        return standard_response(True, data=item) if item else standard_response(False, error="Catalog item not found")

    def list_catalog_by_categories(self, tenant_id: str) -> Dict[str, Any]:
        """
        - list variants from catalog and reconstruct products by variant title/name
        - filter enabled & available_quantity > 0
        - group by category (default 'Uncategorized')
        - sort within category
        - prefix key label with emoji
        """
        try:
            # Fetch a sample of in-stock variants by scanning the tenant partition (avoid empty prefix queries)
            from db.catalog import CatalogDB, pk_tenant
            from boto3.dynamodb.conditions import Key, Attr
            db = CatalogDB()

            items: List[Dict[str, Any]] = []
            last_evaluated_key = None
            # Pull up to ~200 items to render a representative catalog
            while len(items) < 200:
                kwargs = {
                    "KeyConditionExpression": Key("PK").eq(pk_tenant(tenant_id)),
                    "FilterExpression": Attr("entity").eq("VARIANT") & Attr("in_stock").eq(True),
                    "Limit": 100,
                }
                if last_evaluated_key:
                    kwargs["ExclusiveStartKey"] = last_evaluated_key
                resp = db.table.query(**kwargs)
                items.extend(resp.get("Items", []))
                last_evaluated_key = resp.get("LastEvaluatedKey")
                if not last_evaluated_key:
                    break

            categories: Dict[str, List[Dict[str, Any]]] = {}
            for variant in items:
                status = variant.get('status', 'enabled')
                if status != 'enabled':
                    continue
                available_qty = variant.get('available_qty') or variant.get('available_quantity') or 0
                if isinstance(available_qty, Decimal):
                    available_qty = float(available_qty)
                if (available_qty or 0) <= 0:
                    continue
                category = variant.get('category_name') or variant.get('category') or 'Uncategorized'
                catalog_item_view = {
                    "id": variant.get("catalog_id") or variant.get("product_id") or variant.get("variant_id"),
                    "name": variant.get("title") or variant.get("variant_title") or "",
                    "unit": variant.get("unit"),
                    "price": float(variant.get("price_num", 0)) if isinstance(variant.get("price_num"), (int, float, Decimal)) else 0,
                    "available_quantity": available_qty,
                    "category": category,
                    "category_emoji": variant.get('category_emoji', DEFAULT_CATEGORY_EMOJI),
                }
                categories.setdefault(category, []).append(catalog_item_view)

            for category in list(categories.keys()):
                categories[category] = sorted(categories[category], key=lambda x: x.get("name", ""))

            result: Dict[str, List[Dict[str, Any]]] = {}
            for category, catalog_items in categories.items():
                cat_emoji = DEFAULT_CATEGORY_EMOJI
                if catalog_items:
                    cat_emoji = catalog_items[0].get('category_emoji', DEFAULT_CATEGORY_EMOJI)
                result[f"{cat_emoji} {category}"] = catalog_items

            return standard_response(True, data=result)
        except Exception as e:
            return standard_response(False, error=str(e))

    def list_catalog_by_categories_formatted(
        self,
        *,
        tenant_id: str,
        limit_categories: int = 10,
        examples_per_category: int = 2
    ) -> Dict[str, Any]:
        try:
            base = self.list_catalog_by_categories(tenant_id)
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

    def list_catalog_flat(self, tenant_id: str, limit: int = 10_000) -> Dict[str, str]:
        # Build flat map from variants
        from db.catalog import CatalogDB, pk_tenant
        from boto3.dynamodb.conditions import Key, Attr
        db = CatalogDB()
        acc: Dict[str, str] = {}
        last_evaluated_key = None
        fetched = 0
        while fetched < limit:
            kwargs = {
                "KeyConditionExpression": Key("PK").eq(pk_tenant(tenant_id)),
                "FilterExpression": Attr("entity").eq("VARIANT") & Attr("in_stock").eq(True),
                "Limit": min(200, limit - fetched),
            }
            if last_evaluated_key:
                kwargs["ExclusiveStartKey"] = last_evaluated_key
            resp = db.table.query(**kwargs)
            for v in resp.get("Items", []):
                cid = v.get("catalog_id") or v.get("product_id") or v.get("variant_id")
                title = v.get("title") or v.get("variant_title")
                if cid and title:
                    acc[cid] = title
            fetched = len(acc)
            last_evaluated_key = resp.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break
        return acc

    def search_catalog(self, catalog_name: str, catalog_items: Dict[str, str] = None, threshold: float = 0.6) -> Dict[str, Any]:
        """
        Search for catalog items by name using exact, partial, and fuzzy matching.
        
        Args:
            catalog_name: The name to search for
            catalog_items: Optional pre-loaded catalog dictionary. If None, will load from database.
            threshold: Minimum similarity score for fuzzy matching (0.0 to 1.0)
            
        Returns:
            Dict with success status and search results
        """
        try:
            from utils.coreutil import search_in_list
            
            # Use provided catalog items or load from database
            if catalog_items is None:
                tenant_id = getattr(self, '_tenant_id', 'demo-tenant-001')
                catalog_items = self.list_catalog_flat(tenant_id)
            
            # Search for matches
            matches = search_in_list(catalog_name, catalog_items, fallback_to_fuzzy=True, threshold=threshold)
            
            if not matches:
                return standard_response(False, error=f"No catalog items found matching '{catalog_name}'")
            
            # Get catalog item details for matches
            results = []
            for catalog_id in matches:
                catalog_name_found = catalog_items.get(catalog_id, catalog_id)
                results.append({
                    "id": catalog_id,
                    "name": catalog_name_found,
                    "available": True
                })
            
            return standard_response(True, data={
                "query": catalog_name,
                "matches": results,
                "count": len(results)
            })
            
        except Exception as e:
            return standard_response(False, error=f"Search failed: {str(e)}")
