"""
Payment Prompt Workflow Example

Example workflow that injects a payment prompt after booking creation.
This demonstrates how workflows can enrich outcomes without modifying core logic.
"""

from typing import Dict, Any
from core.routing.workflows import Workflow

# from core.routing.workflows import register_workflow


class PaymentPromptWorkflow:
    """
    Example workflow that adds a payment prompt after booking creation.
    
    This workflow observes CREATE_APPOINTMENT outcomes and injects
    a payment prompt into facts.context for rendering.
    """
    
    intent_name = "CREATE_APPOINTMENT"
    
    def after_execute(self, outcome: Dict[str, Any]) -> Dict[str, Any]:
        """
        Inject payment prompt into outcome facts.context.
        
        Args:
            outcome: Outcome dictionary with status="EXECUTED"
            
        Returns:
            Outcome with payment_prompt added to facts.context
        """
        # Ensure facts structure exists
        if "facts" not in outcome:
            outcome["facts"] = {}
        if "context" not in outcome["facts"]:
            outcome["facts"]["context"] = {}
        
        # Inject payment prompt
        outcome["facts"]["context"]["payment_prompt"] = (
            "Your booking is confirmed! Would you like to pay now or later?"
        )
        
        return outcome


# Example: Register workflow (commented out to avoid auto-registration)
# register_workflow(PaymentPromptWorkflow())

