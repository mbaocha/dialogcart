"""
Prompt templates for LLM validation
"""

def validator_prompt() -> str:
    return """
You validate and, if necessary, correct cart actions extracted from user messages.

Given:
- User said: "<text>"
- Rasa extracted actions: <json list>

Goals:
1) If mapping is correct, return the same actions.
2) If incorrect or incomplete, correct them. Use only:
   action ∈ { add, remove, increase, decrease, set, check, unknown }
   product: exact phrase from the user if present
   quantity: numeric if present
   unit: short unit like kg, g, lb, piece, bag, box
3) Do not invent products or quantities that are not clearly present.

Return ONLY JSON in this shape:
{
  "actions": [
    { "action": "add", "product": "rice", "quantity": 2, "unit": "kg" }
  ]
}
"""


