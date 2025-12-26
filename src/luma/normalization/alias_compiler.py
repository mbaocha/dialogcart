"""
Compiled alias normalization for tenant aliases.

Precompiles tenant aliases into efficient structures (pre-sorted, pre-compiled regex)
to avoid per-request alias scanning and regex compilation.
"""
import re
import threading
import hashlib
import json
from typing import Dict, List, Tuple, Any, Optional


# Thread-safe cache for compiled aliases
# Key: hash of alias dict (order-independent)
# Value: CompiledAliasStructure
_compiled_alias_cache: Dict[str, 'CompiledAliasStructure'] = {}
_cache_lock = threading.Lock()


class CompiledAliasStructure:
    """
    Precompiled alias structure for efficient matching.
    
    Contains:
    - Pre-sorted alias list (longest-first for deterministic longest-match)
    - Pre-compiled regex patterns for each alias
    - Canonical mappings
    """
    
    def __init__(self, aliases: List[Tuple[str, str, str, re.Pattern]]):
        """
        Args:
            aliases: List of (alias_key, alias_lower, canonical, compiled_pattern) tuples,
                     sorted by token length desc, then char length desc
        """
        self.aliases = aliases
    
    def detect_spans(self, normalized_text: str) -> List[Dict[str, Any]]:
        """
        Detect alias spans in normalized text.
        
        Args:
            normalized_text: Already normalized (lowercase) text
        
        Returns:
            List of span dicts with start_char, end_char, text, canonical, alias_key
        """
        # Text should already be lowercase from pre_normalization, but lowercase
        # again defensively to ensure case-insensitive matching
        text_lower = normalized_text.lower()
        spans: List[Dict[str, Any]] = []
        used_ranges: List[Tuple[int, int]] = []
        
        # Iterate through pre-sorted, pre-compiled aliases
        for alias_key, alias_lower, canonical, pattern in self.aliases:
            for match in pattern.finditer(text_lower):
                start_char, end_char = match.span()
                
                # Skip if overlaps an already-matched longer alias
                overlap = any(
                    not (end_char <= u_start or start_char >= u_end)
                    for u_start, u_end in used_ranges
                )
                if overlap:
                    continue
                
                spans.append({
                    "start_char": start_char,
                    "end_char": end_char,
                    "text": normalized_text[start_char:end_char],
                    "canonical": canonical,  # alias value (canonical_family)
                    "alias_key": alias_key,  # alias key (tenant_service_id)
                })
                used_ranges.append((start_char, end_char))
        
        return spans


def _hash_aliases(aliases: Dict[str, str]) -> str:
    """
    Generate a stable hash of aliases dict (order-independent).
    
    Args:
        aliases: Dict mapping alias -> canonical
    
    Returns:
        Hex digest of the hash
    """
    # Sort items to make hash order-independent
    sorted_items = sorted(aliases.items())
    # Create a stable JSON representation
    alias_json = json.dumps(sorted_items, sort_keys=True)
    # Generate hash
    return hashlib.sha256(alias_json.encode('utf-8')).hexdigest()


def _compile_aliases(aliases: Dict[str, str]) -> CompiledAliasStructure:
    """
    Compile aliases into efficient structure.
    
    Args:
        aliases: Dict mapping alias_key -> canonical_family (alias value)
    
    Returns:
        CompiledAliasStructure with pre-sorted, pre-compiled patterns
    """
    compiled: List[Tuple[str, str, str, re.Pattern]] = []
    
    # Sort aliases by token length desc, then char length desc for deterministic longest-first
    sorted_aliases = sorted(
        aliases.items(),
        key=lambda kv: (len(kv[0].split()), len(kv[0])),
        reverse=True,
    )
    
    for alias_key, canonical_family in sorted_aliases:
        if not isinstance(alias_key, str) or not isinstance(canonical_family, str):
            continue
        alias_lower = alias_key.lower().strip()
        if not alias_lower:
            continue
        
        # Pre-compile regex pattern
        pattern = re.compile(r"\b" + re.escape(alias_lower) + r"\b")
        compiled.append((alias_key, alias_lower, canonical_family, pattern))
    
    return CompiledAliasStructure(compiled)


def get_compiled_aliases(tenant_aliases: Optional[Dict[str, str]]) -> Optional[CompiledAliasStructure]:
    """
    Get or create a cached CompiledAliasStructure for tenant aliases.
    
    This function provides thread-safe caching of compiled alias structures
    to avoid repeated sorting and regex compilation on each request.
    
    Args:
        tenant_aliases: Dict mapping alias -> canonical, or None/empty
    
    Returns:
        CompiledAliasStructure if aliases exist, None otherwise
    
    Note:
        - Compiled structures are cached by hash of alias dict (order-independent)
        - Thread-safe: multiple requests can call this concurrently
        - Falls back gracefully if compilation fails (returns None)
    """
    if not tenant_aliases:
        return None
    
    # Generate stable hash for cache key
    alias_hash = _hash_aliases(tenant_aliases)
    
    # Try to get from cache (fast path - no lock needed for read)
    if alias_hash in _compiled_alias_cache:
        compiled = _compiled_alias_cache[alias_hash]
        if compiled is not None:
            return compiled
    
    # Cache miss - acquire lock and compile
    with _cache_lock:
        # Double-check after acquiring lock (another thread may have compiled it)
        if alias_hash in _compiled_alias_cache:
            compiled = _compiled_alias_cache[alias_hash]
            if compiled is not None:
                return compiled
        
        # Compile aliases (with fallback on error)
        try:
            compiled = _compile_aliases(tenant_aliases)
            # Store in cache
            _compiled_alias_cache[alias_hash] = compiled
            return compiled
        except Exception:
            # Fallback: return None to use slow path
            # Don't cache failures to allow retry if aliases change
            return None


def detect_tenant_alias_spans_compiled(
    normalized_text: str,
    tenant_aliases: Optional[Dict[str, str]]
) -> Optional[List[Dict[str, Any]]]:
    """
    Detect tenant alias spans using compiled alias structure.
    
    This is the optimized version that uses precompiled aliases.
    
    Args:
        normalized_text: Normalized text (already lowercase)
        tenant_aliases: Dict mapping alias -> canonical, or None/empty
    
    Returns:
        List of span dicts if successful, None if no aliases or compilation failed
        (caller should use slow path if None is returned and aliases exist)
    """
    if not tenant_aliases:
        return []
    
    compiled = get_compiled_aliases(tenant_aliases)
    if compiled is None:
        # Compilation failed (but aliases exist) - signal to use slow path
        return None
    
    return compiled.detect_spans(normalized_text)


def clear_cache():
    """
    Clear the compiled alias cache.
    
    Useful for testing or when alias configs are reloaded.
    """
    global _compiled_alias_cache
    with _cache_lock:
        _compiled_alias_cache.clear()


def get_cache_size() -> int:
    """Get the number of cached compiled alias structures."""
    with _cache_lock:
        return len(_compiled_alias_cache)

