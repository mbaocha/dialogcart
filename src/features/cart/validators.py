# Cart-specific tool argument validators

from typing import Any, Dict
from ..validators.base import BaseValidator
from ..validators.common import coerce_positive_int, resolve_product
from .service import CartService


class CartValidator(BaseValidator):
    # Validator for cart-related tool operations
    
    def validate_add_item_to_cart(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # Requires product; normalizes quantity (default 1)
        cleaned = dict(args)
        products = args.get('products', {})

        # normalize qty
        qty = cleaned.get("quantity", cleaned.get("qty", 1))
        cleaned["quantity"] = cleaned["qty"] = coerce_positive_int(qty, default=1)
        print("[DEBUG] add_item_to_cart -> cleaned:", cleaned)

        ok, pid, candidates, reason = resolve_product(cleaned.get("product_name"), products)
        meta = {
            "ok": ok and reason is None,
            "needs_clarification": not ok,
            "reason": reason,
            "candidates": candidates,
            "product_id": pid,
            "cleaned_args": cleaned,
        }
        if ok and pid:
            cleaned["product_id"] = pid
        return meta

    def validate_remove_item_from_cart(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # Requires product; quantity optional (if present, normalize)
        cleaned = dict(args)
        
        # Get products currently in cart
        cart_response = CartService().get_cart(args.get("user_id"))
        cart_items = cart_response.get("data", {}).get("items", []) if cart_response.get("success") else []
        products_in_cart = {item["product_id"]: item["product_name"] for item in cart_items} if cart_items else {}

        if "quantity" in cleaned or "qty" in cleaned:
            qty = cleaned.get("quantity", cleaned.get("qty"))
            cleaned["quantity"] = cleaned["qty"] = coerce_positive_int(qty, default=1)

        ok, pid, candidates, reason = resolve_product(cleaned.get("product_name"), products_in_cart)
        meta = {
            "ok": ok and reason is None,
            "needs_clarification": not ok,
            "reason": reason,
            "candidates": candidates,
            "product_id": pid,
            "cleaned_args": cleaned,
        }
        if ok and pid:
            cleaned["product_id"] = pid
        print("[DEBUG] remove_item_from_cart -> cleaned:", cleaned)
        return meta

    def validate_update_cart_quantity(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # Supports either:
        #   - set: {product_name, quantity}
        #   - increment: {product_name, delta}
        # The tool can compute final target from delta using current cart
        cleaned = dict(args)

        cart_response = CartService().get_cart(args.get("user_id"))
        cart_items = cart_response.get("data", {}).get("items", []) if cart_response.get("success") else []
        products_in_cart = {item["product_id"]: item["product_name"] for item in cart_items} if cart_items else {}

        if "quantity" in cleaned:
            cleaned["quantity"] = coerce_positive_int(cleaned["quantity"], default=1)
        if "delta" in cleaned:
            cleaned["delta"] = coerce_positive_int(cleaned["delta"], default=1)

        ok, pid, candidates, reason = resolve_product(cleaned.get("product_name"), products_in_cart)
        if not ok:
            return {
                "ok": False,
                "needs_clarification": True,
                "reason": reason,
                "candidates": candidates,
                "product_id": None,
                "cleaned_args": cleaned,
            }

        if cleaned.get("quantity") is None and cleaned.get("delta") is None:
            return {
                "ok": False,
                "needs_clarification": True,
                "reason": "missing_quantity",
                "candidates": [],
                "product_id": pid,
                "cleaned_args": cleaned,
            }

        cleaned["product_id"] = pid
        print("[DEBUG] update_cart_quantity -> cleaned:", cleaned)
        return {
            "ok": True,
            "needs_clarification": False,
            "reason": None,
            "candidates": [],
            "product_id": pid,
            "cleaned_args": cleaned,
        }

    def validate(self, args: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        # Default validation method (not used in current implementation)
        return {
            "ok": True,
            "needs_clarification": False,
            "reason": None,
            "candidates": [],
            "cleaned_args": args,
        } 