# Exceptions Package Analysis

## Current Location
`core/errors/exceptions.py`

## Usage Analysis

### ContractViolation
**Used in:**
- `orchestration/contracts/luma_contracts.py` - Raises it
- `orchestration/orchestrator.py` - Catches it
- `orchestration/api/message.py` - Catches it
- Tests

**Purpose**: Luma API response contract validation

### UpstreamError
**Used in:**
- `orchestration/clients/base_client.py` - Raises it
- `orchestration/clients/luma_client.py` - Raises it
- `orchestration/clients/*` - All client files raise it
- `orchestration/cache/org_domain_cache.py` - Raises it
- `orchestration/orchestrator.py` - Catches it
- `orchestration/api/message.py` - Catches it
- Tests

**Purpose**: External API failures (Luma, Booking, Customer, etc.)

### UnsupportedIntentError
**Used in:**
- `orchestration/orchestrator.py` - Raises and catches it

**Purpose**: Unsupported intent handling

## Classification

### Current Classification
**Location**: `core/errors/` (top-level shared utility)

**Analysis**: 
- ❌ **INCORRECT** - These exceptions are NOT shared across layers
- ❌ **INCORRECT** - They are NOT used by routing or rendering layers
- ✅ **FACT**: They are used EXCLUSIVELY by orchestration layer

### Correct Classification

**Responsibility**: Orchestration Layer  
**Reason**: All three exceptions are orchestration-specific concerns:
1. `ContractViolation` - Contract validation (orchestration/contracts)
2. `UpstreamError` - External API calls (orchestration/clients, orchestration/cache)
3. `UnsupportedIntentError` - Intent handling (orchestration/orchestrator)

## Recommendation

### ✅ IMPLEMENTED: Moved to Orchestration Package
**New Location**: `core/orchestration/errors.py`

**Status**: ✅ **COMPLETED**

**Changes Made**:
1. Created `core/orchestration/errors.py` with all three exception classes
2. Updated all imports across codebase (8 files):
   - `orchestration/orchestrator.py`
   - `orchestration/contracts/luma_contracts.py`
   - `orchestration/clients/base_client.py`
   - `orchestration/clients/luma_client.py`
   - `orchestration/cache/org_domain_cache.py`
   - `orchestration/api/message.py`
   - `tests/test_orchestrator_flow.py`
   - `tests/contracts/test_luma_contracts.py`
3. Deleted `core/errors/exceptions.py`
4. Verified all imports work correctly

**Result**: Exceptions now correctly belong to orchestration layer.

### Option 2: Keep as Shared Utility (Not Recommended)
**Location**: `core/errors/exceptions.py` (current)

**Pros**:
- No import changes needed
- Could be used by other layers in the future

**Cons**:
- ❌ Misleading - suggests cross-layer usage when it's orchestration-only
- ❌ Violates single responsibility - errors package should be for truly shared exceptions
- ❌ Makes it unclear which layer owns these exceptions

## Conclusion

**Recommendation**: Move to `core/orchestration/errors.py`

**Justification**:
1. All exceptions are orchestration-layer specific
2. No current usage by routing or rendering layers
3. Better aligns with three-layer responsibility model
4. Clearer ownership and maintainability

**If keeping as shared utility**:
- Should rename to `core/common/` or `core/shared/` to be clearer
- Should document that these are orchestration-specific but available for future use
- Less ideal than moving to orchestration package

