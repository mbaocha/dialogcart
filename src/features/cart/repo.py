"""
Cart repository layer for data access operations.
"""

from typing import List, Dict, Optional, Any
from db.cart import CartDB
from db.catalog import CatalogDB


class CartRepo:
    """
    Thin data-access adapter around CartDB.
    """
    
    def __init__(self, db: Optional[CartDB] = None):
        self.db = db or CartDB()
        self.catalog_db = CatalogDB()
    
    def add_item(self, tenant_id: str, customer_id: str, variant_id: str,
                 catalog_id: str, title: str, qty: int, unit_price: float) -> Dict[str, Any]:
        """Add an item to the cart (tenant/customer scoped)."""
        return self.db.add_item(tenant_id=tenant_id, customer_id=customer_id, variant_id=variant_id,
                                catalog_id=catalog_id, title=title, qty=qty, unit_price=unit_price)
    
    def get_cart(self, tenant_id: str, customer_id: str) -> List[Dict[str, Any]]:
        """Get all items in a customer's cart with product details (tenant-scoped)."""
        cart_items = self.db.get_cart(tenant_id, customer_id)
        
        # Enrich cart items with product information
        enriched_items = []
        for item in cart_items:
            variant_id = item.get("variant_id")
            if variant_id:
                # fetch variant to enrich title/price if needed
                variant = self.catalog_db.get_variant(tenant_id, variant_id)
                product_title = (variant.get("title") if variant else item.get("title")) or ""
                unit_price = float(item.get("unit_price", 0))
                qty = int(item.get("qty", 1))
                line_total = float(item.get("total_price", "0") or 0)
                product_unit = (variant.get("unit") if variant else item.get("unit")) or "piece"
                enriched_item = {
                    **item,
                    # legacy/enriched fields
                    "product_title": product_title,
                    "unit_price": unit_price,
                    "qty": qty,
                    "line_total": line_total,
                    # normalized presenter-friendly fields
                    "product_name": product_title,
                    "product_unit": product_unit,
                    "product_price": unit_price,
                    "quantity": qty,
                }
                enriched_items.append(enriched_item)
        
        return enriched_items
    
    def remove_item(self, tenant_id: str, customer_id: str, variant_id: str) -> Dict[str, Any]:
        """Remove an item from the cart (tenant/customer scoped)."""
        return self.db.remove_item(tenant_id, customer_id, variant_id)
    
    def reduce_quantity(self, tenant_id: str, customer_id: str, variant_id: str, reduce_by: int) -> Dict[str, Any]:
        """Reduce the quantity of an item in the cart by the specified amount (tenant/customer scoped)."""
        return self.db.reduce_quantity(tenant_id, customer_id, variant_id, reduce_by)
    
    def update_quantity(self, tenant_id: str, customer_id: str, variant_id: str, qty: int) -> Dict[str, Any]:
        """Update the quantity of an item in the cart (tenant/customer scoped)."""
        return self.db.update_quantity(tenant_id, customer_id, variant_id, qty)
    
    def clear_cart(self, tenant_id: str, customer_id: str) -> Dict[str, Any]:
        """Clear all items from a customer's cart (tenant/customer scoped)."""
        return self.db.clear_cart(tenant_id, customer_id)

    def restore_cart(self, tenant_id: str, customer_id: str) -> Dict[str, Any]:
        """Restore cart from the latest valid backup (tenant/customer scoped)."""
        return self.db.restore_cart(tenant_id, customer_id)