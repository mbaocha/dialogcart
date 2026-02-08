"""
Execution Mode Configuration

Determines whether core executes real side effects (production) or
deterministic test responses (test).
"""

import os
from typing import Literal

EXECUTION_MODE_PRODUCTION = "production"
EXECUTION_MODE_TEST = "test"

ExecutionMode = Literal["production", "test"]


def get_execution_mode() -> ExecutionMode:
    """
    Get the current execution mode.
    
    Returns:
        "production" for real API execution, "test" for deterministic test execution.
        Defaults to "production" if CORE_EXECUTION_MODE is not set.
    """
    mode = os.getenv("CORE_EXECUTION_MODE", EXECUTION_MODE_PRODUCTION)
    if mode not in (EXECUTION_MODE_PRODUCTION, EXECUTION_MODE_TEST):
        # Default to production for invalid values
        return EXECUTION_MODE_PRODUCTION
    return mode  # type: ignore

