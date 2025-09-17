"""
Catalog package for handling catalog-related operations.
"""

from .service import CatalogService
from .repo import CatalogRepo
from .presenter import (
    categories_bulleted,
    DEFAULT_CATEGORY_EMOJI,
    _fmt_example,
    _fmt_money,
    _fmt_package_size
)
from .tools import (
    get_catalog_item,
    search_catalog,
    list_catalog_by_categories_formatted
)

__all__ = [
    'CatalogService',
    'CatalogRepo',
    'categories_bulleted',
    'DEFAULT_CATEGORY_EMOJI',
    '_fmt_example',
    '_fmt_money',
    '_fmt_package_size',
    'get_catalog_item',
    'search_catalog',
    'list_catalog_by_categories_formatted'
]
