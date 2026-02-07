"""
Capability Runner

Thin orchestrator that drives capability adapters when core emits
status == "AWAITING_CAPABILITY".

The runner sits outside core and is the only layer that knows adapters exist.
It manages adapter lifecycle, routes user input, and merges facts back into session.

Design:
- Runner is capability-agnostic (works with any adapter)
- Adapters are fully sandboxed (no direct core access)
- Core remains unchanged (runner is external)
- One adapter can be swapped without touching runner
- Facts are the only bridge back to core
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

from .base import CapabilityAdapter, AdapterResponse
from .registry import ADAPTER_REGISTRY, get_adapter

logger = logging.getLogger(__name__)


@dataclass
class RunnerResult:
    """
    Result from capability runner.

    This is returned to the caller to indicate how to proceed after
    capability handling.

    Rules:
    - `passthrough=True` → send input to core (normal flow)
    - `passthrough=False` → adapter handled input, show text, wait for next input
    - `text` is adapter prompt to show user (if any)
    - `facts` are merged into session only when adapter completes
    """
    passthrough: bool
    """
    Whether to pass through to core.
    
    True: Adapter completed or no capability active → core handles next
    False: Adapter is active → show text and wait for next user input
    """
    
    text: Optional[str] = None
    """Adapter prompt to show user (if any). Only set when passthrough=False."""
    
    facts: Optional[Dict[str, Any]] = None
    """
    Facts to merge into session context.
    
    Only set when adapter completes (completed=True).
    Facts are merged by caller, not by runner.
    """
    
    active_capability: Optional[str] = None
    """
    Current active capability name.
    
    Set when capability is active (passthrough=False).
    Cleared when capability completes (passthrough=True, facts set).
    """


class CapabilityRunner:
    """
    Capability runner that orchestrates adapters when core emits AWAITING_CAPABILITY.
    
    Responsibilities:
    - Detect capability activation from core outcome
    - Look up adapter from registry
    - Route user input to adapter
    - Manage adapter lifecycle (start, handle_input, abort)
    - Persist adapter-local state
    - Merge adapter facts into session when complete
    - Clear active_capability when adapter completes
    
    The runner is the only layer that knows adapters exist. Core remains unchanged.
    """
    
    def __init__(self, state_store: Optional[Any] = None):
        """
        Initialize capability runner.
        
        Args:
            state_store: Optional state store for adapter-local state.
                        If None, uses in-memory store.
        """
        self._state_store = state_store or InMemoryStateStore()
        logger.info("CapabilityRunner initialized")
    
    def handle(
        self,
        user_input: Optional[str],
        core_outcome: Dict[str, Any],
        context: Dict[str, Any]
    ) -> RunnerResult:
        """
        Handle capability activation and routing.
        
        This is the main entry point for the runner. It:
        1. Detects if capability is active (status == AWAITING_CAPABILITY)
        2. Routes to adapter if active
        3. Returns result indicating how caller should proceed
        
        Args:
            user_input: Optional user message text. None on first activation.
            core_outcome: Core outcome dictionary with status, active_capability, etc.
            context: Context dictionary with user_id, session_slots, session_facts, etc.
        
        Returns:
            RunnerResult indicating:
            - passthrough: Whether to send input to core
            - text: Adapter prompt (if any)
            - facts: Facts to merge (if adapter completed)
            - active_capability: Current capability name (if active)
        
        Example:
            # First activation
            result = runner.handle(
                user_input=None,
                core_outcome={"status": "AWAITING_CAPABILITY", "active_capability": "payment"},
                context={"user_id": "user123", "session_slots": {}, "session_facts": {}}
            )
            # Returns: RunnerResult(passthrough=False, text="Please select payment method", active_capability="payment")
            
            # User provides input
            result = runner.handle(
                user_input="credit card",
                core_outcome={"status": "AWAITING_CAPABILITY", "active_capability": "payment"},
                context={"user_id": "user123", "session_slots": {}, "session_facts": {}}
            )
            # Returns: RunnerResult(passthrough=False, text="Please enter card number", active_capability="payment")
            
            # Adapter completes
            result = runner.handle(
                user_input="4111 1111 1111 1111",
                core_outcome={"status": "AWAITING_CAPABILITY", "active_capability": "payment"},
                context={"user_id": "user123", "session_slots": {}, "session_facts": {}}
            )
            # Returns: RunnerResult(passthrough=True, facts={"payment_satisfied": True}, active_capability=None)
        """
        # Step 1: Detect capability activation
        if core_outcome.get("status") != "AWAITING_CAPABILITY":
            # No capability active → passthrough to core
            logger.debug("No capability active, passthrough to core")
            return RunnerResult(passthrough=True)
        
        # Step 2: Extract capability name
        capability = core_outcome.get("active_capability")
        if not capability:
            logger.warning("AWAITING_CAPABILITY status but no active_capability in core_outcome")
            return RunnerResult(passthrough=True)
        
        # Step 3: Look up adapter
        try:
            adapter = get_adapter(capability)
        except KeyError:
            logger.error(f"Adapter not registered for capability: {capability}")
            # Fail fast - do not proceed without adapter
            return RunnerResult(passthrough=True)
        
        # Step 4: Determine if first activation
        user_id = context.get("user_id")
        if not user_id:
            logger.error("user_id missing from context")
            return RunnerResult(passthrough=True)
        
        is_first_activation = self._is_first_activation(user_id, capability)
        
        # Step 5: Route to adapter
        try:
            if is_first_activation:
                logger.info(f"First activation of capability: {capability} for user: {user_id}")
                response = adapter.start(context)
            else:
                if not user_input:
                    logger.warning(f"Capability {capability} active but no user_input provided")
                    return RunnerResult(passthrough=True)
                logger.debug(f"Handling input for capability: {capability} for user: {user_id}")
                response = adapter.handle_input(user_input, context)
        except Exception as e:
            logger.error(f"Adapter error for capability {capability}: {e}", exc_info=True)
            # Abort adapter on error
            try:
                adapter.abort(reason="error", context=context)
            except Exception as abort_error:
                logger.error(f"Adapter abort error: {abort_error}", exc_info=True)
            # Clear adapter state
            self._clear_adapter_state(user_id, capability)
            # Passthrough to core (core will handle error state)
            return RunnerResult(passthrough=True)
        
        # Step 6: Handle adapter response
        if not isinstance(response, AdapterResponse):
            logger.error(f"Adapter returned invalid response type: {type(response)}")
            return RunnerResult(passthrough=True)
        
        # Step 7: Process response
        if response.completed:
            # Adapter completed → merge facts and clear capability
            logger.info(f"Capability {capability} completed for user: {user_id}")
            
            # Persist facts (will be merged by caller)
            facts = response.facts or {}
            
            # Clear adapter-local state
            self._clear_adapter_state(user_id, capability)
            
            # Return result indicating completion
            return RunnerResult(
                passthrough=True,  # Core handles next
                facts=facts,  # Facts to merge
                active_capability=None  # Capability cleared
            )
        else:
            # Adapter still active → persist state and return prompt
            logger.debug(f"Capability {capability} still active for user: {user_id}")
            
            # Persist adapter-local state (if adapter maintains state)
            # Note: Adapter state is separate from core session
            self._persist_adapter_state(user_id, capability, response)
            
            # Return result indicating adapter is active
            return RunnerResult(
                passthrough=False,  # Do not send to core yet
                text=response.text,  # Show adapter prompt
                active_capability=capability  # Capability still active
            )
    
    def abort(
        self,
        reason: str,
        core_outcome: Dict[str, Any],
        context: Dict[str, Any]
    ) -> None:
        """
        Abort active capability.
        
        Called when:
        - Intent changes
        - User cancels
        - Timeout occurs
        - Core error occurs
        
        Args:
            reason: Reason for abortion ("intent_change", "user_cancel", "timeout", "error")
            core_outcome: Core outcome dictionary
            context: Context dictionary with user_id, etc.
        """
        capability = core_outcome.get("active_capability")
        if not capability:
            # No capability active
            return
        
        user_id = context.get("user_id")
        if not user_id:
            logger.warning("Cannot abort capability: user_id missing from context")
            return
        
        try:
            adapter = get_adapter(capability)
        except KeyError:
            logger.warning(f"Adapter not registered for capability: {capability}, skipping abort")
            self._clear_adapter_state(user_id, capability)
            return
        
        try:
            logger.info(f"Aborting capability {capability} for user {user_id}, reason: {reason}")
            adapter.abort(reason=reason, context=context)
        except Exception as e:
            logger.error(f"Error during adapter abort: {e}", exc_info=True)
        finally:
            # Always clear adapter state, even if abort fails
            self._clear_adapter_state(user_id, capability)
    
    def _is_first_activation(self, user_id: str, capability: str) -> bool:
        """
        Check if this is the first activation of the capability for this user.
        
        Args:
            user_id: User identifier
            capability: Capability name
        
        Returns:
            True if first activation, False otherwise
        """
        state = self._state_store.get(user_id, capability)
        return state is None
    
    def _persist_adapter_state(self, user_id: str, capability: str, response: AdapterResponse) -> None:
        """
        Persist adapter-local state.
        
        Note: This is separate from core session state. Adapter state is:
        - Adapter-owned
        - Short-lived (scoped to capability session)
        - Stored separately from core session
        
        Args:
            user_id: User identifier
            capability: Capability name
            response: Adapter response (may contain state hints)
        """
        # Store activation flag (indicates capability is active)
        self._state_store.set(user_id, capability, {"active": True})
        logger.debug(f"Persisted adapter state for {capability} for user {user_id}")
    
    def _clear_adapter_state(self, user_id: str, capability: str) -> None:
        """
        Clear adapter-local state.
        
        Called when:
        - Adapter completes
        - Adapter aborts
        - Error occurs
        
        Args:
            user_id: User identifier
            capability: Capability name
        """
        self._state_store.delete(user_id, capability)
        logger.debug(f"Cleared adapter state for {capability} for user {user_id}")


class InMemoryStateStore:
    """
    In-memory state store for adapter-local state.
    
    This is a simple implementation for single-process deployments.
    For distributed deployments, use Redis or database-backed store.
    
    State structure:
    {
        (user_id, capability): state_dict
    }
    """
    
    def __init__(self):
        self._store: Dict[tuple, Dict[str, Any]] = {}
    
    def get(self, user_id: str, capability: str) -> Optional[Dict[str, Any]]:
        """Get adapter state for user and capability."""
        key = (user_id, capability)
        return self._store.get(key)
    
    def set(self, user_id: str, capability: str, state: Dict[str, Any]) -> None:
        """Set adapter state for user and capability."""
        key = (user_id, capability)
        self._store[key] = state
    
    def delete(self, user_id: str, capability: str) -> None:
        """Delete adapter state for user and capability."""
        key = (user_id, capability)
        self._store.pop(key, None)

