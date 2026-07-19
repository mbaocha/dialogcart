"""
Planning algorithms and supporting logic.

Not the turn-orchestration layer — that lives in ``planning.pipeline``.

Modules:
- turn_planner: Thin production entry that delegates to ``run_planning_pipeline``
- intent_resolution: Effective-intent resolution and merge rules
- missing_slots: Policy required-slot lookup and MODIFY_BOOKING normalization
- plan_builder: Post-execution outcome overlay / status projection helpers
"""
