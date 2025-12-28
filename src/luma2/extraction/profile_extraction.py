#!/usr/bin/env python3
"""
Profile extraction performance to identify bottlenecks.
"""

import sys
import cProfile
import pstats
import time
from pathlib import Path
from io import StringIO

# Add src directory to path for imports
script_dir = Path(__file__).parent.resolve()  # extraction/
src_dir = script_dir.parent.parent  # src/
src_path = str(src_dir)
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from luma.extraction.matcher import EntityMatcher


def profile_extraction():
    """Profile the extraction process with a realistic test case."""
    
    # Initialize matcher
    global_json_path = Path(__file__).parent.parent / "store" / "normalization" / "global.v2.json"
    matcher = EntityMatcher(domain="reservation", entity_file=str(global_json_path), lazy_load_spacy=False)
    
    # Test case that's causing performance issues
    text = "book me a presdential room november 1st through november 5th"
    tenant_aliases = {
        "standard": "room",
        "room": "room",
        "delux": "room",
        "premum suite": "room",
        "hair cut": "haircut",
        "haircut": "haircut",
        "beard": "beard grooming",
        "beerd": "beard grooming",
        "suite": "room",
        "massage": "massage",
        "presidential room": "room",
    }
    
    print("=" * 70)
    print("Profiling Extraction Performance")
    print("=" * 70)
    print(f"Input text: {text}")
    print(f"Tenant aliases: {len(tenant_aliases)} aliases")
    print()
    
    # Time the extraction (wall clock time)
    start_time = time.perf_counter()
    
    # Create profiler
    profiler = cProfile.Profile()
    
    # Profile the extraction
    profiler.enable()
    result = matcher.extract_with_parameterization(
        text=text,
        tenant_aliases=tenant_aliases
    )
    profiler.disable()
    
    end_time = time.perf_counter()
    wall_time_ms = (end_time - start_time) * 1000.0
    
    print(f"\nWall clock time: {wall_time_ms:.2f}ms")
    print(f"Performance budget: 200ms")
    print(f"Over budget by: {wall_time_ms - 200:.2f}ms ({wall_time_ms / 200:.1f}x)")
    print()
    
    # Create stats
    stats = pstats.Stats(profiler)
    
    # Sort by cumulative time
    print("=" * 70)
    print("TOP 30 FUNCTIONS BY CUMULATIVE TIME")
    print("=" * 70)
    stats.sort_stats('cumulative')
    stats.print_stats(30)
    
    print("\n" + "=" * 70)
    print("TOP 30 FUNCTIONS BY TOTAL TIME (self time)")
    print("=" * 70)
    stats.sort_stats('tottime')
    stats.print_stats(30)
    
    # Filter to extraction-related functions
    print("\n" + "=" * 70)
    print("EXTRACTION-RELATED FUNCTIONS (filtered)")
    print("=" * 70)
    
    # Print stats for extraction module
    print("\n--- Functions in extraction module ---")
    stats.print_stats('extraction')
    
    # Print stats for specific functions
    for keyword in ['fuzzy', 'alias', 'normalize', 'detect_tenant', 'regex', 'compile', '_get_compiled']:
        print(f"\n--- Functions containing '{keyword}' ---")
        stats.print_stats(keyword)
    
    # Check regex cache usage
    print("\n" + "=" * 70)
    print("REGEX CACHE ANALYSIS")
    print("=" * 70)
    from luma.extraction.matcher import _regex_cache
    print(f"Cached regex patterns: {len(_regex_cache)}")
    
    # Analyze regex compilation calls
    import re
    regex_compile_calls = sum(1 for (filename, lineno, funcname) in stats.stats.keys() 
                              if 're' in filename.lower() and '_compile' in funcname)
    print(f"Total regex compilation calls: {regex_compile_calls}")
    print(f"Cache hit ratio: {len(_regex_cache)} unique patterns / {43} total calls = {len(_regex_cache)/43*100:.1f}% unique patterns")
    
    if _regex_cache:
        print("\nCached patterns (first 10):")
        for i, (pattern, flags) in enumerate(list(_regex_cache.keys())[:10]):
            print(f"  {i+1}. Pattern: {pattern[:60]}... (flags: {flags})")
    
    # Performance comparison: first vs second run
    print("\n" + "=" * 70)
    print("CACHE WARMUP TEST")
    print("=" * 70)
    start_time2 = time.perf_counter()
    result2 = matcher.extract_with_parameterization(
        text=text,
        tenant_aliases=tenant_aliases
    )
    end_time2 = time.perf_counter()
    warm_time_ms = (end_time2 - start_time2) * 1000.0
    print(f"Second run (cache warm): {warm_time_ms:.2f}ms")
    print(f"Speedup: {wall_time_ms / warm_time_ms:.2f}x faster")
    print(f"Cache impact: {wall_time_ms - warm_time_ms:.2f}ms saved")
    
    # Save full profile to file
    profile_file = script_dir / "extraction_profile.prof"
    profiler.dump_stats(str(profile_file))
    print(f"\nFull profile saved to: {profile_file}")
    print("View with: python -m pstats extraction_profile.prof")
    
    return stats


if __name__ == "__main__":
    stats = profile_extraction()
    
    # Print summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total function calls: {stats.total_calls}")
    print(f"Total time: {stats.total_tt:.3f}s")
    print(f"Primitive calls: {stats.prim_calls}")

