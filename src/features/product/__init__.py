"""
Product package for handling product-related operations.
"""

from .service import ProductService
from .repo import ProductRepo
from .presenter import (
    categories_bulleted,
    DEFAULT_CATEGORY_EMOJI,
    _fmt_example,
    _fmt_money,
    _fmt_package_size
)
from .tools import (
    get_product,
    search_products,
    list_products_by_categories_formatted
)

__all__ = [
    'ProductService',
    'ProductRepo',
    'categories_bulleted',
    'DEFAULT_CATEGORY_EMOJI',
    '_fmt_example',
    '_fmt_money',
    '_fmt_package_size',
    'get_product',
    'search_products',
    'list_products_by_categories_formatted'
] 