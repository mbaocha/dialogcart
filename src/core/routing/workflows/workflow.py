"""
Workflow Interface and Registry

Defines the workflow interface for extending core behavior without modifying
orchestration, routing, or rendering logic.

Workflows are optional hooks that can:
- Observe outcomes
- Inject facts/context
- Trigger side effects

Workflows may NOT:
- Change plan status
- Bypass confirmation
- Introduce new orchestration states
"""

from typing import Dict, Any, Optional, Protocol
import logging

logger = logging.getLogger(__name__)


class Workflow(Protocol):
    """
    Workflow interface for extending core behavior.
    
    Workflows are optional hooks that can observe and modify outcomes,
    but cannot alter orchestration state or decision logic.
    
    This is a Protocol (structural typing) - any class implementing
    the required methods is a valid Workflow.
    """
    
    # Intent name this workflow handles
    intent_name: str
    
    def after_execute(self, outcome: Dict[str, Any]) -> Dict[str, Any]:
        """
        Hook invoked after successful execution (outcome.status == EXECUTED).
        
        This hook is called after a commit action has successfully executed
        and the outcome has been constructed. The workflow can observe the
        outcome and inject additional data into facts.context.
        
        Args:
            outcome: The outcome dictionary with status, booking_code, etc.
            
        Returns:
            Modified outcome dictionary (typically with enriched facts.context)
            
        Note:
            - This hook is optional - if not implemented, outcome is unchanged
            - Workflows should NOT change outcome.status or orchestration fields
            - Workflows should inject data into facts.context for rendering
        """
        return outcome


class WorkflowRegistry:
    """
    Registry for workflows by intent name.
    
    This is a simple in-memory registry. Workflows are registered at
    module import time or during application initialization.
    """
    
    def __init__(self):
        self._workflows: Dict[str, Workflow] = {}
    
    def register(self, workflow: Workflow) -> None:
        """
        Register a workflow for its intent_name.
        
        Args:
            workflow: Workflow instance implementing the Workflow protocol
            
        Raises:
            ValueError: If workflow.intent_name is empty or already registered
        """
        intent_name = getattr(workflow, "intent_name", None)
        if not intent_name:
            raise ValueError("Workflow must have a non-empty intent_name attribute")
        
        if intent_name in self._workflows:
            logger.warning(
                f"Workflow for intent '{intent_name}' is already registered. "
                f"Overwriting with new workflow."
            )
        
        self._workflows[intent_name] = workflow
        logger.info(f"Registered workflow for intent '{intent_name}'")
    
    def get(self, intent_name: str) -> Optional[Workflow]:
        """
        Get workflow for an intent name.
        
        Args:
            intent_name: Intent name to look up
            
        Returns:
            Registered workflow or None if not found
        """
        return self._workflows.get(intent_name)
    
    def has_workflow(self, intent_name: str) -> bool:
        """
        Check if a workflow is registered for an intent.
        
        Args:
            intent_name: Intent name to check
            
        Returns:
            True if workflow is registered, False otherwise
        """
        return intent_name in self._workflows


# Global workflow registry instance
_workflow_registry = WorkflowRegistry()


def register_workflow(workflow: Workflow) -> None:
    """
    Register a workflow in the global registry.
    
    This is the public API for registering workflows. Workflows should be
    registered at module import time or during application initialization.
    
    Args:
        workflow: Workflow instance implementing the Workflow protocol
        
    Example:
        class PaymentWorkflow:
            intent_name = "PAYMENT"
            
            def after_execute(self, outcome):
                # Inject payment prompt into context
                facts = outcome.get("facts", {})
                context = facts.get("context", {})
                context["payment_prompt"] = "Do you want to pay now or later?"
                facts["context"] = context
                outcome["facts"] = facts
                return outcome
        
        register_workflow(PaymentWorkflow())
    """
    _workflow_registry.register(workflow)


def get_workflow(intent_name: str) -> Optional[Workflow]:
    """
    Get workflow for an intent name from the global registry.
    
    Args:
        intent_name: Intent name to look up
        
    Returns:
        Registered workflow or None if not found
    """
    return _workflow_registry.get(intent_name)


def has_workflow(intent_name: str) -> bool:
    """
    Check if a workflow is registered for an intent.
    
    Args:
        intent_name: Intent name to check
        
    Returns:
        True if workflow is registered, False otherwise
    """
    return _workflow_registry.has_workflow(intent_name)

