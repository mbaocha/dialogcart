"""
Tests for workflow registry and registration.
"""

import pytest

from core.workflows import (
    WorkflowRegistry,
    register_workflow,
    get_workflow,
    has_workflow,
)


class TestWorkflow:
    """Test workflow implementation."""
    
    intent_name = "TEST_INTENT"
    
    def after_execute(self, outcome):
        outcome["facts"]["context"]["test"] = "value"
        return outcome


class TestWorkflowRegistry:
    """Test workflow registry functionality."""
    
    def test_register_workflow(self):
        """Verify workflows can be registered."""
        registry = WorkflowRegistry()
        workflow = TestWorkflow()
        
        registry.register(workflow)
        
        assert registry.has_workflow("TEST_INTENT") is True
        assert registry.get("TEST_INTENT") == workflow
    
    def test_register_workflow_overwrites_existing(self):
        """Verify registering same intent overwrites previous workflow."""
        registry = WorkflowRegistry()
        workflow1 = TestWorkflow()
        workflow2 = TestWorkflow()
        
        registry.register(workflow1)
        registry.register(workflow2)
        
        assert registry.get("TEST_INTENT") == workflow2
    
    def test_get_workflow_returns_none_if_not_registered(self):
        """Verify get_workflow returns None for unregistered intents."""
        registry = WorkflowRegistry()
        
        assert registry.get("UNREGISTERED_INTENT") is None
        assert registry.has_workflow("UNREGISTERED_INTENT") is False
    
    def test_register_workflow_requires_intent_name(self):
        """Verify workflow must have intent_name attribute."""
        registry = WorkflowRegistry()
        
        class WorkflowWithoutIntent:
            def after_execute(self, outcome):
                return outcome
        
        with pytest.raises(ValueError, match="intent_name"):
            registry.register(WorkflowWithoutIntent())


class TestGlobalRegistry:
    """Test global workflow registry functions."""
    
    def test_register_workflow_global(self):
        """Verify register_workflow works with global registry."""
        workflow = TestWorkflow()
        
        register_workflow(workflow)
        
        assert has_workflow("TEST_INTENT") is True
        assert get_workflow("TEST_INTENT") == workflow
    
    def test_get_workflow_global(self):
        """Verify get_workflow works with global registry."""
        workflow = TestWorkflow()
        register_workflow(workflow)
        
        retrieved = get_workflow("TEST_INTENT")
        assert retrieved == workflow
    
    def test_has_workflow_global(self):
        """Verify has_workflow works with global registry."""
        assert has_workflow("NONEXISTENT") is False
        
        workflow = TestWorkflow()
        register_workflow(workflow)
        
        assert has_workflow("TEST_INTENT") is True

