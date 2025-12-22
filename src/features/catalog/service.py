from __future__ import annotations
from typing import Any, Dict, List, Optional
from decimal import Decimal

from .repo import CatalogRepo
from .search import CatalogSearchService
from .presenter import categories_bulleted, DEFAULT_CATEGORY_EMOJI
from utils.response import standard_response


class CatalogService:
    """
    Customer-facing catalog service for read-only operations.

    Admin operations (create, update, delete) should be handled by a separate admin service.
    This service focuses on browsing, searching, and retrieving catalog data for customers.
    """

    def __init__(self, repo: Optional[CatalogRepo] = None):
        self.repo = repo or CatalogRepo()
        self.search_service = CatalogSearchService()

    # ---- Queries ----
    def get(self, tenant_id: str, catalog_id: str) -> Optional[Dict[str, Any]]:
        return self.repo.get_catalog_item(tenant_id, catalog_id)

    def get_catalog_item(self, catalog_id: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        if not tenant_id:
            return standard_response(False, error="tenant_id is required")
        item = self.repo.get_catalog_item(tenant_id, catalog_id)
        return standard_response(True, data=item) if item else standard_response(False, error="Catalog item not found")

    def get_variant(self, tenant_id: str, variant_id: str) -> Dict[str, Any]:
        """Get a specific variant by variant_id."""
        variant = self.repo.get_variant(tenant_id, variant_id)
        return standard_response(True, data=variant) if variant else standard_response(False, error="Variant not found")

    def list_variants_for_catalog_item(self, tenant_id: str, catalog_id: str, limit: int = 100) -> Dict[str, Any]:
        """List all variants for a given catalog item (customer-facing, for viewing product options)."""
        try:
            variants, last_key = self.repo.list_variants_for_catalog_item(
                tenant_id, catalog_id, limit=limit)
            return standard_response(True, data={"variants": variants, "last_key": last_key})
        except Exception as e:
            return standard_response(False, error=str(e))

    def list_catalog_by_categories(self, tenant_id: str) -> Dict[str, Any]:
        """
        - list variants from catalog and reconstruct products by variant title/name
        - filter active status & in_stock = True
        - group by category (default 'Uncategorized')
        - sort within category
        - prefix key label with emoji

        Aligned with new db/catalog.py schema.
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
                # Check status: should be "active" (not "enabled" from old schema)
                status = variant.get('status', 'active')
                if status not in ('active', 'enabled'):  # Support both for transition
                    continue

                # Use available_qty from new schema
                available_qty = variant.get('available_qty', 0)
                if isinstance(available_qty, Decimal):
                    available_qty = float(available_qty)
                if available_qty <= 0:
                    continue

                # Extract category_name (new schema has this as dedicated field)
                category = variant.get('category_name', 'Uncategorized')
                if not category:
                    # Fallback to category.name if category is a map
                    cat_obj = variant.get('category')
                    if isinstance(cat_obj, dict):
                        category = cat_obj.get('name', 'Uncategorized')
                    else:
                        category = 'Uncategorized'

                # Build catalog item view matching presenter expectations
                catalog_item_view = {
                    "id": variant.get("catalog_id") or variant.get("variant_id"),
                    "name": variant.get("title") or variant.get("variant_title") or "",
                    # May be in rules.unit in new schema
                    "unit": variant.get("unit"),
                    "price": float(variant.get("price_num", 0)) if isinstance(variant.get("price_num"), (int, float, Decimal)) else 0,
                    "available_quantity": available_qty,
                    "category": category,
                    "category_emoji": variant.get('category_emoji', DEFAULT_CATEGORY_EMOJI),
                }

                # Extract unit and allowed_quantities from rules if present
                rules = variant.get("rules")
                if rules and isinstance(rules, dict):
                    if "unit" in rules:
                        catalog_item_view["unit"] = rules["unit"]
                    if "allowed_quantities" in rules:
                        catalog_item_view["allowed_quantities"] = rules["allowed_quantities"]
                    if "min_order_qty" in rules:
                        catalog_item_view["allowed_quantities"] = {
                            "min": rules["min_order_qty"]}

                categories.setdefault(category, []).append(catalog_item_view)

            for category in list(categories.keys()):
                categories[category] = sorted(
                    categories[category], key=lambda x: x.get("name", ""))

            result: Dict[str, List[Dict[str, Any]]] = {}
            for category, catalog_items in categories.items():
                cat_emoji = DEFAULT_CATEGORY_EMOJI
                if catalog_items:
                    cat_emoji = catalog_items[0].get(
                        'category_emoji', DEFAULT_CATEGORY_EMOJI)
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
        """
        Build flat map from variants {catalog_id: title}.
        Aligned with new db/catalog.py schema.
        """
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
                # Use catalog_id from new schema (not product_id)
                cid = v.get("catalog_id") or v.get("variant_id")
                title = v.get("title") or v.get("variant_title")
                if cid and title:
                    acc[cid] = title
            fetched = len(acc)
            last_evaluated_key = resp.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break
        return acc

    def search_catalog(self, catalog_name: str, catalog_items: Dict[str, str] = None, threshold: float = 0.6, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Search for catalog items by name using GSI4_TitlePrefix for prefix matching,
        with fallback to fuzzy matching if needed.

        Args:
            catalog_name: The name to search for
            catalog_items: Optional pre-loaded catalog dictionary. If None, will use GSI4 search.
            threshold: Minimum similarity score for fuzzy matching (0.0 to 1.0)
            tenant_id: Tenant ID (required if catalog_items is None)

        Returns:
            Dict with success status and search results
        """
        try:
            # If catalog_items provided, use legacy fuzzy search
            if catalog_items is not None:
                from utils.coreutil import search_in_list
                matches = search_in_list(
                    catalog_name, catalog_items, fallback_to_fuzzy=True, threshold=threshold)

                if not matches:
                    return standard_response(False, error=f"No catalog items found matching '{catalog_name}'")

                results = []
                for catalog_id in matches:
                    catalog_name_found = catalog_items.get(
                        catalog_id, catalog_id)
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

            # Use GSI4_TitlePrefix for efficient prefix search
            if not tenant_id:
                tenant_id = getattr(self, '_tenant_id', 'demo-tenant-001')

            variants, _ = self.repo.search_title_prefix(
                tenant_id, catalog_name, limit=20)

            if not variants:
                return standard_response(False, error=f"No catalog items found matching '{catalog_name}'")

            # Deduplicate by catalog_id and format results
            seen = set()
            results = []
            for variant in variants:
                catalog_id = variant.get(
                    "catalog_id") or variant.get("variant_id")
                if catalog_id in seen:
                    continue
                seen.add(catalog_id)

                results.append({
                    "id": catalog_id,
                    "name": variant.get("title") or variant.get("variant_title") or "",
                    "available": variant.get("in_stock", False),
                    "price": float(variant.get("price_num", 0)) if variant.get("price_num") else None,
                    "variant_id": variant.get("variant_id"),
                })

            return standard_response(True, data={
                "query": catalog_name,
                "matches": results,
                "count": len(results)
            })

        except Exception as e:
            return standard_response(False, error=f"Search failed: {str(e)}")

    def search_catalog_by_attributes(
        self,
        tenant_id: str,
        attributes: Dict[str, Any],
        fuzzy_threshold: float = 0.65,
        limit: int = 20
    ) -> Dict[str, Any]:
        """
        Smart catalog search with fuzzy matching, synonym expansion, and flexible filtering.

        Args:
            tenant_id: Tenant ID
            attributes: Search criteria dictionary:
                {
                    "product": "shoes",           # Product name/type (required)
                    "brand": "nike",              # Brand/vendor (optional)
                    "variants": ["black", "42"]   # Variant attributes like color, size (optional)
                }
            fuzzy_threshold: Minimum similarity score (0.0-1.0), default 0.65
            limit: Maximum results to return, default 20

        Returns:
            Standard response with matches and smart presentation based on query type

        Examples:
            # General product search
            service.search_catalog_by_attributes(
                tenant_id="demo-001",
                attributes={"product": "shoes"}
            )

            # Search with brand
            service.search_catalog_by_attributes(
                tenant_id="demo-001",
                attributes={"product": "shoes", "brand": "nike"}
            )

            # Search with variant filter (shows size options)
            service.search_catalog_by_attributes(
                tenant_id="demo-001",
                attributes={"product": "shoes", "variants": ["black"]}
            )

            # Specific SKU search
            service.search_catalog_by_attributes(
                tenant_id="demo-001",
                attributes={"product": "shoes", "variants": ["black", "42"]}
            )
        """
        return self.search_service.search_by_attributes(
            tenant_id=tenant_id,
            attributes=attributes,
            fuzzy_threshold=fuzzy_threshold,
            limit=limit
        )
