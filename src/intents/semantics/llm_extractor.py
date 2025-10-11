import json
import time
from openai import OpenAI

client = OpenAI()

def create_cart_and_check_prompt(sentence: str) -> str:
    return f"""You extract e-commerce intents and entities from user messages related to
shopping, cart actions, or product availability.

USER: "{sentence}"

ENTITY TYPES:
- products: item names
- brands: brand names
- quantities: numbers only (2, 5, 10)
- units: measurement or count words (kg, g, bottles, packs, pieces, etc.)
- variants: attributes (color, size, shade, style)

BRAND RULE:
If a brand name appears alone (“Do you sell Coca-Cola?”), treat it as a product.
If it modifies a noun (“Coca-Cola soda”, “Nike shoes”), tag brand separately and noun as product.

CART & PRODUCT INTENTS:
- add → adding or buying new items or increasing quantity
- remove → taking items out or decreasing quantity
- set → changing a product’s quantity or variant
- clear → emptying the entire cart
- get → viewing or checking cart contents
- check_product_existence → asking whether a product is available or sold
- none → message unrelated to these intents (e.g., checkout, delivery, greetings, support)

OUTPUT FORMAT:
{{
  "status": "success|error|no_entities_found",
  "reason": "",
  "groups": [
    {{
      "intent": "add|remove|set|clear|get|check_product_existence|none",
      "action": "user verb or phrase (e.g., 'add', 'check out')",
      "products": ["..."],
      "quantities": ["..."],
      "units": ["..."],
      "brands": ["..."],
      "variants": ["..."]
    }}
  ],
  "notes": []
}}

RULES:
- Always include at least one group.
- Quantities = numbers only; units = measurement words.
- Lowercase all values.
- If multiple intents appear, create multiple groups.
- If out of scope, use intent = "none" and fill 'action' with the literal phrase.
- Do not resolve pronouns (“it”, “them”).

SPELLING CORRECTION:
- If a product or brand name appears misspelled but the intended word is clear (e.g., "cheicken" → "chicken"), fix it.
- If you are not highly confident of the correction, leave the original spelling unchanged.
- Always record any corrections you make in the "notes" field (e.g., "corrected 'cheicken' → 'chicken'").
"""

def extract_cart_and_check_entities(sentence: str, model="gpt-4o-mini"):
    """
    Sends the prompt to OpenAI and returns parsed JSON output, with timing.
    """
    prompt = create_cart_and_check_prompt(sentence)
    start_time = time.time()

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a structured JSON generator. Always return valid JSON only."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.0,
        max_tokens=300,
    )

    elapsed = time.time() - start_time
    text_output = response.choices[0].message.content.strip()

    try:
        parsed = json.loads(text_output)
    except json.JSONDecodeError:
        parsed = {"status": "error", "reason": "invalid JSON", "raw_output": text_output}

    parsed["elapsed_seconds"] = round(elapsed, 2)
    return parsed


def main():
    print("\n🛍️  E-COMMERCE CART & PRODUCT INTENT EXTRACTOR")
    print("Type a user message and get structured JSON output.")
    print("Type 'exit' or 'quit' to stop.\n")

    while True:
        try:
            sentence = input("💬 User: ").strip()
            if sentence.lower() in ["exit", "quit"]:
                print("\n👋 Exiting. Goodbye!\n")
                break

            if not sentence:
                continue

            print("⏳ Processing...\n")
            result = extract_cart_and_check_entities(sentence)
            print("✅ Parsed Output:\n")
            print(json.dumps(result, indent=2))
            print(f"\n⚡ Response time: {result['elapsed_seconds']}s")
            print("\n" + "=" * 80 + "\n")

        except KeyboardInterrupt:
            print("\n\n👋 Exiting. Goodbye!\n")
            break
        except Exception as e:
            print(f"⚠️ Error: {e}\n")


if __name__ == "__main__":
    main()
