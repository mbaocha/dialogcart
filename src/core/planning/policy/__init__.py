"""
Planning Policy Module

Contains policy modules for planning decisions:
- action_policy: Action planning (executable_actions determination)
- stage_policy: Stage-based dialog policy (NEEDS_CLARIFICATION, AWAITING_CONFIRMATION)
- handler_router: Intent → handler mapping (intent_handlers.yaml)
- base_intents: Core-owned intent membership
- intent_router: Legacy intent → action name mapping
"""
