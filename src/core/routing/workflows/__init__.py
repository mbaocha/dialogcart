"""
Core Workflow System

This package provides extensibility hooks for workflows without modifying core logic.
Workflows can observe outcomes and inject data, but cannot alter orchestration state.
"""

from .workflow import Workflow, WorkflowRegistry, register_workflow, get_workflow, has_workflow

__all__ = ["Workflow", "WorkflowRegistry", "register_workflow", "get_workflow", "has_workflow"]

