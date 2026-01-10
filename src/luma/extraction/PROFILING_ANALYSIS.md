# Extraction Performance Profiling Analysis

## Test Case
- Input: "book me a presdential room november 1st through november 5th"
- Tenant aliases: 11 aliases
- Total time: **0.043s** (43ms) in isolated test

## Top Bottlenecks (Cumulative Time)

### 1. Regex Compilation: **0.018s (42% of total time)**
- **72 calls** to `re._compile`
- **68 calls** to `re._compiler.compile`
- **Impact**: This is the #1 bottleneck!

### 2. spaCy Processing: **0.017s (40% of total time)**
- `extract_entities_from_doc`: 0.017s
- spaCy model inference (thinc layers)

### 3. Tenant Alias Detection: **0.015s (35% of total time)**
- `detect_tenant_alias_spans`: 0.015s
- Includes regex operations within

### 4. Fuzzy Matching: **0.009s (21% of total time)**
- `_apply_fuzzy_matching_post_process`: 0.009s
- **Already optimized** with `process.extractOne`

### 5. Regex Searching: **0.007s (16% of total time)**
- **42 calls** to `re.search`
- Used for character position mapping

## Optimization Opportunities (Biggest Wins First)

### 🎯 Priority 1: Cache Compiled Regex Patterns
**Expected gain: ~0.018s (42% improvement)**

**Problem**: 72 regex patterns are being compiled on every extraction call.

**Solution**: Cache compiled regex patterns at module/class level.

**Files to modify**:
- `matcher.py` - `_apply_fuzzy_matching_post_process` (lines 252-256)
- `matcher.py` - `detect_tenant_alias_spans` and related functions
- Any other functions using `re.compile` or `re.search` with repeated patterns

**Implementation**:
```python
# Module-level cache
_regex_cache = {}

def _get_compiled_regex(pattern: str, flags: int = 0) -> re.Pattern:
    """Get compiled regex from cache or compile and cache."""
    cache_key = (pattern, flags)
    if cache_key not in _regex_cache:
        _regex_cache[cache_key] = re.compile(pattern, flags)
    return _regex_cache[cache_key]
```

### 🎯 Priority 2: Reduce Regex Searches for Character Positions
**Expected gain: ~0.007s (16% improvement)**

**Problem**: 42 `re.search` calls to find character positions from token positions.

**Solution**: Calculate character positions directly from token positions instead of regex.

**Location**: `_apply_fuzzy_matching_post_process` (lines 250-259)

**Current approach**: Uses regex to find phrase in text
**Better approach**: Use token positions to calculate character positions directly

### 🎯 Priority 3: Optimize detect_tenant_alias_spans
**Expected gain: ~0.005-0.010s (12-23% improvement)**

**Problem**: Takes 0.015s, likely due to regex operations within.

**Solution**: 
- Ensure compiled alias structure is being used (already implemented)
- Cache any regex patterns used in alias detection
- Consider early exit strategies

### 🎯 Priority 4: spaCy Optimization (Lower Priority)
**Expected gain: Limited (requires model changes)**

**Problem**: spaCy processing takes 0.017s (40% of time).

**Solutions** (more complex):
- Use lighter spaCy model
- Cache spaCy docs for repeated text (if applicable)
- Parallelize independent operations

## Recommended Implementation Order

1. **Cache compiled regex patterns** - Biggest win, easy to implement
2. **Optimize character position calculation** - Good win, medium effort
3. **Review detect_tenant_alias_spans** - Medium win, review existing optimizations
4. **spaCy optimizations** - Lower priority, requires more investigation

## Expected Combined Impact

If we implement Priority 1 + 2:
- Regex compilation: 0.018s → ~0.001s (cached after first call)
- Regex searches: 0.007s → ~0.002s (token-based calculation)
- **Total improvement: ~0.022s (51% of current time)**
- **New extraction time: ~0.021s (from 0.043s)**

Note: Production shows 1258ms vs 43ms in isolated test, suggesting different conditions. 
But relative percentages should still apply, so these optimizations should help proportionally.



