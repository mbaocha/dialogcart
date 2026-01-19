"""
Orchestration Layer - Error Classes

Custom exceptions for orchestration layer operations.

These exceptions are specific to orchestration concerns:
- ContractViolation: Luma API response contract validation
- UpstreamError: External API failures (Luma, Booking, Customer, etc.)
- UnsupportedIntentError: Unsupported intent handling
"""


class ContractViolation(Exception):
    """Raised when Luma response violates contract."""
    pass


class UpstreamError(Exception):
    """Raised when upstream service (Luma or business API) fails."""
    pass


class UnsupportedIntentError(Exception):
    """Raised when intent is not supported."""
    pass

