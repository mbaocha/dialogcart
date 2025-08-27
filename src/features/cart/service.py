"""
Cart service layer for business logic operations.
"""

from typing import Dict, Any, Optional, List
from .repo import CartRepo
from .presenter import CartPresenter
from utils.response import standard_response
from decimal import Decimal


class CartService:
    """Service class for cart-related business operations."""
    
    def __init__(self, repo: Optional[CartRepo] = None, presenter: Optional[CartPresenter] = None):
        self.repo = repo or CartRepo()
        self.presenter = presenter or CartPresenter()
        # We'll need a product repo for product lookups
        from features.product.repo import ProductRepo
        self.product_repo = ProductRepo()
    
    def _as_float(self, x) -> float:
        if isinstance(x, Decimal):
            return float(x)
        try:
            return float(x)
        except Exception:
            return 0.0

    def _enabled_in_stock(self, p: Dict[str, Any]) -> bool:
        if p.get("status", "enabled") != "enabled":
            return False
        return self._as_float(p.get("available_quantity", 0)) > 0
    
    def add_item_to_cart(
        self,
        user_id: str,
        product_id: str,
        quantity: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Add to cart using product_id.
        Returns:
          - success True with {added_item, cart_contents}
          - success False with error in {"below_minimum","invalid_quantity","insufficient_stock","product_not_found_or_unavailable","missing_*"}
        """
        if not user_id:
            return standard_response(False, error="missing_user_id")
        if quantity is None:
            quantity = 1

        # --- Resolve product ---
        if not product_id:
            return standard_response(False, error="product_id_required")
            
        product = self.product_repo.get(product_id)
        if not product or not self._enabled_in_stock(product):
            return standard_response(False, error="product_not_found_or_unavailable")

        
        # --- Quantity / stock / allowed checks ---
        qty = self._as_float(quantity)
        if qty <= 0:
            return standard_response(False, error="invalid_quantity")

        # Check minimum quantity and adjust if needed
        aq = product.get("allowed_quantities")

        print(f"[DEBUG] add_item_to_cart -> aq: {aq}")
        if isinstance(aq, dict) and "min" in aq:
            try:
                min_qty = float(aq["min"])
                if qty < min_qty:
                    qty = min_qty  # Set to minimum instead of returning error
            except Exception:
                pass

        if isinstance(aq, list) and aq:
            min_val = min(int(v) for v in aq)
            if qty < min_val:
                qty = min_val

        # --- Perform add ---
        item = self.repo.add_item(user_id=user_id, product_id=product_id, quantity=qty)

        cart_contents = self.repo.get_cart(user_id)

        return standard_response(True, data={"added_item": item, "cart_contents": cart_contents})

    def get_cart(self, user_id: str) -> Dict[str, Any]:
        """Get all items in a user's cart."""
        if not user_id:
            return standard_response(False, error="user_id is required")
        
        try:
            items = self.repo.get_cart(user_id)
            
            # Calculate cart total from line totals
            cart_total = sum(item.get("line_total", 0) for item in items)
            
            return standard_response(True, data={
                "items": items,
                "cart_total": cart_total
            })
        except Exception as e:
            return standard_response(False, error=str(e))

    def get_cart_formatted(self, user_id: str) -> Dict[str, Any]:
        """Get cart items formatted in a bullet-point style similar to product listings."""
        if not user_id:
            return standard_response(False, error="user_id is required")
        
        try:
            items = self.repo.get_cart(user_id)
            
            if not items:
                return standard_response(True, data={
                    "text": "Your cart is empty. Add some products to get started! 🛒",
                    "items": [],
                    "cart_total": 0,
                    "style_used": "bullet"
                })
            
            # Calculate cart total
            cart_total = sum(item.get("line_total", 0) for item in items)
            
            # Format cart items in bullet points
            lines = []
            rows = []
            
            for idx, item in enumerate(items, start=1):
                product_emoji = item.get("product_emoji", "🛒")
                product_name = item.get("product_name", "Unknown Product")
                quantity = item.get("quantity", 1)
                unit = item.get("product_unit", "piece")
                price = item.get("product_price", 0)
                line_total = item.get("line_total", 0)
                
                # Format the line similar to product listings
                quantity_text = f"{quantity} {unit}"
                if quantity != 1:
                    # Handle pluralization
                    if unit.lower() in ["box", "boxes"]:
                        quantity_text = f"{quantity} boxes" if quantity != 1 else "1 box"
                    elif unit.lower() in ["bunch", "bunches"]:
                        quantity_text = f"{quantity} bunches" if quantity != 1 else "1 bunch"
                    elif unit.lower() in ["kg", "l"]:
                        quantity_text = f"{quantity} {unit}"
                    else:
                        quantity_text = f"{quantity} {unit}s" if quantity != 1 else f"1 {unit}"
                
                # Format price and line total
                price_text = f"${price:.2f}" if price else "$0.00"
                line_total_text = f"${line_total:.2f}" if line_total else "$0.00"
                
                # Create bullet line
                bullet_line = f"• {product_emoji} {product_name} — {quantity_text} @ {price_text} = {line_total_text}"
                lines.append(bullet_line)
                
                rows.append({
                    "index": idx,
                    "product_emoji": product_emoji,
                    "product_name": product_name,
                    "quantity": quantity,
                    "unit": unit,
                    "price": price,
                    "line_total": line_total,
                    "line": bullet_line
                })
            
            # Add cart total line
            total_line = f"\n💰 **Cart Total: ${cart_total:.2f}**"
            lines.append(total_line)
            
            return standard_response(True, data={
                "text": "\n".join(lines),
                "rows": rows,
                "items": items,
                "cart_total": cart_total,
                "style_used": "bullet"
            })
            
        except Exception as e:
            return standard_response(False, error=str(e))
    
    def remove_item(self, user_id: str, product_id: str) -> Dict[str, Any]:
        """Remove a specific product from a user's shopping cart."""
        if not user_id or not product_id:
            return standard_response(False, error="user_id and product_id are required")
        
        try:
            success = self.repo.remove_item(user_id, product_id)
            return standard_response(success, data={"removed": success} if success else None)
        except Exception as e:
            return standard_response(False, error=str(e))
    
    def update_cart_quantity(self, user_id: str, product_id: str, quantity: float, update_op: str = "set") -> Dict[str, Any]:
        """
        Update the quantity of a specific product in a user's shopping cart.
        
        Args:
            user_id: The user's ID
            product_id: The product ID to update
            quantity: The quantity value (absolute for 'set', relative for 'increase'/'decrease')
            update_op: Operation type - 'set' (default), 'increase', or 'decrease'
        
        Returns:
            Standard response with success status and data
        """
        if not user_id or not product_id or quantity is None:
            return standard_response(False, error="user_id, product_id, and quantity are required")
        
        if update_op not in ["set", "increase", "decrease"]:
            return standard_response(False, error="update_op must be 'set', 'increase', or 'decrease'")
        
        try:
            # Get current cart to find existing quantity
            current_cart = self.repo.get_cart(user_id)
            current_item = None
            
            # Find the specific product in the cart
            for item in current_cart:
                if item.get("product_id") == product_id:
                    current_item = item
                    break
            
            if not current_item:
                return standard_response(False, error="Product not found in cart")
            
            current_quantity = current_item.get("quantity", 0)
            final_quantity = quantity
            
            # Calculate final quantity based on operation
            if update_op == "increase":
                final_quantity = current_quantity + quantity
            elif update_op == "decrease":
                final_quantity = current_quantity - quantity
            # For "set", final_quantity remains as the provided quantity
            
            # Validate final quantity
            if final_quantity <= 0:
                # If quantity becomes 0 or negative, remove the item
                success = self.repo.remove_item(user_id, product_id)
                if success:
                    return standard_response(True, data={
                        "updated": True,
                        "operation": update_op,
                        "previous_quantity": current_quantity,
                        "final_quantity": 0,
                        "item_removed": True,
                        "message": f"Quantity updated to 0, item removed from cart"
                    })
                else:
                    return standard_response(False, error="Failed to remove item when quantity became 0")
            
            # Update with the calculated quantity
            success = self.repo.update_quantity(user_id, product_id, final_quantity)
            
            if success:
                return standard_response(True, data={
                    "updated": True,
                    "operation": update_op,
                    "previous_quantity": current_quantity,
                    "final_quantity": final_quantity,
                    "quantity_change": quantity if update_op != "set" else None,
                    "message": f"Quantity {update_op}d from {current_quantity} to {final_quantity}"
                })
            else:
                return standard_response(False, error="Failed to update quantity")
                
        except Exception as e:
            return standard_response(False, error=str(e))
    
    def clear_cart(self, user_id: str) -> Dict[str, Any]:
        """Remove all items from a user's shopping cart."""
        if not user_id:
            return standard_response(False, error="user_id is required")
        
        try:
            count = self.repo.clear_cart(user_id)
            return standard_response(True, data={"removed_items": count})
        except Exception as e:
            return standard_response(False, error=str(e))

    def restore_cart(self, user_id: str) -> Dict[str, Any]:
        """Restore cart from the latest valid backup by merging with current cart."""
        if not user_id:
            return standard_response(False, error="user_id is required")
        
        try:
            result = self.repo.restore_cart(user_id)
            
            if result.get("restored"):
                # Get updated cart contents after restoration
                cart_contents = self.repo.get_cart(user_id)
                
                return standard_response(True, data={
                    "restored": True,
                    "merge": result.get("merge", False),
                    "added_from_backup": result.get("added_from_backup", 0),
                    "increased_existing": result.get("increased_existing", 0),
                    "backup_id": result.get("backup_id"),
                    "cart_contents": cart_contents
                })
            else:
                return standard_response(False, error=result.get("reason", "restore_failed"))
                
        except Exception as e:
            return standard_response(False, error=str(e))

    def _remove_item(self, args) -> Dict[str, Any]:
        """Remove a specific product from a user's shopping cart."""
        user_id = args.get("user_id")
        product_id = args.get("product_name")
        products = args.get("products")
        if not user_id or not product_id:
            return standard_response(False, error="user_id and product_id are required")
        print(f"[DEBUG] _remove_item -> user_id: {user_id}, product_id: {product_id}")
        try:
            success = self.repo.remove_item(user_id, product_id)
            return standard_response(success, data={"removed": success} if success else None)
        except Exception as e:
            return standard_response(False, error=str(e))