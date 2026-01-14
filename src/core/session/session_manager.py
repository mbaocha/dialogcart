"""
Session Manager

Redis-backed session storage for conversational state.

This module provides session management functionality to support follow-ups
without changing existing intent, semantic, or decision logic.

Session schema:
{
    "intent": str,
    "slots": dict,
    "missing_slots": list[str],
    "status": "READY" | "NEEDS_CLARIFICATION"
}

Constraints:
- Uses JSON serialization only (no pickles, no model objects)
- TTL: 20 minutes (reset on save)
- Stateless session logic at API boundary only
"""

import json
import os
import sys
import time
from typing import Dict, Any, Optional

SESSION_TTL_SECONDS = 20 * 60  # 20 minutes (middle of 15-30 range)
REDIS_ENV_VAR = "REDIS_URL"
SESSION_KEY_PREFIX = "session:"

# In-memory session store (fallback when Redis is not available)
_in_memory_sessions: Dict[str, Dict[str, Any]] = {}
SESSION_TTL_SECONDS_FALLBACK = 30 * 60  # 30 minutes for in-memory fallback


def _get_redis_url():
    """
    Get Redis URL from environment variable or config file fallback.
    
    Returns:
        Redis URL string or None if not configured.
    """
    # Try environment variable first
    redis_url = os.getenv(REDIS_ENV_VAR)
    if redis_url:
        return redis_url
    
    # Fallback to config file (if exists)
    try:
        from pathlib import Path
        project_root = Path(__file__).parent.parent.parent.parent
        env_file = project_root / ".env"
        env_local_file = project_root / ".env.local"
        
        # Try .env.local first (highest priority), then .env
        for env_path in [env_local_file, env_file]:
            if env_path.exists():
                try:
                    from dotenv import dotenv_values
                    config = dotenv_values(env_path)
                    redis_url = config.get(REDIS_ENV_VAR)
                    if redis_url:
                        return redis_url
                except ImportError:
                    # python-dotenv not available, skip config fallback
                    break
                except Exception:
                    # Error reading config file, continue to next
                    continue
    except Exception:
        # Error accessing config files, fall back to None
        pass
    
    return None


def _get_redis_client():
    """
    Get Redis client from environment configuration or config file.
    
    Returns:
        Redis client instance or None if Redis is not available.
    """
    redis_url = _get_redis_url()
    if not redis_url:
        return None
    
    try:
        import redis  # type: ignore
        return redis.from_url(redis_url)
    except Exception:
        return None


def _get_session_key(user_id: str) -> str:
    """Generate Redis key for user session."""
    return f"{SESSION_KEY_PREFIX}{user_id}"


def validate_redis_connection():
    """
    Validate Redis connection at startup.
    
    Tests read/write operations to Redis. If REDIS_URL is set (env or config) but Redis is
    unavailable, exits with error code 1. If Redis is working, prints success message.
    If REDIS_URL is not set, skips validation (in-memory fallback will be used).
    
    This function is called at module import time when REDIS_URL is configured.
    """
    redis_url = _get_redis_url()
    if not redis_url:
        # Redis not configured - in-memory fallback will be used, no validation needed
        return
    
    try:
        import redis  # type: ignore
        client = redis.from_url(redis_url)
        
        # Test write
        test_key = f"{SESSION_KEY_PREFIX}__health_check__"
        test_value = json.dumps({"test": True, "timestamp": time.time()})
        client.setex(test_key, 10, test_value)  # 10 second TTL
        
        # Test read
        retrieved = client.get(test_key)
        if not retrieved:
            print(f"ERROR: Redis health check failed - write succeeded but read returned None", file=sys.stderr)
            sys.exit(1)
        
        retrieved_value = json.loads(retrieved)
        if retrieved_value.get("test") is not True:
            print(f"ERROR: Redis health check failed - read returned invalid data", file=sys.stderr)
            sys.exit(1)
        
        # Clean up test key
        client.delete(test_key)
        
        # Success - print to stdout and flush immediately
        print(f"✓ Redis connection validated successfully (REDIS_URL={redis_url})", flush=True)
        
    except ImportError:
        print(f"ERROR: Redis URL is configured ({REDIS_ENV_VAR}={redis_url}) but 'redis' package is not installed", file=sys.stderr, flush=True)
        print(f"Install with: pip install redis", file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Redis connection failed (REDIS_URL={redis_url})", file=sys.stderr, flush=True)
        print(f"Error: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


def get_session(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve session state for a user.
    
    Args:
        user_id: Unique identifier for the user
        
    Returns:
        Session state dictionary or None if not found/expired
    """
    redis_client = _get_redis_client()
    if redis_client:
        # Try Redis first
        try:
            key = _get_session_key(user_id)
            raw = redis_client.get(key)
            if not raw:
                return None
            return json.loads(raw)
        except Exception:
            # Fall through to in-memory fallback
            pass
    
    # In-memory fallback
    if user_id in _in_memory_sessions:
        session_data = _in_memory_sessions[user_id]
        stored_at = session_data.get("_stored_at", 0)
        if time.time() - stored_at > SESSION_TTL_SECONDS_FALLBACK:
            # Expired, remove it
            del _in_memory_sessions[user_id]
            return None
        # Return session state (without internal _stored_at field)
        session_state = {k: v for k, v in session_data.items() if not k.startswith("_")}
        return session_state
    
    return None


def save_session(user_id: str, session_state: Dict[str, Any]) -> None:
    """
    Save session state for a user.
    
    Resets TTL on each save (20 minutes for Redis, 30 minutes for in-memory).
    
    Args:
        user_id: Unique identifier for the user
        session_state: Session state dictionary with keys:
            - intent: str
            - slots: dict
            - missing_slots: list[str]
            - status: "READY" | "NEEDS_CLARIFICATION"
    """
    redis_client = _get_redis_client()
    if redis_client:
        # Try Redis first
        try:
            key = _get_session_key(user_id)
            serialized = json.dumps(session_state)
            redis_client.setex(key, SESSION_TTL_SECONDS, serialized)
            return
        except Exception:
            # Fall through to in-memory fallback
            pass
    
    # In-memory fallback
    session_data = session_state.copy()
    session_data["_stored_at"] = time.time()
    _in_memory_sessions[user_id] = session_data


def clear_session(user_id: str) -> None:
    """
    Clear session state for a user.
    
    Args:
        user_id: Unique identifier for the user
    """
    redis_client = _get_redis_client()
    if redis_client:
        # Try Redis first
        try:
            key = _get_session_key(user_id)
            redis_client.delete(key)
        except Exception:
            # Fall through to in-memory fallback
            pass
    
    # In-memory fallback
    _in_memory_sessions.pop(user_id, None)


# Validate Redis connection at startup if REDIS_URL is configured
validate_redis_connection()

