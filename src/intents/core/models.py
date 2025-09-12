"""
Core data models for intent classification and validation
"""
from typing import List, Optional, Literal
from pydantic import BaseModel


class Entity(BaseModel):
    product: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    raw: Optional[str] = None


class Action(BaseModel):
    action: str  # "add", "remove", "set", "check" (increase→add, decrease→remove, replace→set with product_from/product_to)
    product: Optional[str] = None
    product_from: Optional[str] = None  # For set operations with 2 products: what to replace
    product_to: Optional[str] = None    # For set operations with 2 products: what to replace with
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


# Validator models
class ValidatedAction(BaseModel):
    action: Literal["add", "remove", "set", "check", "unknown"]
    product: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None


class ValidationResult(BaseModel):
    actions: List[ValidatedAction] = []


