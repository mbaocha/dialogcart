"""
Capability Adapter Bootstrap

Registers default capability adapters for production use.
This module is called once at application startup to ensure adapters are available.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Import adapters and clients
try:
    from .adapters.payment import PaymentAdapter
    from .clients.payment import HttpPaymentClient
    from .registry import register_adapter
except ImportError as e:
    logger.warning(f"Failed to import capability adapters: {e}. Capabilities will not be available.")
    PaymentAdapter = None
    HttpPaymentClient = None
    register_adapter = None


def register_default_adapters(*, organization_id: Optional[int] = None) -> None:
    """
    Register default capability adapters for production use.
    
    This function should be called once at application startup to ensure
    adapters are available when core emits AWAITING_CAPABILITY status.
    
    Currently registers:
    - PaymentAdapter with HttpPaymentClient
    
    Args:
        organization_id: Optional organization ID for payment client initialization.
                        If None, HttpPaymentClient will use default behavior.
    
    Example:
        # At application startup
        from capabilities.bootstrap import register_default_adapters
        register_default_adapters(organization_id=1)
    """
    if PaymentAdapter is None or HttpPaymentClient is None or register_adapter is None:
        logger.warning(
            "Cannot register default adapters: required modules not available. "
            "Capabilities will not be available."
        )
        return
    
    try:
        # Register payment adapter with HTTP client
        payment_client = HttpPaymentClient(organization_id=organization_id)
        payment_adapter = PaymentAdapter(payment_client=payment_client)
        register_adapter(payment_adapter)
        
        logger.info(
            f"Registered default capability adapters (payment) "
            f"organization_id={organization_id}"
        )
    except Exception as e:
        logger.error(
            f"Failed to register default adapters: {e}. "
            "Capabilities will not be available.",
            exc_info=True
        )

