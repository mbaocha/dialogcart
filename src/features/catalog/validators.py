# Catalog-specific tool argument validators

from typing import Any, Dict
from ..validators.base import BaseValidator
from ..validators.common import resolve_catalog_item


class CatalogValidator(BaseValidator):
    # Validator for catalog-related tool operations
    
    def validate_check_catalog_item(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # Validates a catalog item reference (used for search/get checks)
        cleaned = dict(args)
        catalog_items = args.get('catalog_items', {})

        catalog_name = cleaned.get("catalog_name")
        if catalog_name:
            cleaned["catalog_name"] = catalog_name
        if catalog_items is not None:
            cleaned["catalog_items"] = catalog_items
        print(f"[DEBUG] validate_check_catalog_item -> catalog_name: '{catalog_name}', catalog_items keys: {list(catalog_items.keys())[:5]}")

        ok, cid, candidates, reason = resolve_catalog_item(catalog_name, catalog_items)
        if ok and cid:
            cleaned["catalog_id"] = cid
        print("[DEBUG] check_catalog_item -> cleaned:", cleaned)
        return {
            "ok": ok and reason is None,
            "needs_clarification": not ok,
            "reason": reason,
            "candidates": candidates,
            "catalog_id": cid,
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
