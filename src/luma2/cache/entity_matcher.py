"""
Thread-safe caching for EntityMatcher instances.

Caches EntityMatcher instances by (domain, entity_file_path) to avoid
repeated disk I/O and object construction on each request.
"""
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from luma.extraction.matcher import EntityMatcher


# Thread-safe cache for EntityMatcher instances
# Key: (domain: str, entity_file_path: str) - normalized absolute path
# Value: EntityMatcher instance
_matcher_cache: Dict[Tuple[str, str], EntityMatcher] = {}
_cache_lock = threading.Lock()


def _normalize_entity_file_path(entity_file: Optional[str]) -> str:
    """
    Normalize entity_file path to absolute path for consistent caching.
    
    Args:
        entity_file: Path to entity file (may be None or relative)
    
    Returns:
        Normalized absolute path as string, or empty string if None
    """
    if not entity_file:
        return ""
    
    # Resolve to absolute path for consistent caching
    entity_path = Path(entity_file).resolve()
    return str(entity_path)


def get_entity_matcher(
    domain: str,
    entity_file: Optional[str] = None,
    lazy_load_spacy: bool = False
) -> EntityMatcher:
    """
    Get or create a cached EntityMatcher instance.
    
    This function provides thread-safe caching of EntityMatcher instances
    to avoid repeated disk I/O and object construction on each request.
    
    Args:
        domain: "service" | "reservation"
        entity_file: Path to entity JSON file (optional)
        lazy_load_spacy: Skip spaCy init (testing only - not cached)
    
    Returns:
        Cached or newly created EntityMatcher instance
    
    Note:
        - Instances are cached by (domain, normalized_entity_file_path)
        - If lazy_load_spacy=True, instances are NOT cached (for testing)
        - Thread-safe: multiple requests can call this concurrently
        - Tenant-specific data (aliases) should be passed at extraction time,
          not during EntityMatcher construction
    """
    # For testing mode, don't cache (always create new instance)
    if lazy_load_spacy:
        return EntityMatcher(
            domain=domain,
            entity_file=entity_file,
            lazy_load_spacy=True
        )
    
    # Normalize entity_file path for consistent cache key
    normalized_path = _normalize_entity_file_path(entity_file)
    cache_key = (domain, normalized_path)
    
    # Try to get from cache (fast path - no lock needed for read)
    # Note: We use double-checked locking pattern for thread safety
    if cache_key in _matcher_cache:
        matcher = _matcher_cache[cache_key]
        # Verify matcher is still valid (defensive check)
        if matcher is not None:
            return matcher
    
    # Cache miss or invalid entry - acquire lock and create
    with _cache_lock:
        # Double-check after acquiring lock (another thread may have created it)
        if cache_key in _matcher_cache:
            matcher = _matcher_cache[cache_key]
            if matcher is not None:
                return matcher
        
        # Create new EntityMatcher instance
        matcher = EntityMatcher(
            domain=domain,
            entity_file=entity_file,
            lazy_load_spacy=False
        )
        
        # Store in cache
        _matcher_cache[cache_key] = matcher
        
        return matcher


def clear_cache():
    """
    Clear the EntityMatcher cache.
    
    Useful for testing or when config files are reloaded.
    """
    global _matcher_cache
    with _cache_lock:
        _matcher_cache.clear()


def get_cache_size() -> int:
    """Get the number of cached EntityMatcher instances."""
    with _cache_lock:
        return len(_matcher_cache)

