from __future__ import annotations
from typing import Any, Dict, List, Optional
from db.product import ProductDB

class ProductRepo:
    """
    Thin data-access adapter around ProductDB.
    """
    def __init__(self, db: Optional[ProductDB] = None):
        self.db = db or ProductDB()

    def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.db.create_product(**payload)

    def get(self, product_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_product(product_id)

    def list(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.db.list_products(limit=limit)

    def search(self, product_name: str) -> List[Dict[str, Any]]:
        return self.db.search_products(product_name)
