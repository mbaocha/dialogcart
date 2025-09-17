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
        # Catalog lookups
        from features.catalog.service import CatalogService
        from features.catalog.repo import CatalogRepo
        self.catalog_service = CatalogService()
        self.catalog_repo = CatalogRepo()
    
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
        catalog_id: str,
        quantity: Optional[float] = None,
        *,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Add to cart using catalog_id.
        Returns:
          - success True with {added_item, cart_contents}
          - success False with error in {"below_minimum","invalid_quantity","insufficient_stock","product_not_found_or_unavailable","missing_*"}
        """
        if not user_id:
            return standard_response(False, error="missing_user_id")
        if quantity is None:
            quantity = 1

        # --- Resolve product ---
        if not catalog_id:
            return standard_response(False, error="catalog_id_required")
        
        if not tenant_id:
            # As a fallback, try default tenant
            from features.customer import _default_tenant_id
            tenant_id = _default_tenant_id()
        
        # Prefer in-stock variant for the product
        try:
            variants, _ = self.catalog_repo.list_variants_for_catalog_item(tenant_id, catalog_id, limit=50)
        except Exception as e:
            variants = []
        chosen = None
        for v in variants:
            in_stock = v.get("in_stock")
            available_qty = v.get("available_qty") or v.get("available_quantity") or 0
            if in_stock or self._as_float(available_qty) > 0:
                chosen = v
                break
        if not chosen and variants:
            chosen = variants[0]
        if not chosen:
            return standard_response(False, error="product_not_found_or_unavailable")

        # --- Quantity / stock / allowed checks ---
        qty = self._as_float(quantity)
        if qty <= 0:
            return standard_response(False, error="invalid_quantity")

        # Check minimum quantity and adjust if needed
        aq = (chosen.get("rules") or {}).get("allowed_quantities") or chosen.get("allowed_quantities")

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
        variant_id = chosen.get("variant_id")
        title = chosen.get("title") or chosen.get("variant_title") or ""
        price_num = chosen.get("price_num")
        unit_price = self._as_float(price_num)

        item = self.repo.add_item(
            tenant_id=tenant_id,
            customer_id=user_id,
            variant_id=variant_id,
            catalog_id=catalog_id,
            title=title,
            qty=int(qty),
            unit_price=unit_price,
        )

        cart_contents = self.repo.get_cart(tenant_id, user_id)

        return standard_response(True, data={"added_item": item, "cart_contents": cart_contents})

    def get_cart(self, user_id: str, *, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Get all items in a user's cart."""
        if not user_id:
            return standard_response(False, error="user_id is required")
        
        try:
            if not tenant_id:
                from features.customer import _default_tenant_id
                tenant_id = _default_tenant_id()
            items = self.repo.get_cart(tenant_id, user_id)
            
            # Calculate cart total from line totals
            cart_total = sum(item.get("line_total", 0) for item in items)
            
            return standard_response(True, data={
                "items": items,
                "cart_total": cart_total
            })
        except Exception as e:
            return standard_response(False, error=str(e))

    def get_cart_formatted(self, user_id: str, *, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Get cart items formatted in a bullet-point style similar to product listings."""
        if not user_id:
            return standard_response(False, error="user_id is required")
        
        try:
            if not tenant_id:
                from features.customer import _default_tenant_id
                tenant_id = _default_tenant_id()
            items = self.repo.get_cart(tenant_id, user_id)
            
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
    
    def remove_item(self, user_id: str, catalog_id: str, *, tenant_id: Optional[str] = None, quantity: Optional[int] = None) -> Dict[str, Any]:
        """Remove a specific catalog item from a user's shopping cart.
        Resolves the variant_id from the user's cart for the given catalog_id.
        
        Args:
            user_id: The user's ID
            catalog_id: The catalog ID to remove
            tenant_id: Optional tenant ID
            quantity: Optional quantity to reduce by. If None, removes the entire item.
        """
        if not user_id or not catalog_id:
            return standard_response(False, error="user_id and catalog_id are required")

        try:
            if not tenant_id:
                from features.customer import _default_tenant_id
                tenant_id = _default_tenant_id()

            # Find the matching variant in the user's cart for this catalog_id
            items = self.repo.get_cart(tenant_id, user_id)
            match_variant_id: Optional[str] = None
            current_quantity: Optional[int] = None
            for it in items:
                if it.get("catalog_id") == catalog_id or it.get("product_id") == catalog_id:
                    match_variant_id = it.get("variant_id")
                    current_quantity = it.get("qty", 0)
                    break

            if not match_variant_id:
                return standard_response(False, error="catalog_item_not_found_in_cart")

            if quantity is None:
                # Remove the entire item
                success = self.repo.remove_item(tenant_id, user_id, match_variant_id)
                return standard_response(success, data={"removed": success, "action": "removed_entirely"} if success else None)
            else:
                # Reduce quantity by the specified amount
                if quantity <= 0:
                    return standard_response(False, error="quantity_must_be_positive")
                
                if quantity >= current_quantity:
                    # If reducing by >= current quantity, remove the entire item
                    success = self.repo.remove_item(tenant_id, user_id, match_variant_id)
                    return standard_response(success, data={"removed": success, "action": "removed_entirely", "reduced_by": quantity} if success else None)
                else:
                    # Reduce the quantity
                    success = self.repo.reduce_quantity(tenant_id, user_id, match_variant_id, quantity)
                    return standard_response(success, data={"reduced": success, "action": "reduced_quantity", "reduced_by": quantity, "remaining": current_quantity - quantity} if success else None)
        except Exception as e:
            return standard_response(False, error=str(e))
    
    def update_cart_quantity(self, user_id: str, catalog_id: str, quantity: float, update_op: str = "set", *, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Update the quantity of a specific catalog item in a user's shopping cart.
        
        Args:
            user_id: The user's ID
            catalog_id: The catalog ID to update
            quantity: The quantity value (absolute for 'set', relative for 'increase'/'decrease')
            update_op: Operation type - 'set' (default), 'increase', or 'decrease'
        
        Returns:
            Standard response with success status and data
        """
        if not user_id or not catalog_id or quantity is None:
            return standard_response(False, error="user_id, catalog_id, and quantity are required")
        
        if update_op not in ["set", "increase", "decrease"]:
            return standard_response(False, error="update_op must be 'set', 'increase', or 'decrease'")
        
        try:
            # Ensure tenant id
            if not tenant_id:
                from features.customer import _default_tenant_id
                tenant_id = _default_tenant_id()

            # Get current cart to find existing quantity and variant
            current_cart = self.repo.get_cart(tenant_id, user_id)
            current_item = None
            variant_id: Optional[str] = None
            
            # Find the specific product in the cart
            for item in current_cart:
                if item.get("catalog_id") == catalog_id or item.get("product_id") == catalog_id:
                    current_item = item
                    variant_id = item.get("variant_id")
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
                # If quantity becomes 0 or negative, remove the item by variant
                if not variant_id:
                    return standard_response(False, error="variant_not_found_for_item")
                success = self.repo.remove_item(tenant_id, user_id, variant_id)
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
            if not variant_id:
                return standard_response(False, error="variant_not_found_for_item")
            success = self.repo.update_quantity(tenant_id, user_id, variant_id, int(final_quantity))
            
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