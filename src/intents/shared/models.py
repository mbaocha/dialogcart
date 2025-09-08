from typing import List, Optional
from pydantic import BaseModel


class Entity(BaseModel):
    product: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    raw: Optional[str] = None


class IntentMeta(BaseModel):
    intent: str
    confidence: str
    confidence_score: Optional[float] = None
    entities: List[Entity] = []


class ClassifyResponse(BaseModel):
    source: str                 # "rasa" | "llm"
    intent_meta: IntentMeta


class MultiClassifyResponse(BaseModel):
    source: str                 # "rasa" | "llm"
    intents: List[IntentMeta]   # Multiple intents for LLM
    intent_meta: Optional[IntentMeta] = None  # Single intent for Rasa compatibility


