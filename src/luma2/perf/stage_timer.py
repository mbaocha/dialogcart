"""
Stage-level timing utility for Luma pipeline.

Provides a context manager to measure stage execution time and optionally
warn when soft performance budgets are exceeded.

Also supports stage snapshot capture for tracing (see StageTimerWithSnapshot).
"""

import time
from typing import Dict, Any, Optional, Callable
import logging

logger = logging.getLogger(__name__)

# Soft performance budgets (ms) - warn-only, never raise
STAGE_BUDGETS_MS: Dict[str, int] = {
    "extraction": 200,
    "intent": 150,
    "structure": 100,
    "grouping": 100,
    "semantic": 300,
    "decision": 50,
    "binder": 150,
    "memory": 100,  # Optional memory operations
}


class StageTimer:
    """
    Context manager for timing pipeline stage execution.
    
    Usage:
        with StageTimer(trace, "extraction"):
            # stage code here
            pass
    
    Stores duration (ms) into trace["timings"][stage_name] = duration_ms
    Emits warning if stage exceeds soft budget (never raises).
    
    Args:
        trace: Dictionary to store timings (will create trace["timings"] if needed)
        stage_name: Name of the stage being timed
        request_id: Optional request ID for logging
        budget_ms: Optional budget override (defaults to STAGE_BUDGETS_MS[stage_name])
    """
    
    def __init__(
        self,
        trace: Dict[str, Any],
        stage_name: str,
        request_id: Optional[str] = None,
        budget_ms: Optional[int] = None
    ):
        self.trace = trace
        self.stage_name = stage_name
        self.request_id = request_id
        
        # Get budget from override or default
        base_budget = budget_ms or STAGE_BUDGETS_MS.get(stage_name)
        
        # In non-production mode, disable budget warnings for extraction and semantic stages
        # (these stages often take longer in development due to model loading, debug logging, etc.)
        if base_budget is not None and stage_name in ("extraction", "semantic"):
            # Lazy import to avoid circular dependencies
            try:
                from ..config import config
                if config.API_DEBUG:
                    self.budget_ms = None  # Disable warnings in non-production
                else:
                    self.budget_ms = base_budget
            except (ImportError, AttributeError):
                # Fallback: use default budget if config not available
                self.budget_ms = base_budget
        else:
            self.budget_ms = base_budget
        
        self.start_time: Optional[float] = None
        self.duration_ms: Optional[float] = None
    
    def __enter__(self):
        """Start timing."""
        # Zero side effects if trace is missing or invalid
        if not isinstance(self.trace, dict):
            return self
        
        # Initialize timings dict if needed
        if "timings" not in self.trace:
            self.trace["timings"] = {}
        
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop timing and record duration."""
        # Zero side effects if trace is missing or invalid
        if not isinstance(self.trace, dict) or self.start_time is None:
            return False  # Never suppress exceptions
        
        end_time = time.perf_counter()
        self.duration_ms = (end_time - self.start_time) * 1000.0
        
        # Store duration in trace (safe - we checked trace is dict)
        if "timings" not in self.trace:
            self.trace["timings"] = {}
        self.trace["timings"][self.stage_name] = round(self.duration_ms, 2)
        
        # Warn if budget exceeded (soft check - never raise)
        if self.budget_ms is not None and self.duration_ms > self.budget_ms:
            logger.warning(
                f"Stage '{self.stage_name}' exceeded performance budget",
                extra={
                    'request_id': self.request_id,
                    'stage': self.stage_name,
                    'duration_ms': round(self.duration_ms, 2),
                    'budget_ms': self.budget_ms
                }
            )
        
        # Never suppress exceptions
        return False

