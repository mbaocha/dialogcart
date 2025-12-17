# dialogcart_demo.py

import re
from typing import Dict, Tuple

# -----------------------------
# 1. Templates (UI Layer)
# -----------------------------

TEMPLATES = {
    "payment_request": {
        "message": "💳 Deposit required: {{currency}}{{amount}}\nTap below to pay securely.",
        "cta": {
            "label": "Pay {{currency}}{{amount}}",
            "url": "{{payment_link}}"
        }
    },
    "ask_checkin": {
        "message": "Sure 😊 What is your check-in date?"
    },
    "fallback": {
        "message": (
            "I want to help 😊\n"
            "You can say things like:\n"
            "• Book a room\n"
            "• Pay deposit\n"
            "• Ask about prices"
        )
    }
}

# -----------------------------
# 2. Very Simple Rule / ML Intent Detector
# -----------------------------


def rule_intent_detector(text: str) -> Tuple[str, float]:
    text = text.lower()

    if "pay" in text or "deposit" in text or "half" in text:
        return "MAKE_PAYMENT", 0.55

    if "book" in text or "room" in text:
        return "BOOK_ROOM", 0.75

    return "UNKNOWN", 0.3


# -----------------------------
# 3. Mock LLM Fallback (NO API)
# -----------------------------
# This simulates what an LLM would do
# It NEVER generates user text

def llm_fallback(text: str) -> Dict:
    text = text.lower()

    if "half" in text or "deposit" in text:
        return {
            "intent": "MAKE_PAYMENT",
            "slots": {"payment_type": "deposit"}
        }

    return {
        "intent": "UNKNOWN",
        "slots": {}
    }


# -----------------------------
# 4. Business Logic / Policy Layer
# -----------------------------

def decide_action(intent: str, slots: Dict, state: Dict) -> Dict:
    """
    Decides what to do and prepares render context
    """
    if intent == "MAKE_PAYMENT":
        if state.get("deposit_allowed"):
            amount = int(state["total"] * 0.5)
            return {
                "template_id": "payment_request",
                "context": {
                    "currency": state["currency"],
                    "amount": f"{amount:,}",
                    "payment_link": state["payment_link"]
                }
            }

    if intent == "BOOK_ROOM":
        return {
            "template_id": "ask_checkin",
            "context": {}
        }

    return {
        "template_id": "fallback",
        "context": {}
    }


# -----------------------------
# 5. Template Renderer
# -----------------------------

def render_template(template: Dict, context: Dict) -> Dict:
    def replace(value):
        if isinstance(value, str):
            for k, v in context.items():
                value = value.replace(f"{{{{{k}}}}}", str(v))
        return value

    rendered = {}
    for key, value in template.items():
        if isinstance(value, dict):
            rendered[key] = render_template(value, context)
        else:
            rendered[key] = replace(value)

    return rendered


# -----------------------------
# 6. Main Chat Handler
# -----------------------------

def handle_message(user_text: str, state: Dict):
    print(f"\nUSER: {user_text}")

    # Step 1: Rule / ML intent detection
    intent, confidence = rule_intent_detector(user_text)

    # Step 2: LLM fallback if confidence is low
    slots = {}
    if confidence < 0.6:
        llm_result = llm_fallback(user_text)
        intent = llm_result["intent"]
        slots = llm_result["slots"]

    # Step 3: Decide action + template
    decision = decide_action(intent, slots, state)
    template = TEMPLATES[decision["template_id"]]

    # Step 4: Render template
    rendered = render_template(template, decision["context"])

    # Step 5: Output (simulating WhatsApp send)
    print("\nASSISTANT:")
    print(rendered["message"])
    if "cta" in rendered:
        print(f"[ {rendered['cta']['label']} ] → {rendered['cta']['url']}")


# -----------------------------
# 7. Demo Run
# -----------------------------

if __name__ == "__main__":
    # Mock booking state
    STATE = {
        "currency": "₦",
        "total": 170000,
        "deposit_allowed": True,
        "payment_link": "https://pay.dialogcart.com/tx/abc123"
    }

    # Try different messages
    handle_message("Can I pay half now?", STATE)
    handle_message("I want to book a room", STATE)
    handle_message("Do something for me", STATE)
