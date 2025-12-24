"""
Stage-level timing utility for Luma pipeline.

Provides a context manager to measure stage execution time and optionally
warn when soft performance budgets are exceeded.
"""

import time
from typing import Dict, Any, Optional
from contextlib import contextmanager
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
        self.budget_ms = budget_ms or STAGE_BUDGETS_MS.get(stage_name)
        self.start_time: Optional[float] = None
        self.duration_ms: Optional[float] = None
    
    def __enter__(self):
        """Start timing."""
        # Initialize timings dict if needed
        if "timings" not in self.trace:
            self.trace["timings"] = {}
        
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop timing and record duration."""
        if self.start_time is None:
            return  # Context manager not properly entered
        
        end_time = time.perf_counter()
        self.duration_ms = (end_time - self.start_time) * 1000.0
        
        # Store duration in trace
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

