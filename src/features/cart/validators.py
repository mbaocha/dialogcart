# Cart-specific tool argument validators

from typing import Any, Dict, List, Optional
from ..validators.base import BaseValidator
from ..validators.common import coerce_positive_int, resolve_catalog_item
from .service import CartService
from utils.coreutil import search_similar


class CartValidator(BaseValidator):
    # Validator for cart-related tool operations
    
    def validate_add_item_to_cart(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # Requires catalog item; normalizes quantity (default 1)
        cleaned = dict(args)
        catalog_items = args.get('catalog_items', {})

        # normalize qty
        qty = cleaned.get("quantity", cleaned.get("qty", 1))
        cleaned["quantity"] = cleaned["qty"] = coerce_positive_int(qty, default=1)
        print("[DEBUG] add_item_to_cart -> cleaned:", cleaned)

        ok, cid, candidates, reason = resolve_catalog_item(cleaned.get("catalog_name"), catalog_items)
        meta = {
            "ok": ok and reason is None,
            "needs_clarification": not ok,
            "reason": reason,
            "candidates": candidates,
            "catalog_id": cid,
            "cleaned_args": cleaned,
        }
        if ok and cid:
            cleaned["catalog_id"] = cid
        return meta

    def validate_remove_item_from_cart(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # Requires product; quantity optional (if present, normalize)
        cleaned = dict(args)

        # Prefer customer_id; fallback to user_id for legacy compatibility
        customer_id = args.get("customer_id") or args.get("user_id")

        # Get products currently in cart (id -> name)
        products_in_cart: Dict[str, str] = {}
        try:
            cart_response = CartService().get_cart(customer_id)
            cart_items = cart_response.get("data", {}).get("items", []) if cart_response and cart_response.get("success") else []
            if cart_items:
                products_in_cart = {item.get("catalog_id") or item.get("product_id"): (item.get("product_name") or item.get("title") or "") for item in cart_items if item.get("product_id") or item.get("catalog_id")}
        except Exception:
            products_in_cart = {}

        # Normalize quantity if present (default 1 if provided but invalid)
        if "quantity" in cleaned or "qty" in cleaned:
            qty = cleaned.get("quantity", cleaned.get("qty"))
            cleaned["quantity"] = cleaned["qty"] = coerce_positive_int(qty, default=1)

        # Safer resolution: exact id, exact name, contains, then fuzzy (cart-only)
        def _resolve_from_cart(name: Optional[str]) -> (Optional[str], List[str], Optional[str]):
            if not name:
                return None, [], "missing_catalog_name"
            name_l = str(name).lower().strip()
            if not products_in_cart:
                return None, [], "catalog_item_not_found"

            # 1) exact id
            if name in products_in_cart:
                return name, [], None

            # 2) exact name
            for pid, pname in products_in_cart.items():
                if isinstance(pname, str) and pname.lower().strip() == name_l:
                    return pid, [], None

            # 3) contains match
            contains_matches = [pid for pid, pname in products_in_cart.items()
                                if isinstance(pname, str) and name_l in pname.lower()]
            if len(contains_matches) == 1:
                return contains_matches[0], [], None
            if len(contains_matches) > 1:
                return None, contains_matches[:10], "ambiguous_catalog_item"

            # 4) fuzzy within cart with high threshold; accept only single
            fuzzy_hits = search_similar(name, products_in_cart, threshold=0.9, max_results=3) or []
            if len(fuzzy_hits) == 1:
                return fuzzy_hits[0][0], [], None
            if len(fuzzy_hits) > 1:
                return None, [pid for pid, _ in fuzzy_hits][:10], "ambiguous_catalog_item"

            return None, [], "catalog_item_not_found"

        pid, candidates, reason = _resolve_from_cart(cleaned.get("catalog_name"))

        meta = {
            "ok": pid is not None and reason is None,
            "needs_clarification": pid is None,
            "reason": reason,
            "candidates": candidates,
            "catalog_id": pid,
            "cleaned_args": cleaned,
        }
        if pid:
            cleaned["catalog_id"] = pid
        print("[DEBUG] remove_item_from_cart -> cleaned:", cleaned)
        return meta

    def validate_update_cart_quantity(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # Supports either:
        #   - set: {catalog_name, quantity}
        #   - increment: {catalog_name, delta}
        # The tool can compute final target from delta using current cart
        cleaned = dict(args)

        cart_response = CartService().get_cart(args.get("user_id"))
        cart_items = cart_response.get("data", {}).get("items", []) if cart_response.get("success") else []
        products_in_cart = { (item.get("catalog_id") or item.get("product_id")): item.get("product_name") for item in cart_items } if cart_items else {}

        if "quantity" in cleaned:
            cleaned["quantity"] = coerce_positive_int(cleaned["quantity"], default=1)
        if "delta" in cleaned:
            cleaned["delta"] = coerce_positive_int(cleaned["delta"], default=1)

        ok, pid, candidates, reason = resolve_catalog_item(cleaned.get("catalog_name"), products_in_cart)
        if not ok:
            return {
                "ok": False,
                "needs_clarification": True,
                "reason": reason,
                "candidates": candidates,
                "catalog_id": None,
                "cleaned_args": cleaned,
            }

        if cleaned.get("quantity") is None and cleaned.get("delta") is None:
            return {
                "ok": False,
                "needs_clarification": True,
                "reason": "missing_quantity",
                "candidates": [],
                "catalog_id": pid,
                "cleaned_args": cleaned,
            }

        cleaned["catalog_id"] = pid
        print("[DEBUG] update_cart_quantity -> cleaned:", cleaned)
        return {
            "ok": True,
            "needs_clarification": False,
            "reason": None,
            "candidates": [],
            "catalog_id": pid,
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