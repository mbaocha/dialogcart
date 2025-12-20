"""
Redis Memory Store Implementation

Concrete implementation of MemoryStore using Redis.
"""

import json
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from .store import MemoryStore
from ..config import config


class RedisMemoryStore(MemoryStore):
    """
    Redis-backed memory store.
    
    Key format: luma:{domain}:user:{user_id}
    Value: JSON-serialized memory state
    TTL: 30-60 minutes (configurable)
    """
    
    def __init__(self, redis_client=None):
        """
        Initialize Redis memory store.
        
        Args:
            redis_client: Optional Redis client instance.
                         If None, will create one using config.
        """
        self._client = redis_client
        self._redis_available = False
        
        if self._client is None:
            try:
                import redis
                redis_host = getattr(config, 'REDIS_HOST', 'localhost')
                redis_port = int(getattr(config, 'REDIS_PORT', 6379))
                redis_db = int(getattr(config, 'REDIS_DB', 0))
                redis_password = getattr(config, 'REDIS_PASSWORD', None)
                
                self._client = redis.Redis(
                    host=redis_host,
                    port=redis_port,
                    db=redis_db,
                    password=redis_password,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2
                )
                # Test connection
                self._client.ping()
                self._redis_available = True
            except ImportError:
                raise ImportError(
                    "redis package not installed. "
                    "Install with: pip install redis"
                )
            except Exception as e:
                # Redis not available - fail gracefully
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Redis not available: {e}. Memory will not persist.")
                self._redis_available = False
        else:
            # Test provided client
            try:
                self._client.ping()
                self._redis_available = True
            except Exception:
                self._redis_available = False
    
    def _make_key(self, user_id: str, domain: str) -> str:
        """Generate Redis key for user memory."""
        return f"luma:{domain}:user:{user_id}"
    
    def get(self, user_id: str, domain: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve memory state for a user.
        
        Args:
            user_id: User identifier
            domain: Domain (e.g., "service", "reservation")
            
        Returns:
            Memory state dict or None if not found
        """
        if not self._redis_available:
            return None
        
        try:
            key = self._make_key(user_id, domain)
            value = self._client.get(key)
            if value is None:
                return None
            return json.loads(value)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to get memory for {user_id}: {e}")
            return None
    
    def _sanitize_for_storage(self, obj: Any) -> Any:
        """
        Recursively sanitize objects for JSON storage.
        
        Ensures only primitives (str, int, float, bool, None) and
        collections (dict, list) are stored. Converts datetime objects
        to ISO format strings. No datetime conversion on strings.
        
        Args:
            obj: Object to sanitize
            
        Returns:
            Sanitized object safe for JSON serialization
        """
        if obj is None:
            return None
        elif isinstance(obj, (str, int, float, bool)):
            return obj
        elif isinstance(obj, datetime):
            # Convert datetime to ISO string - this is the ONLY place datetime conversion happens
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {k: self._sanitize_for_storage(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._sanitize_for_storage(item) for item in obj]
        else:
            # For any other type, convert to string (fallback)
            return str(obj)
    
    def set(self, user_id: str, domain: str, state: Dict[str, Any], ttl: int = 3600) -> None:
        """
        Store memory state for a user.
        
        Args:
            user_id: User identifier
            domain: Domain (e.g., "service", "reservation")
            state: Memory state dict to store
            ttl: Time-to-live in seconds (default: 3600 = 1 hour)
        """
        if not self._redis_available:
            return
        
        try:
            # Sanitize state to ensure only JSON-serializable primitives are stored
            # This prevents datetime conversion on strings and ensures safe storage
            sanitized_state = self._sanitize_for_storage(state)
            
            key = self._make_key(user_id, domain)
            value = json.dumps(sanitized_state, default=str)
            self._client.setex(key, ttl, value)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            # Log loudly - persistence failures should not be silent
            # Use ERROR level with full traceback to ensure visibility
            logger.error(
                f"CRITICAL: Failed to persist memory for {user_id} in {domain}: {e}",
                extra={
                    'user_id': user_id,
                    'domain': domain,
                    'error_type': type(e).__name__
                },
                exc_info=True
            )
            # Do not re-raise - allow graceful degradation if Redis is unavailable
            # The error is logged loudly and will be visible in logs
    
    def clear(self, user_id: str, domain: str) -> None:
        """
        Clear memory state for a user.
        
        Args:
            user_id: User identifier
            domain: Domain (e.g., "service", "reservation")
        """
        if not self._redis_available:
            return
        
        try:
            key = self._make_key(user_id, domain)
            self._client.delete(key)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to clear memory for {user_id}: {e}")

