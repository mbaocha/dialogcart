"""
Tests for workflow invocation in orchestrator.
"""

import pytest
from unittest.mock import Mock, patch

from core.workflows import register_workflow
from core.orchestration.orchestrator import _invoke_workflow_after_execute


class TestWorkflowInvocation:
    """Test workflow invocation hooks."""
    
    def test_invoke_workflow_when_registered(self):
        """Verify workflow after_execute is invoked when registered."""
        class TestWorkflow:
            intent_name = "CREATE_APPOINTMENT"
            called = False
            
            def after_execute(self, outcome):
                self.called = True
                outcome["facts"]["context"]["test"] = "injected"
                return outcome
        
        workflow = TestWorkflow()
        register_workflow(workflow)
        
        outcome = {
            "status": "EXECUTED",
            "booking_code": "ABC123",
            "facts": {"context": {}}
        }
        
        result = _invoke_workflow_after_execute("CREATE_APPOINTMENT", outcome)
        
        assert workflow.called is True
        assert result["facts"]["context"]["test"] == "injected"
    
    def test_invoke_workflow_when_not_registered(self):
        """Verify outcome unchanged when no workflow registered."""
        outcome = {
            "status": "EXECUTED",
            "booking_code": "ABC123",
        }
        
        result = _invoke_workflow_after_execute("UNREGISTERED_INTENT", outcome)
        
        assert result == outcome
    
    def test_invoke_workflow_creates_facts_structure(self):
        """Verify facts structure is created if missing."""
        class TestWorkflow:
            intent_name = "CREATE_RESERVATION"
            
            def after_execute(self, outcome):
                outcome["facts"]["context"]["test"] = "value"
                return outcome
        
        register_workflow(TestWorkflow())
        
        outcome = {
            "status": "EXECUTED",
            "booking_code": "XYZ789",
        }
        
        result = _invoke_workflow_after_execute("CREATE_RESERVATION", outcome)
        
        assert "facts" in result
        assert "context" in result["facts"]
        assert result["facts"]["context"]["test"] == "value"
    
    def test_invoke_workflow_handles_exceptions_gracefully(self):
        """Verify workflow exceptions don't break core flow."""
        class FailingWorkflow:
            intent_name = "MODIFY_BOOKING"
            
            def after_execute(self, outcome):
                raise ValueError("Workflow error")
        
        register_workflow(FailingWorkflow())
        
        outcome = {
            "status": "EXECUTED",
            "booking_code": "DEF456",
        }
        
        # Should not raise, should return original outcome
        result = _invoke_workflow_after_execute("MODIFY_BOOKING", outcome)
        
        assert result == outcome
    
    def test_invoke_workflow_preserves_outcome_structure(self):
        """Verify workflow cannot break outcome structure."""
        class PreservingWorkflow:
            intent_name = "CANCEL_BOOKING"
            
            def after_execute(self, outcome):
                # Workflow should preserve required fields
                assert "status" in outcome
                assert outcome["status"] == "EXECUTED"
                outcome["facts"]["context"]["cancelled"] = True
                return outcome
        
        register_workflow(PreservingWorkflow())
        
        outcome = {
            "status": "EXECUTED",
            "booking_code": "GHI789",
            "booking_status": "cancelled",
        }
        
        result = _invoke_workflow_after_execute("CANCEL_BOOKING", outcome)
        
        # Required fields preserved
        assert result["status"] == "EXECUTED"
        assert result["booking_code"] == "GHI789"
        assert result["booking_status"] == "cancelled"
        # Workflow injected data
        assert result["facts"]["context"]["cancelled"] is True


class TestWorkflowExample:
    """Test the example payment prompt workflow."""
    
    def test_payment_prompt_workflow_injects_prompt(self):
        """Verify example workflow injects payment prompt."""
        from core.workflows.examples.payment_prompt_workflow import PaymentPromptWorkflow
        
        workflow = PaymentPromptWorkflow()
        register_workflow(workflow)
        
        outcome = {
            "status": "EXECUTED",
            "booking_code": "ABC123",
            "facts": {"context": {}}
        }
        
        result = _invoke_workflow_after_execute("CREATE_APPOINTMENT", outcome)
        
        assert result["facts"]["context"]["payment_prompt"] == "Do you want to pay now or later?"

