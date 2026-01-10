"""
Example Workflow: Payment Prompt

A minimal example workflow that injects a payment prompt into the outcome
after a booking is successfully created.

This demonstrates:
- Workflow registration
- Outcome mutation (injecting data into facts.context)
- No core edits required
"""

from typing import Dict, Any

from core.workflows import Workflow


class PaymentPromptWorkflow:
    """
    Example workflow that adds a payment prompt after booking creation.
    
    This workflow:
    - Observes CREATE_APPOINTMENT execution outcomes
    - Injects a payment prompt into facts.context
    - Does NOT change orchestration state or outcome status
    """
    
    intent_name = "CREATE_APPOINTMENT"
    
    def after_execute(self, outcome: Dict[str, Any]) -> Dict[str, Any]:
        """
        Inject payment prompt into outcome facts.context.
        
        Args:
            outcome: Outcome dictionary with status "EXECUTED"
            
        Returns:
            Outcome with enriched facts.context containing payment_prompt
        """
        # Ensure facts structure exists
        if "facts" not in outcome:
            outcome["facts"] = {}
        if "context" not in outcome["facts"]:
            outcome["facts"]["context"] = {}
        
        # Inject payment prompt into context
        outcome["facts"]["context"]["payment_prompt"] = (
            "Do you want to pay now or later?"
        )
        
        return outcome


# Example registration (commented out - workflows should be registered by application)
# from core.workflows import register_workflow
# register_workflow(PaymentPromptWorkflow())

