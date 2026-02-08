"""
Mock Discovery Endpoints

Placeholder for discovery/catalog endpoint mocks.
Currently not required for CREATE_APPOINTMENT flow.
"""

from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


def mock_discovery_endpoints(
    endpoint: str,
    **kwargs
) -> Dict[str, Any]:
    """
    Mock discovery/catalog endpoints.
    
    Placeholder implementation - not required for CREATE_APPOINTMENT flow.
    
    Args:
        endpoint: Endpoint path
        **kwargs: Additional parameters (ignored)
    
    Returns:
        Mock discovery response
    """
    logger.debug(f"[MOCK] Discovery endpoint: {endpoint}")
    
    return {
        "status": "success",
        "endpoint": endpoint
    }


