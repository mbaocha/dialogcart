"""
Core Error Classes

Custom exceptions for dialogcart-core.
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

