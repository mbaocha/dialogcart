"""
Intent Planning Module

Public planning API for ConversationEngine: ``planning_service.plan_message``.

Package roles:
- ``pipeline`` — turn orchestration (CurrentRequest → Attach → Evidence → Decision)
- ``planner`` — planning algorithms and supporting helpers used by the pipeline
- ``policy`` — static intent/action policy tables
"""

from .planning_service import plan_message

__all__ = [
    "plan_message",
]
