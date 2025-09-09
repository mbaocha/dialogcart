from typing import List, Optional
from pydantic import BaseModel


class Entity(BaseModel):
    product: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    raw: Optional[str] = None


class Action(BaseModel):
    action: str  # "add", "remove", "increase", "decrease", "set", "check", "replace"
    product: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    container: Optional[str] = None
    confidence: str = "medium"
    confidence_score: Optional[float] = None
    
    class Config:
        # Include None values in model_dump, but always exclude container
        exclude_none = False
    
    def dict(self, **kwargs):
        """Override dict() to always exclude container field"""
        data = super().dict(**kwargs)
        data.pop("container", None)  # Always remove container field
        return data


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