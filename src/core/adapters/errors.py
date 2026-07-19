"""
Orchestration Layer - Error Classes

Custom exceptions for Core adapter / upstream concerns:
- ContractViolation: Luma API response contract validation
- UpstreamError: External API failures (Luma, Booking, etc.)
"""


class ContractViolation(Exception):
    """Raised when Luma response violates contract."""

    pass


class UpstreamError(Exception):
    """Raised when upstream service (Luma or business API) fails."""

    pass
