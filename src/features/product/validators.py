# Product-specific tool argument validators

from typing import Any, Dict
from ..validators.base import BaseValidator
from ..validators.common import resolve_product


class ProductValidator(BaseValidator):
    # Validator for product-related tool operations
    
    def validate_check_product(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # Validates a product reference (used for search/get checks)
        cleaned = dict(args)
        products = args.get('products', {})
        ok, pid, candidates, reason = resolve_product(cleaned.get("product_name"), products)
        if ok and pid:
            cleaned["product_id"] = pid
        print("[DEBUG] check_product -> cleaned:", cleaned)
        return {
            "ok": ok and reason is None,
            "needs_clarification": not ok,
            "reason": reason,
            "candidates": candidates,
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