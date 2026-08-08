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


class AvailabilityRejectedError(UpstreamError):
    """Availability request was valid but rejected as a business outcome."""

    def __init__(self, *, reason: str = "availability_rejected") -> None:
        super().__init__("Availability is unavailable for the requested criteria")
        self.status_code = 422
        self.reason = reason
