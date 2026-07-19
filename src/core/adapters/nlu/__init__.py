"""
NLU (Natural Language Understanding) Module

This module provides the interface to Luma for semantic understanding.
Luma is NOT an execution client - it is part of semantic understanding.

Responsibilities:
- Calling Luma API for intent/entity extraction
- Validating Luma response contracts
"""

from .luma_client import LumaClient
from .luma_contracts import assert_luma_contract

__all__ = [
    "LumaClient",
    "assert_luma_contract",
]
