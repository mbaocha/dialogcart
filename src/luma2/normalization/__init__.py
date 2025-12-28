"""
Normalization utilities for text processing.
"""

from .alias_compiler import (
    get_compiled_aliases,
    detect_tenant_alias_spans_compiled,
    clear_cache as clear_alias_cache,
    get_cache_size as get_alias_cache_size
)

__all__ = [
    'get_compiled_aliases',
    'detect_tenant_alias_spans_compiled',
    'clear_alias_cache',
    'get_alias_cache_size'
]

