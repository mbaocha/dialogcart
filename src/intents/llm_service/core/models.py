"""
Data models for LLM service - extracted from llm.py
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field

class Entity(BaseModel):
    product: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    raw: Optional[str] = None

class IntentAction(BaseModel):
    intent: Literal[
        "SHOW_PRODUCT_LIST", "VIEW_CART", "ADD_TO_CART", "REMOVE_FROM_CART",
        "CLEAR_CART", "CHECK_PRODUCT_EXISTENCE", "RESTORE_CART",
        "UPDATE_CART_QUANTITY", "NONE"
    ]
    confidence: Literal["high", "medium", "low"]
    reasoning: Optional[str] = None
    entities: List[Entity] = Field(default_factory=list)

class MultiIntentResult(BaseModel):
    intents: List[IntentAction]

# Follow-up narrow extraction (latest message only)
class FollowUpSlots(BaseModel):
    product: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
