"""
Prompt templates for LLM service - extracted from llm.py
"""
from .config import INTENTS

def system_prompt() -> str:
    intents_list = "\n- " + "\n- ".join(INTENTS)
    return f"""
You are an intent classifier and slot extractor for a grocery shopping assistant.

Identify ALL intents present in the user's message (from this list):{intents_list}

For each intent, extract ONLY explicitly mentioned entities: product, quantity, unit.
❌ Do NOT guess or infer missing info from context or prior turns.
✅ If a field isn't clearly present, set it to null.

Return ONLY valid JSON in this exact shape:
{{
  "intents": [
    {{
      "intent": "ADD_TO_CART",
      "confidence": "high",
      "reasoning": "...",
      "entities": [
        {{
          "product": "rice",
          "quantity": 2,
          "unit": "kg",
          "raw": "2kg rice"
        }}
      ]
    }}
  ]
}}

If the user message is unclear, respond with exactly one intent: "NONE" with "low" confidence.
"""

def followup_prompt(target_product: str, missing: list) -> str:
    needs = ", ".join(missing)
    return f"""
You are extracting ONLY the missing slot values from the user's LATEST message.

Current target product (may be empty when 'product' is missing): "{target_product}"
Missing fields now: {needs}

Rules:
- Consider ONLY the latest user message (ignore earlier turns).
- Do NOT guess. If a field isn't explicitly present, return null.
- product should be the exact product phrase mentioned (short noun phrase).
- quantity must be numeric if present.
- unit must be a short unit like "kg", "g", "lb", "piece", "bag", "box".

Respond ONLY in JSON:
{{
  "product": <string or null>,
  "quantity": <number or null>,
  "unit": <string or null>
}}
"""
