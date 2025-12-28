"""
Caching utilities for expensive-to-construct objects.

This module provides thread-safe caching for:
- EntityMatcher instances (keyed by domain + entity_file_path)
- Normalization config loading
"""
from .entity_matcher import get_entity_matcher

__all__ = ['get_entity_matcher']

