"""
Data models for LLM service - extracted from llm.py
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field

class Entity(BaseModel):
    product: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    verb: Optional[str] = None
    raw: Optional[str] = None

class IntentAction(BaseModel):
    intent: Literal[
        "SHOW_PRODUCT_LIST", "VIEW_CART", "CLEAR_CART", 
        "CHECK_PRODUCT_EXISTENCE", "RESTORE_CART", "MODIFY_CART", "NONE"
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

# Models used by ConversationManager (response/meta containers)
class IntentMeta(BaseModel):
    intent: str
    confidence: str
    confidence_score: Optional[float] = None
    entities: List[Entity] = Field(default_factory=list)

class ClassifyResponse(BaseModel):
    source: str = "llm"
    intent: Optional[IntentMeta] = None

class MultiClassifyResponse(BaseModel):
    source: str = "llm"
    intents: List[IntentMeta] = Field(default_factory=list)
