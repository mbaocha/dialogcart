"""
Validation layer for canonical invariant checking.

This module provides invariant validation that computes booking status
and issues based purely on intent metadata and semantic slots, without
applying runtime logic or temporal anchoring.
"""
from .invariants import compute_invariants, ValidationResult

__all__ = ["compute_invariants", "ValidationResult"]

