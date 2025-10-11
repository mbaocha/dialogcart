# intent_mapper.py
from sentence_transformers import SentenceTransformer, util

class IntentMapper:
    def __init__(self):
        # Load lightweight transformer for semantic similarity
        self.model = SentenceTransformer("all-MiniLM-L6-v2")

        # Canonical action examples (just the action spans!)
        self.intent_examples = {
            "add": [
                "add",
                "insert",
                "put",
                "include",
                "throw in",
                "buy",
                "order",
                "give me",
            ],
            "remove": [
                "remove",
                "delete",
                "take out",
                "drop",
                "cancel",
                "subtract",
                "get rid of",
                "reduce by",
            ],
            "set": [
                "set",
                "make it",
                "update to",
                "change",
                "adjust",
                "switch to",
                "modify",
                "update order",
            ],
            "check": [
                "do you have",
                "are available in stock",
                "is available",
                "any available",
                "check availability",
                "do you stock",
                "can i get",
                "is in stock",
            ]
        }

        # Build embeddings
        self.intent_embeddings = {
            intent: self.model.encode(examples, convert_to_tensor=True)
            for intent, examples in self.intent_examples.items()
        }

    def map_action_to_intent(self, action_text: str):
        """
        Given an extracted action phrase (e.g. "throw in", "is available"),
        return the best matching canonical intent ("add", "remove", "set", "check").
        """
        if not action_text:
            return None, 0.0

        action_embedding = self.model.encode(action_text, convert_to_tensor=True)
        best_intent, best_score = None, -1

        for intent, embeddings in self.intent_embeddings.items():
            cosine_scores = util.cos_sim(action_embedding, embeddings)
            score = cosine_scores.max().item()
            if score > best_score:
                best_intent, best_score = intent, score

        return best_intent, best_score


if __name__ == "__main__":
    mapper = IntentMapper()

    test_actions = [
        "throw in",
        "get rid of",
        "switch to",
        "do you stock",
        "is available",
        "order",
    ]

    for act in test_actions:
        intent, score = mapper.map_action_to_intent(act)
        print(f"Action: {act:15s} → Intent: {intent:6s} (score={score:.3f})")
