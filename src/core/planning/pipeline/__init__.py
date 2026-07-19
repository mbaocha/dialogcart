"""Canonical staged planning pipeline (orchestration layer).

``run_planning_pipeline`` owns numbered Stages 01–09. Request/Attach models and
Decision surfaces are consolidated by architectural boundary; supporting
algorithms live under ``planning.planner``.
"""

from core.planning.pipeline.orchestrator import run_planning_pipeline

__all__ = [
    "run_planning_pipeline",
]
