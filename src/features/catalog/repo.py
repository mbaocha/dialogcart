from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from db.catalog import CatalogDB

class CatalogRepo:
    """
    Thin data-access adapter around CatalogDB.
    """
    def __init__(self, db: Optional[CatalogDB] = None):
        self.db = db or CatalogDB()

    def put_catalog_item(self, tenant_id: str, catalog_item: Dict[str, Any]) -> None:
        self.db.put_catalog_item(tenant_id, catalog_item)

    def put_variant(self, tenant_id: str, variant: Dict[str, Any]) -> None:
        self.db.put_variant(tenant_id, variant)

    def get(self, tenant_id: str, catalog_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_catalog_item(tenant_id, catalog_id)

    def list_variants_for_catalog_item(self, tenant_id: str, catalog_id: str, limit: int = 100, last_key: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        return self.db.list_variants_for_catalog_item(tenant_id, catalog_id, limit=limit, last_key=last_key)

    def list_by_category(self, tenant_id: str, category: str, limit: int = 24, last_key: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        return self.db.list_by_category(tenant_id, category, limit=limit, last_key=last_key)

    def search_title_prefix(self, tenant_id: str, prefix: str, limit: int = 20, last_key: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        return self.db.search_title_prefix(tenant_id, prefix, limit=limit, last_key=last_key)
