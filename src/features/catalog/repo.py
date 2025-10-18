from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from db.catalog import CatalogDB

class CatalogRepo:
    """
    Thin data-access adapter around CatalogDB.
    """
    def __init__(self, db: Optional[CatalogDB] = None):
        self.db = db or CatalogDB()

    # ---- CATALOG operations ----
    def put_catalog_item(self, tenant_id: str, catalog_item: Dict[str, Any]) -> None:
        self.db.put_catalog_item(tenant_id, catalog_item)

    def get_catalog_item(self, tenant_id: str, catalog_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_catalog_item(tenant_id, catalog_id)
    
    # Legacy alias for backward compatibility
    def get(self, tenant_id: str, catalog_id: str) -> Optional[Dict[str, Any]]:
        return self.get_catalog_item(tenant_id, catalog_id)

    def delete_catalog_item_and_variants(self, tenant_id: str, catalog_id: str) -> int:
        return self.db.delete_catalog_item_and_variants(tenant_id, catalog_id)

    # ---- VARIANT operations ----
    def put_variant(self, tenant_id: str, variant: Dict[str, Any]) -> None:
        self.db.put_variant(tenant_id, variant)

    def get_variant(self, tenant_id: str, variant_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_variant(tenant_id, variant_id)

    def list_variants_for_catalog_item(self, tenant_id: str, catalog_id: str, limit: int = 100, last_key: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        return self.db.list_variants_for_catalog_item(tenant_id, catalog_id, limit=limit, last_key=last_key)

    # ---- VARIANT updates ----
    def update_inventory(self, tenant_id: str, variant_id: str, available_qty: int, inventory_policy: Optional[str] = None) -> bool:
        return self.db.update_inventory(tenant_id, variant_id, available_qty, inventory_policy)

    def update_price(self, tenant_id: str, variant_id: str, new_price: float, compare_at: Optional[float] = None) -> bool:
        return self.db.update_price(tenant_id, variant_id, new_price, compare_at)

    def update_title(self, tenant_id: str, variant_id: str, new_title: str) -> bool:
        return self.db.update_title(tenant_id, variant_id, new_title)

    # ---- QUERIES (GSI-based) ----
    def list_by_category(self, tenant_id: str, category: str, limit: int = 24, last_key: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        return self.db.list_by_category(tenant_id, category, limit=limit, last_key=last_key)

    def list_by_tag(self, tenant_id: str, tag: str, limit: int = 24, last_key: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        return self.db.list_by_tag(tenant_id, tag, limit=limit, last_key=last_key)

    def list_by_collection(self, tenant_id: str, collection: str, limit: int = 24, last_key: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        return self.db.list_by_collection(tenant_id, collection, limit=limit, last_key=last_key)

    def search_title_prefix(self, tenant_id: str, prefix: str, limit: int = 20, last_key: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        return self.db.search_title_prefix(tenant_id, prefix, limit=limit, last_key=last_key)
