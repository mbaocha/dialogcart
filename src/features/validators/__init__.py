# Central validators module for tool argument validation

from .base import BaseValidator
from .common import coerce_positive_int, ensure_catalog_items, resolve_catalog_item
from ..cart.validators import CartValidator
# Import moved to avoid circular import
# from ..catalog.validators import CatalogValidator

# Central registry mapping tool names to their validator functions (catalog-only)
def _get_catalog_validator():
    from ..catalog.validators import CatalogValidator
    return CatalogValidator()

VALIDATOR_REGISTRY = {
    "add_item_to_cart": CartValidator().validate_add_item_to_cart,
    "remove_item_from_cart": CartValidator().validate_remove_item_from_cart,
    "update_cart_quantity": CartValidator().validate_update_cart_quantity,
    "get_catalog_item": lambda args: _get_catalog_validator().validate_check_catalog_item(args),
    "search_catalog": lambda args: _get_catalog_validator().validate_check_catalog_item(args),
}

__all__ = [
    "BaseValidator",
    "CartValidator", 
    "VALIDATOR_REGISTRY",
    "coerce_positive_int",
    "resolve_catalog_item",
    "ensure_catalog_items",
] 