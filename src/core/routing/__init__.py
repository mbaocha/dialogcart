"""
Routing Layer

Maps semantic signals (clarification reasons, intent names) to internal identifiers
(template keys, action names).

This layer contains pure decision tables with no side effects, no execution,
and no rendering logic. It is responsible for:
- clarification_reason → template_key mapping
- intent_name → action_name mapping
- YAML/JSON decision tables (e.g. config/clarification_templates.yaml)

Constraints:
- No side effects
- No rendering
- No external calls
"""

from core.routing.clarification_router import get_template_key
from core.routing.intent_router import get_action_name

__all__ = ["get_template_key", "get_action_name"]

