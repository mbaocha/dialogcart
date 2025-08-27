"""
Cart repository layer for data access operations.
"""

from typing import List, Dict, Optional, Any
from db.cart import CartDB
from db.product import ProductDB


class CartRepo:
    """
    Thin data-access adapter around CartDB.
    """
    
    def __init__(self, db: Optional[CartDB] = None):
        self.db = db or CartDB()
        self.product_db = ProductDB()
    
    def add_item(self, user_id: str, product_id: str, quantity: float) -> Dict[str, Any]:
        """Add an item to the cart."""
        return self.db.add_item(user_id=user_id, product_id=product_id, quantity=quantity)
    
    def get_cart(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all items in a user's cart with product details."""
        cart_items = self.db.get_cart(user_id)
        
        # Enrich cart items with product information
        enriched_items = []
        for item in cart_items:
            product_id = item.get("product_id")
            if product_id:
                product = self.product_db.get_product(product_id)
                if product:
                    quantity = item.get("quantity", 1)
                    product_price = product.get("price", 0)
                    line_total = quantity * product_price
                    enriched_item = {
                        **item,
                        "product_name": product.get("name", "Unknown Product"),
                        "product_emoji": product.get("category_emoji", "🛒"),  # Default emoji if none
                        "product_unit": product.get("unit", "piece"),
                        "product_price": product_price,
                        "line_total": line_total
                    }
                    enriched_items.append(enriched_item)
                else:
                    # Product not found, add with default values
                    quantity = item.get("quantity", 1)
                    line_total = quantity * 0
                    enriched_item = {
                        **item,
                        "product_name": "Product Not Found",
                        "product_emoji": "❓",
                        "product_unit": "piece",
                        "product_price": 0,
                        "line_total": line_total
                    }
                    enriched_items.append(enriched_item)
        
        return enriched_items
    
    def remove_item(self, user_id: str, product_id: str) -> bool:
        """Remove an item from the cart."""
        return self.db.remove_item(user_id, product_id)
    
    def update_quantity(self, user_id: str, product_id: str, quantity: float) -> bool:
        """Update the quantity of an item in the cart."""
        return self.db.update_quantity(user_id, product_id, quantity)
    
    def clear_cart(self, user_id: str) -> int:
        """Clear all items from a user's cart."""
        return self.db.clear_cart(user_id)

    def restore_cart(self, user_id: str) -> Dict[str, Any]:
        """Restore cart from the latest valid backup by merging with current cart."""
        return self.db.restore_cart(user_id)