"""
Memory Module

Provides conversational memory for booking state persistence.
"""

from .store import MemoryStore
from .redis_store import RedisMemoryStore
from .policy import (
    is_active_booking,
    is_partial_booking,
    maybe_persist_draft,
    normalize_intent_for_continuation,
    is_continuation_applicable,
    detect_continuation,
    merge_continuation_semantics,
    detect_contextual_update,
    should_clear_memory,
    should_persist_memory,
    prepare_memory_for_persistence,
    get_final_memory_state,
    CONTEXTUAL_UPDATE
)

__all__ = [
    'MemoryStore',
    'RedisMemoryStore',
    'is_active_booking',
    'is_partial_booking',
    'maybe_persist_draft',
    'normalize_intent_for_continuation',
    'is_continuation_applicable',
    'detect_continuation',
    'merge_continuation_semantics',
    'detect_contextual_update',
    'should_clear_memory',
    'should_persist_memory',
    'prepare_memory_for_persistence',
    'get_final_memory_state',
    'CONTEXTUAL_UPDATE'
]

