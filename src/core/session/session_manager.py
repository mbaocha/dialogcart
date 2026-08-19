"""
Session Manager

Redis-backed session storage for conversational state.

This module provides session management functionality to support follow-ups
without changing existing intent, semantic, or decision logic.

Session schema:
{
    "intent": str,
    "slots": dict,  # Collected slots only - missing_slots are computed fresh
    "status": "READY" | "NEEDS_CLARIFICATION",
}

Note: missing_slots are NEVER persisted in session.
They are computed fresh from intent contract + collected slots.

Constraints:
- Uses JSON serialization only (no pickles, no model objects)
- TTL: ``DIALOGCART_SESSION_TTL_SECONDS`` (default 20 minutes; reset on save)
- Stateless session logic at API boundary only
- Keys are scoped by organization and user: session:{organization_id}:{user_id}
- Legacy session:{user_id} keys are intentionally ignored; active sessions reset once

Deployments may set ``DIALOGCART_SESSION_TTL_SECONDS=172800`` only after artifact
freshness invalidation is verified. Increasing it changes customer-data retention
and Redis capacity requirements, not merely cache performance.
"""

import json
import os
import sys
from typing import Any, Dict, Optional

from core.config.session_freshness import DEFAULT_SESSION_TTL_SECONDS

SESSION_TTL_SECONDS = DEFAULT_SESSION_TTL_SECONDS
REDIS_ENV_VAR = "REDIS_URL"
SESSION_KEY_PREFIX = "session:"

# In-memory session store (fallback when Redis is not available)
_in_memory_sessions: Dict[str, Dict[str, Any]] = {}
SESSION_TTL_SECONDS_FALLBACK = DEFAULT_SESSION_TTL_SECONDS


def _normalize_loaded_session(session_state: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize confirmation state and canonical Session V2 shape for runtime use."""
    try:
        from core.session.confirmation_gate import normalize_confirmation_state

        session_state = normalize_confirmation_state(session_state)
    except Exception:
        pass

    from core.session.freshness import apply_load_freshness
    from core.session.session_schema_v2 import (
        hydrate_v1_compat_shims,
        normalize_session_to_v2,
    )

    canonical = normalize_session_to_v2(session_state)
    apply_load_freshness(canonical)
    hydrated = hydrate_v1_compat_shims(canonical)
    from core.session.freshness import AVAILABILITY_REFRESH_REASON_KEY

    refresh_reason = canonical.get(AVAILABILITY_REFRESH_REASON_KEY)
    if refresh_reason is not None:
        hydrated[AVAILABILITY_REFRESH_REASON_KEY] = refresh_reason
    return hydrated


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

        project_root = Path(__file__).parent.parent.parent.parent.parent
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


def _get_session_key(organization_id: int, user_id: str) -> str:
    """Generate the tenant-scoped Redis key for a conversation session."""
    if organization_id <= 0:
        raise ValueError("organization_id must be positive")
    return f"{SESSION_KEY_PREFIX}{organization_id}:{user_id}"


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
        from core.clock import utc_now

        test_value = json.dumps({"test": True, "timestamp": utc_now().timestamp()})
        client.setex(test_key, 10, test_value)  # 10 second TTL

        # Test read
        retrieved = client.get(test_key)
        if not retrieved:
            print(
                f"ERROR: Redis health check failed - write succeeded but read returned None",
                file=sys.stderr,
            )
            sys.exit(1)

        retrieved_value = json.loads(retrieved)
        if retrieved_value.get("test") is not True:
            print(
                f"ERROR: Redis health check failed - read returned invalid data",
                file=sys.stderr,
            )
            sys.exit(1)

        # Clean up test key
        client.delete(test_key)

        # Success - print to stdout and flush immediately
        print(
            f"[OK] Redis connection validated successfully (REDIS_URL={redis_url})",
            flush=True,
        )

    except ImportError:
        print(
            f"ERROR: Redis URL is configured ({REDIS_ENV_VAR}={redis_url}) but 'redis' package is not installed",
            file=sys.stderr,
            flush=True,
        )
        print(f"Install with: pip install redis", file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception as e:
        print(
            f"ERROR: Redis connection failed (REDIS_URL={redis_url})",
            file=sys.stderr,
            flush=True,
        )
        print(f"Error: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


def get_session(organization_id: int, user_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve session state for a user.

    Args:
        user_id: Unique identifier for the user

    Returns:
        Session state dictionary or None if not found/expired
    """
    import logging

    logger = logging.getLogger(__name__)

    key = _get_session_key(organization_id, user_id)
    logger.debug("[SESSION_LOAD] user_id=%s key=%s", user_id, key)

    redis_client = _get_redis_client()
    if redis_client:
        # Try Redis first
        try:
            raw = redis_client.get(key)
            if not raw:
                logger.debug("[SESSION_LOAD] not found in Redis: user_id=%s", user_id)
                return None
            session_state = json.loads(raw)
            session_state = _normalize_loaded_session(session_state)
            logger.debug(
                "[SESSION_LOAD] found in Redis: user_id=%s intent_name=%r status=%r",
                user_id,
                session_state.get("intent_name"),
                session_state.get("status"),
            )
            return session_state
        except Exception as e:
            logger.warning(
                "[SESSION_LOAD] Redis load failed, falling back to in-memory: user_id=%s error=%s",
                user_id,
                e,
            )
            # Fall through to in-memory fallback
            pass

    # In-memory fallback
    if key in _in_memory_sessions:
        session_data = _in_memory_sessions[key]
        stored_at = session_data.get("_stored_at", 0)
        from core.clock import utc_now
        from core.config.session_freshness import load_session_freshness_settings

        if utc_now().timestamp() - stored_at > load_session_freshness_settings().session_ttl_seconds:
            del _in_memory_sessions[key]
            logger.debug("[SESSION_LOAD] expired in-memory: user_id=%s", user_id)
            return None
        session_state = {k: v for k, v in session_data.items() if not k.startswith("_")}
        session_state = _normalize_loaded_session(session_state)
        logger.debug(
            "[SESSION_LOAD] found in-memory: user_id=%s intent_name=%r status=%r",
            user_id,
            session_state.get("intent_name"),
            session_state.get("status"),
        )
        return session_state

    logger.debug("[SESSION_LOAD] not found: user_id=%s", user_id)
    return None


def save_session(
    organization_id: int, user_id: str, session_state: Dict[str, Any]
) -> None:
    """
    Save session state for a user.

    Resets the configured TTL on each save for Redis and the in-memory fallback.

    Args:
        user_id: Unique identifier for the user
        session_state: Session state dictionary with keys:
            - intent: str
            - slots: dict (collected slots only)
            - status: "READY" | "NEEDS_CLARIFICATION"

    Note: missing_slots are NOT stored in session - they are computed fresh.
    """
    import logging

    logger = logging.getLogger(__name__)

    logger.debug(
        "[PERSISTENCE_TRACE] save_session: user_id=%s slot_attempts=%s",
        user_id,
        session_state.get("slot_attempts"),
    )

    _normalize_loaded_session(session_state)

    from core.session.session_schema_v2 import (
        is_session_v2,
        prepare_session_for_persist,
    )

    session_state = prepare_session_for_persist(session_state)
    from core.session.freshness import stamp_last_activity

    stamp_last_activity(session_state)

    # Legacy guard: in-memory compat may still carry facts; pure V2 persist does not.
    if not is_session_v2(session_state):
        if "facts" not in session_state:
            logger.warning(
                f"[SESSION_SAVE] Missing 'facts' key in session_state, adding empty dict. "
                f"user_id={user_id}, session_state keys: {list(session_state.keys())}"
            )
            session_state["facts"] = {}
        elif session_state["facts"] is None:
            logger.warning(
                f"[SESSION_SAVE] session_state['facts'] is None, replacing with empty dict. user_id={user_id}"
            )
            session_state["facts"] = {}
        elif not isinstance(session_state["facts"], dict):
            logger.warning(
                f"[SESSION_SAVE] session_state['facts'] is not a dict (type: {type(session_state['facts'])}), "
                f"replacing with empty dict. user_id={user_id}"
            )
            session_state["facts"] = {}

        assert isinstance(session_state.get("facts"), dict), (
            f"CRITICAL: session_state['facts'] must be a dict before save_session. "
            f"user_id={user_id}, Got type: {type(session_state.get('facts'))}, "
            f"value: {session_state.get('facts')}"
        )

    # Log session save with key verification
    from core.session.session_schema_v2 import get_intent_name, get_planning_status

    intent_name = get_intent_name(session_state)
    status = get_planning_status(session_state)
    slots_keys = list((session_state.get("planning", {}) or {}).get("slots", {}).keys())
    if not slots_keys:
        slots_keys = list((session_state.get("slots") or {}).keys())
    logger.debug(
        "[SESSION_SAVE] user_id=%s intent_name=%r status=%r slots_keys=%s",
        user_id,
        intent_name,
        status,
        slots_keys,
    )

    redis_client = _get_redis_client()
    if redis_client:
        # Try Redis first
        try:
            key = _get_session_key(organization_id, user_id)
            serialized = json.dumps(session_state)
            from core.config.session_freshness import load_session_freshness_settings

            redis_client.setex(
                key,
                load_session_freshness_settings().session_ttl_seconds,
                serialized,
            )
            logger.debug("[SESSION_SAVE] saved to Redis: user_id=%s", user_id)
            return
        except Exception as e:
            logger.warning(
                "[SESSION_SAVE] Redis save failed, falling back to in-memory: user_id=%s error=%s",
                user_id,
                e,
            )
            # Fall through to in-memory fallback
            pass

    # In-memory fallback
    session_data = session_state.copy()
    from core.clock import utc_now

    session_data["_stored_at"] = utc_now().timestamp()
    key = _get_session_key(organization_id, user_id)
    _in_memory_sessions[key] = session_data
    logger.debug("[SESSION_SAVE] saved to in-memory: user_id=%s", user_id)


def clear_session(organization_id: int, user_id: str) -> None:
    """
    Clear session state for a user.

    Args:
        user_id: Unique identifier for the user
    """
    redis_client = _get_redis_client()
    if redis_client:
        # Try Redis first
        try:
            key = _get_session_key(organization_id, user_id)
            redis_client.delete(key)
        except Exception:
            # Fall through to in-memory fallback
            pass

    # In-memory fallback
    _in_memory_sessions.pop(_get_session_key(organization_id, user_id), None)


# Validate Redis connection at startup if REDIS_URL is configured
validate_redis_connection()
