"""
Intent mapping from actions to intents using semantic similarity.

Ported from semantics/intent_mapper.py with SentenceTransformer for
accurate action → intent mapping using ML embeddings.
"""
from typing import Tuple, Optional

# Lazy import for optional dependency
try:
    from sentence_transformers import SentenceTransformer, util
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    SentenceTransformer = None
    util = None


class IntentMapper:
    """
    Intent mapper using SentenceTransformer embeddings.
    
    Maps action text to canonical intents using semantic similarity.
    
    NOTE: Ported from semantics/intent_mapper.py exactly
    """
    
    def __init__(self):
        """
        Initialize intent mapper with SentenceTransformer model.
        
        NOTE: Matches semantics/intent_mapper.py lines 4-57 exactly
        """
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise ImportError(
                "sentence-transformers required for IntentMapper. "
                "Install with: pip install sentence-transformers"
            )
        
        # Load lightweight transformer for semantic similarity
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        
        # Canonical action examples (expanded for better coverage)
        # NOTE: Based on semantics/intent_mapper.py lines 10-51, expanded
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
                "give",
                "i want",
                "want",
                "take",
                "get me",
                "place",
                "purchase",
                "choose",
                "select",
                "pick",
                "i'll take",
                "i'll get",
                "add to cart",
                "put in cart",
                "throw it in",
                "include it",
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
                "take away",
                "eliminate",
                "discard",
                "exclude",
                "remove from cart",
                "take it out",
                "delete it",
                "get it out",
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
                "replace",
                "replace with",
                "switch",
                "change to",
                "update",
                "modify to",
                "set it to",
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
                "do you carry",
                "do you sell",
                "are there",
                "is there",
                "any",
                "got any",
                "have you got",
                "in stock",
                "available",
            ],
            "clear": [
                "clear",
                "clear cart",
                "empty",
                "empty cart",
                "remove all",
                "delete all",
                "clear everything",
                "start over",
                "reset",
                "reset cart",
            ],
            "view": [
                "view",
                "show",
                "get",
                "see",
                "display",
                "check",
                "view cart",
                "show cart",
                "what's in cart",
                "show me cart",
                "display cart",
                "check cart",
            ]
        }
        
        # Build embeddings
        # NOTE: Matches semantics/intent_mapper.py lines 54-57 exactly
        self.intent_embeddings = {
            intent: self.model.encode(examples, convert_to_tensor=True)
            for intent, examples in self.intent_examples.items()
        }
    
    def map_action_to_intent(self, action_text: str) -> Tuple[Optional[str], float]:
        """
        Map action text to canonical intent using semantic similarity.
        
        Given an extracted action phrase (e.g. "throw in", "is available"),
        return the best matching canonical intent ("add", "remove", "set", "check").
        
        Args:
            action_text: Action phrase extracted from sentence
            
        Returns:
            Tuple of (intent, confidence_score)
            intent: Best matching intent or None
            confidence: Cosine similarity score (0.0 to 1.0)
            
        NOTE: Matches semantics/intent_mapper.py lines 59-76 exactly
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

