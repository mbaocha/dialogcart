"""
Capability Adapter Base Interface

Defines the contract for capability adapters that plug into DialogCart core
via the AWAITING_CAPABILITY status mechanism.

Design Principles:
- Adapters are intent-agnostic (do NOT know about booking intents)
- Adapters manage local, temporary state only
- Adapters resolve to boolean gate facts (e.g., payment_satisfied: true)
- Adapters are invoked only when core.status == AWAITING_CAPABILITY
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class AdapterResponse:
    """
    Response from a capability adapter.

    This is a pure data object that adapters return to communicate their state
    and any facts that should be merged into the session.

    Rules:
    - `facts` must contain only booleans/references, never secrets
    - `completed == True` implies the adapter will not be called again
    - `text` is optional (some steps may be silent)
    - `facts` are merged into session context, not core session state

    Example facts:
    {
        "payment_satisfied": True,
        "payment_method": "credit_card",
        "payment_reference": "txn_12345"
    }
    """

    completed: bool
    """Whether the capability is finished. When True, adapter will not be called again."""

    text: Optional[str] = None
    """Optional prompt to show the user. This is adapter-local rendering, not core rendering."""

    facts: Dict[str, Any] = field(default_factory=dict)
    """
    Facts to merge into session context.
    
    Must contain only:
    - Booleans (e.g., payment_satisfied: True)
    - References/IDs (e.g., payment_reference: "txn_12345")
    - Non-sensitive metadata
    
    Must NOT contain:
    - Secrets (passwords, tokens, PII)
    - Core session fields (intent_name, slots, status)
    - Intent-specific data (dates, times, services)
    """


class CapabilityAdapter(ABC):
    """
    Abstract base interface for capability adapters.

    Adapters handle external capabilities (payment, KYC, consent, etc.) that
    must be satisfied before core can proceed with intent execution.

    Adapter Lifecycle:
    1. Core emits AWAITING_CAPABILITY with active_capability="payment"
    2. Capability runner invokes adapter.start(context) on first activation
    3. User provides input → adapter.handle_input(user_input, context)
    4. Adapter returns AdapterResponse(completed=True) when done
    5. Core proceeds when capability fact (e.g., payment_satisfied) is True

    Constraints:
    - Adapters do NOT know about intents (intent-agnostic)
    - Adapters do NOT set global status (core controls status)
    - Adapters do NOT touch core state directly
    - Adapters do NOT render outside their active window
    - Adapters do NOT persist core session data

    Adapters MAY:
    - Manage local, temporary state (adapter-owned, short-lived)
    - Ask questions and validate input (within capability scope)
    - Resolve to boolean gate facts (e.g., payment_satisfied: True)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Unique identifier for this capability adapter.

        Examples: "payment", "kyc", "consent", "verification"

        This name must match the active_capability value emitted by core
        when status == AWAITING_CAPABILITY.
        """
        pass

    @abstractmethod
    def start(self, context: Dict[str, Any]) -> AdapterResponse:
        """
        Called once when the capability becomes active (first activation).

        This is invoked when core first emits AWAITING_CAPABILITY for this
        capability. The adapter should initialize its local state and return
        the first prompt (if any) to show the user.

        Args:
            context: Context dictionary containing:
                - user_id: str (user identifier)
                - session_slots: Dict (current session slots, read-only)
                - session_facts: Dict (current session facts, read-only)
                - Any other adapter-specific context

        Returns:
            AdapterResponse with:
                - completed: False (capability just started)
                - text: Optional prompt for user (e.g., "Please enter payment method")
                - facts: Empty dict or initial facts

        Example:
            return AdapterResponse(
                completed=False,
                text="Please select a payment method: credit card or PayPal",
                facts={}
            )
        """
        pass

    @abstractmethod
    def handle_input(self, user_input: str, context: Dict[str, Any]) -> AdapterResponse:
        """
        Called on subsequent user messages while this capability is active.

        This is invoked for each user message after start() until the adapter
        returns completed=True. The adapter should process the input, update
        its local state, and return the next prompt or completion status.

        Args:
            user_input: Raw user message text
            context: Context dictionary (same structure as start())

        Returns:
            AdapterResponse with:
                - completed: True if capability is satisfied, False otherwise
                - text: Optional prompt for next step or confirmation
                - facts: Facts to merge (e.g., {"payment_satisfied": True})

        Example:
            # User says "credit card"
            return AdapterResponse(
                completed=False,
                text="Please enter your card number",
                facts={"payment_method": "credit_card"}
            )

            # User completes payment
            return AdapterResponse(
                completed=True,
                text="Payment confirmed. Thank you!",
                facts={"payment_satisfied": True, "payment_reference": "txn_12345"}
            )
        """
        pass

    @abstractmethod
    def abort(self, reason: str, context: Dict[str, Any]) -> None:
        """
        Called if core aborts the capability (intent change, cancel, timeout).

        This allows the adapter to clean up its local state when the capability
        is interrupted before completion.

        Reasons:
        - "intent_change": User changed intent (e.g., CREATE_APPOINTMENT → CANCEL)
        - "user_cancel": User explicitly cancelled
        - "timeout": Capability timed out
        - "error": Core error occurred

        Args:
            reason: Reason for abortion
            context: Context dictionary (same structure as start())

        Note:
            Adapter should clean up local state but should NOT modify core state.
            Core will clear active_capability automatically.

        Example:
            # Clean up in-memory state
            self._clear_user_state(context["user_id"])
        """
        pass
