"""
Memory Store Abstraction

Provides a clean interface for persisting and retrieving booking state.
All memory operations go through this abstraction.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class MemoryStore(ABC):
    """
    Abstract base class for memory storage.
    
    Implementations must provide:
    - get(user_id, domain) -> dict | None
    - set(user_id, domain, state, ttl) -> None
    - clear(user_id, domain) -> None
    """
    
    @abstractmethod
    def get(self, user_id: str, domain: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve memory state for a user.
        
        Args:
            user_id: User identifier
            domain: Domain (e.g., "service", "reservation")
            
        Returns:
            Memory state dict or None if not found
        """
        pass
    
    @abstractmethod
    def set(self, user_id: str, domain: str, state: Dict[str, Any], ttl: int = 3600) -> None:
        """
        Store memory state for a user.
        
        Args:
            user_id: User identifier
            domain: Domain (e.g., "service", "reservation")
            state: Memory state dict to store
            ttl: Time-to-live in seconds (default: 3600 = 1 hour)
        """
        pass
    
    @abstractmethod
    def clear(self, user_id: str, domain: str) -> None:
        """
        Clear memory state for a user.
        
        Args:
            user_id: User identifier
            domain: Domain (e.g., "service", "reservation")
        """
        pass

