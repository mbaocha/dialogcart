"""
Core Execution System

This package provides execution backends for different environments.
Execution is environment-dependent, orchestration is not.
"""

from .config import get_execution_mode, EXECUTION_MODE_PRODUCTION, EXECUTION_MODE_TEST
from .test_backend import TestExecutionBackend

__all__ = [
    "get_execution_mode",
    "EXECUTION_MODE_PRODUCTION",
    "EXECUTION_MODE_TEST",
    "TestExecutionBackend",
]

