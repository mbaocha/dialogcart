# Luma Pipeline Performance Analysis

## Executive Summary

Performance profiling of the Luma pipeline identified several key bottlenecks and optimization opportunities. The analysis was conducted using cProfile on 20 diverse scenarios with detailed function-level profiling.

**Key Findings:**
- **Extraction stage dominates**: 91.8% of total execution time (76.03ms average)
- **Vocabulary loading inefficiency**: `_load_vocabularies()` called 109 times despite caching
- **spaCy processing overhead**: Neural network inference takes significant time
- **JSON file I/O**: Repeated file reads even with caching mechanisms

## Performance Metrics

### Overall Statistics
- **Average total time**: 82.84 ms per request
- **Min time**: 8.72 ms
- **Max time**: 1304.67 ms (outlier - likely first request with cold cache)

### Stage Breakdown (Average)
| Stage | Time (ms) | Percentage |
|-------|-----------|------------|
| Extraction | 76.03 | 91.8% |
| Semantic | 5.21 | 6.3% |
| Intent | 0.87 | 1.1% |
| Structure | 0.11 | 0.1% |
| Decision | 0.05 | 0.1% |
| Grouping | 0.03 | 0.0% |

## Critical Bottlenecks

### 1. Extraction Stage (76.03ms, 91.8%)

**Root Causes:**
- **spaCy NLP processing**: Neural network inference dominates
  - `spacy.language.__call__`: ~10-15ms per request
  - `thinc.model.predict`: Multiple calls totaling ~8-12ms
  - `thinc.layers.forward`: Deep neural network layers
- **Entity pattern matching**: Multiple pattern checks per request
- **Fuzzy matching overhead**: Tenant alias fuzzy matching adds latency

**Impact**: This is the primary bottleneck affecting all requests.

### 2. Vocabulary Loading Inefficiency (129.62ms cumulative, 109 calls)

**Problem**: `_load_vocabularies()` is being called 109 times across 20 scenarios, despite having a cache mechanism.

**Analysis**:
- Average: 5.45 calls per scenario
- Each call: ~1.19ms (even with cache check)
- `load_global_vocabularies()` called 107 times
- JSON file I/O happening repeatedly

**Root Cause**: The cache check `if not _VOCAB_CACHE:` may be failing, or the function is being called from multiple code paths that don't share the cache properly.

**Location**: `src/luma/resolution/semantic_resolver.py:54`

### 3. Semantic Resolution (5.21ms average, 148.03ms cumulative)

**Components**:
- `_check_ambiguity`: 83.59ms cumulative (20 calls)
- `_load_vocabularies`: 129.62ms cumulative (109 calls) - **major issue**
- `_resolve_date_semantics`: Multiple calls
- JSON loading: 8-9 calls per scenario in some cases

### 4. Frequently Called Functions

| Function | Calls | Avg Time/Call | Total Impact |
|----------|-------|---------------|------------|
| `forward` (thinc layers) | 886 | 0.10ms | 90.8ms |
| `_load_vocabularies` | 109 | 1.19ms | 129.6ms |
| `load_global_vocabularies` | 107 | 0.16ms | 17.5ms |
| `_matches_signals` | 120 | 0.02ms | 2.4ms |
| `search` (regex) | 228 | 0.001ms | 0.3ms |

## Optimization Recommendations

### Priority 1: Fix Vocabulary Loading Cache

**Issue**: `_load_vocabularies()` is called 109 times despite caching.

**Solution**:
1. **Verify cache is working**: Add logging to confirm cache hits/misses
2. **Consolidate loading points**: Ensure all code paths use the same cache
3. **Pre-load at startup**: Load vocabularies during pipeline initialization
4. **Thread-safety**: If using multiple threads, ensure cache is thread-safe

**Expected Impact**: Reduce 109 calls to 1-2 calls, saving ~100-120ms per 20 requests.

**Code Location**: `src/luma/resolution/semantic_resolver.py:54-59`

### Priority 2: Optimize spaCy Processing

**Current**: ~10-15ms per request for spaCy NLP processing.

**Solutions**:
1. **Model optimization**:
   - Use smaller/faster spaCy model if accuracy allows
   - Consider disabling unused pipeline components
   - Use `nlp.disable_pipes()` for components not needed

2. **Batch processing**: If processing multiple requests, batch them

3. **Caching spaCy results**: Cache doc objects for identical inputs (with TTL)

4. **Alternative NLP**: Consider faster alternatives for simple entity extraction

**Expected Impact**: Reduce extraction time by 30-50% (23-38ms savings).

**Code Location**: `src/luma/extraction/entity_processing.py:33`

### Priority 3: Reduce JSON File I/O

**Issue**: JSON files are being read multiple times per request.

**Solutions**:
1. **Consolidate JSON loading**: Load all required JSON files once at startup
2. **Memory caching**: Cache parsed JSON objects in memory
3. **Lazy loading with better caching**: Ensure cache checks happen before file I/O

**Expected Impact**: Reduce file I/O overhead by 80-90%.

**Code Location**: 
- `src/luma/extraction/entity_loading.py:1301` (load_global_vocabularies)
- `src/luma/resolution/semantic_resolver.py:40` (_get_global_config_path)

### Priority 4: Optimize Fuzzy Matching

**Issue**: Fuzzy matching adds overhead, especially for tenant aliases.

**Solutions**:
1. **Early exit**: Skip fuzzy matching if exact match found
2. **Limit search space**: Only check relevant aliases, not all
3. **Caching fuzzy results**: Cache fuzzy match results for common inputs
4. **Optimize threshold**: Use higher threshold to reduce candidates

**Expected Impact**: Reduce fuzzy matching overhead by 20-30%.

**Code Location**: `src/luma/extraction/matcher.py:182` (_apply_fuzzy_matching_post_process)

### Priority 5: Optimize Semantic Resolution

**Issue**: Multiple function calls and repeated computations.

**Solutions**:
1. **Memoization**: Cache results of expensive computations
2. **Reduce function calls**: Consolidate repeated logic
3. **Early returns**: Exit early when possible

**Expected Impact**: Reduce semantic resolution time by 10-20%.

## Implementation Plan

### Phase 1: Quick Wins (1-2 days)
1. Fix vocabulary loading cache (Priority 1)
2. Add startup pre-loading for vocabularies
3. Add logging to verify cache effectiveness

**Expected Improvement**: 20-30% overall performance gain

### Phase 2: Medium-term (3-5 days)
1. Optimize spaCy processing (Priority 2)
2. Consolidate JSON loading (Priority 3)
3. Add performance monitoring

**Expected Improvement**: Additional 30-40% performance gain

### Phase 3: Long-term (1-2 weeks)
1. Optimize fuzzy matching (Priority 4)
2. Refactor semantic resolution (Priority 5)
3. Add comprehensive caching layer
4. Consider architectural changes (e.g., async processing)

**Expected Improvement**: Additional 20-30% performance gain

## Monitoring Recommendations

1. **Add performance metrics**:
   - Track stage timings in production
   - Monitor cache hit rates
   - Track vocabulary loading frequency

2. **Set up alerts**:
   - Alert if extraction > 200ms
   - Alert if vocabulary loading > 10 calls per request
   - Alert if total time > 500ms (p95)

3. **Regular profiling**:
   - Run profiling script weekly
   - Track performance trends
   - Identify regressions early

## Code Quality Improvements

1. **Cache validation**: Add unit tests to verify caching works correctly
2. **Performance tests**: Add performance regression tests
3. **Documentation**: Document caching strategies and performance characteristics

## Conclusion

The Luma pipeline is functional but has significant optimization opportunities. The primary bottleneck is the extraction stage (91.8% of time), with vocabulary loading inefficiency being a critical issue. Implementing the Priority 1 and 2 recommendations should yield 50-70% performance improvement, bringing average response time from 82.84ms to ~25-40ms.

The most critical issue is the vocabulary loading cache not working effectively (109 calls vs expected 1-2). This should be addressed immediately as it's a clear bug/inefficiency that's easy to fix.

