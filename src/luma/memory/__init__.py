"""
Memory Module

Provides conversational memory for booking state persistence.
"""

from .store import MemoryStore
from .redis_store import RedisMemoryStore
from .policy import (
    # Legacy functions (deprecated, kept for backward compatibility but not used in state-first model)
    is_active_booking,
    is_partial_booking,
    maybe_persist_draft,
    should_clear_memory,
    should_persist_memory,
    prepare_memory_for_persistence,
    get_final_memory_state,
    # New state-first model functions
    state_exists,
    is_new_task,
    get_state_intent,
    merge_slots_for_followup
)

__all__ = [
    'MemoryStore',
    'RedisMemoryStore',
    # Legacy functions (deprecated, kept for backward compatibility but not used in state-first model)
    'is_active_booking',
    'is_partial_booking',
    'maybe_persist_draft',
    'should_clear_memory',
    'should_persist_memory',
    'prepare_memory_for_persistence',
    'get_final_memory_state',
    # New state-first model functions
    'state_exists',
    'is_new_task',
    'get_state_intent',
    'merge_slots_for_followup'
]

