"""
Normalization utilities for text processing.
"""

from .alias_compiler import clear_cache as clear_alias_cache
from .alias_compiler import detect_tenant_alias_spans_compiled
from .alias_compiler import get_cache_size as get_alias_cache_size
from .alias_compiler import get_compiled_aliases

__all__ = [
    "get_compiled_aliases",
    "detect_tenant_alias_spans_compiled",
    "clear_alias_cache",
    "get_alias_cache_size",
]
