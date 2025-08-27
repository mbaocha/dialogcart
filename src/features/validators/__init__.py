# Central validators module for tool argument validation

from .base import BaseValidator
from .common import coerce_positive_int, resolve_product, ensure_products
from ..cart.validators import CartValidator
from ..product.validators import ProductValidator

# Central registry mapping tool names to their validator functions
VALIDATOR_REGISTRY = {
    "add_item_to_cart": CartValidator().validate_add_item_to_cart,
    "remove_item_from_cart": CartValidator().validate_remove_item_from_cart,
    "update_cart_quantity": CartValidator().validate_update_cart_quantity,
    "get_product": ProductValidator().validate_check_product,
    "search_products": ProductValidator().validate_check_product,
}

__all__ = [
    "BaseValidator",
    "CartValidator", 
    "ProductValidator",
    "VALIDATOR_REGISTRY",
    "coerce_positive_int",
    "resolve_product",
    "ensure_products",
] 