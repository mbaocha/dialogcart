from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

INTENTS = {
    "order_request": [],
    "product_inquiry": [],
    "other": []
}

class AgentIntentClassifier:
    def __init__(self, model_name="gpt-3.5-turbo"):
        self.llm = ChatOpenAI(model=model_name)

    def classify(self, message: str) -> list[dict]:
        system_prompt = (
            "You are an intent segmenter and classifier for a WhatsApp AI assistant focused on African/Caribbean groceries.\n\n"
            "Break down the user's message into separate parts if it expresses multiple intents. For each part, classify the intent using one of these:\n"
            "- order_request: When the user wants to buy or order something (e.g., 'I want to buy rice')\n"
            "- product_inquiry: When the user is asking about product availability, types, or what's offered (e.g., 'Do you have plantain?')\n"
            "- other: For all unrelated or general topics (e.g., greetings, delivery questions, etc.)\n\n"
            "Return a JSON list of objects, each with the intent as the key and the relevant part of the message as the value.\n"
            "Use lowercase intent keys (order_request, product_inquiry, other)."
            "\n\nExample:\n"
            "Input: 'I want to buy rice, how much is a bag?'\n"
            "Output: [{\"order_request\": \"I want to buy rice\"}, {\"product_inquiry\": \"how much is a bag?\"}]\n\n"
            "If there is only one intent, return a list with one object.\n\n"
            "Do not explain. Just output the JSON array."
        )

        human_prompt = f"Message: '{message}'"

        response = self.llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ])

        try:
            parsed = eval(response.content)  # safer than json.loads in this specific context
            if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
                return parsed
        except Exception:
            pass

        # fallback to single intent if something fails
        return [{"other": message}]


def get_intents_with_messages(message: str) -> list[dict]:
    return AgentIntentClassifier().classify(message)


if __name__ == "__main__":
    clf = AgentIntentClassifier()
    while True:
        msg = input("Enter a message (or 'quit' to exit): ")
        if msg.lower() == 'quit':
            break
        structured_intents = clf.classify(msg)
        print(f"Structured Intents: {structured_intents}")
