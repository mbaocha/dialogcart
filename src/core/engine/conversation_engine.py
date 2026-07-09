"""ConversationEngine — Phase 1 thin coordinator for the full turn lifecycle.

Initially delegates entirely to the existing orchestration layer.
Phase 2 will wire in WorkflowRouter, ResponseRenderer, and SessionProjector directly.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ConversationEngine:
    """Coordinates the complete turn lifecycle.

    Owns no business logic. Every stage is delegated to a specialist component.

    Phase 1 pseudo-flow (fully delegated to existing orchestration):
        context = build_context(...)
        plan    = planner.plan(context)
        result  = workflow_router.execute(plan)
        text    = renderer.render(result)
        session = session_projector.project(result)
        return response

    Phase 2 will replace the delegation with direct calls to each component.
    """

    def handle_turn(
        self,
        text: str,
        user_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Coordinate a complete conversational turn (planning + execution + rendering)."""
        from core.orchestration.orchestrator import handle_message

        return handle_message(text=text, user_id=user_id, **kwargs)

    def plan_turn(
        self,
        text: str,
        user_id: str,
        session_state: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Return the planning result for a turn without executing."""
        from core.orchestration.orchestrator import plan_message

        return plan_message(
            text=text,
            user_id=user_id,
            session_state=session_state,
            **kwargs,
        )
