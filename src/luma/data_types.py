"""
Data structures for entity extraction pipeline.

This module defines the contracts between pipeline stages using dataclasses
for type safety, validation, and better IDE support.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class ProcessingStatus(Enum):
    """Pipeline processing status codes."""
    SUCCESS = "success"
    ERROR = "error"
    NEEDS_LLM = "needs_llm_fix"
    NO_ENTITIES = "no_entities_found"


@dataclass
class Entity:
    """
    Base entity with text and confidence score.
    
    Attributes:
        text: The entity text value
        confidence: Confidence score (0.0 to 1.0)
        position: Optional token position in sentence
    """
    text: str
    confidence: float = 1.0
    position: Optional[int] = None
    
    def __post_init__(self):
        """Validate entity attributes."""
        if not isinstance(self.text, str):
            raise TypeError(f"Entity text must be str, got {type(self.text)}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0 and 1, got {self.confidence}")


@dataclass
class NLPExtraction:
    """
    Entities extracted by NLP processor (Stage 1).
    
    This represents the output of nlp_processor.py after entity matching
    and parameterization.
    """
    # High confidence entities
    products: List[str] = field(default_factory=list)
    brands: List[str] = field(default_factory=list)
    units: List[str] = field(default_factory=list)
    quantities: List[str] = field(default_factory=list)
    variants: List[str] = field(default_factory=list)
    
    # Lower confidence entities
    likely_products: List[str] = field(default_factory=list)
    likely_brands: List[str] = field(default_factory=list)
    likely_variants: List[str] = field(default_factory=list)
    
    # Product-brand combinations
    productbrands: List[str] = field(default_factory=list)
    
    # Sentences
    original_sentence: str = ""
    parameterized_sentence: str = ""
    
    def __post_init__(self):
        """Validate extraction has required fields."""
        if not self.original_sentence:
            raise ValueError("original_sentence is required")
    
    def has_entities(self) -> bool:
        """Check if any entities were extracted."""
        return bool(
            self.products or self.brands or self.units or 
            self.quantities or self.variants or self.likely_products
        )


@dataclass
class NERPrediction:
    """
    NER model prediction output (Stage 2).
    
    This represents the output from the trained NER model after processing
    the parameterized sentence.
    """
    tokens: List[str]
    labels: List[str]
    scores: List[float]
    
    def __post_init__(self):
        """Validate that all lists have the same length."""
        if not (len(self.tokens) == len(self.labels) == len(self.scores)):
            raise ValueError(
                f"Length mismatch: {len(self.tokens)} tokens, "
                f"{len(self.labels)} labels, {len(self.scores)} scores"
            )
        if not self.tokens:
            raise ValueError("NER prediction cannot be empty")
    
    def get_entities_by_label(self, label_prefix: str) -> List[str]:
        """
        Extract all tokens with a specific label.
        
        Args:
            label_prefix: Label prefix to filter (e.g., "PRODUCT", "BRAND")
            
        Returns:
            List of token texts with matching labels
        """
        return [
            token for token, label in zip(self.tokens, self.labels)
            if label.replace("B-", "").replace("I-", "") == label_prefix
        ]


@dataclass
class EntityGroup:
    """
    A semantic group of related entities.
    
    Represents a single action with its associated entities (e.g., "add 2kg rice").
    This is the core output unit of the pipeline.
    """
    action: str
    intent: Optional[str] = None
    intent_confidence: Optional[float] = None
    
    # Entity lists
    products: List[str] = field(default_factory=list)
    brands: List[str] = field(default_factory=list)
    quantities: List[str] = field(default_factory=list)
    units: List[str] = field(default_factory=list)
    variants: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        """Validate group has required fields."""
        if not self.action:
            raise ValueError("EntityGroup must have an action")
        if self.intent_confidence is not None:
            if not 0.0 <= self.intent_confidence <= 1.0:
                raise ValueError(
                    f"intent_confidence must be between 0 and 1, got {self.intent_confidence}"
                )
    
    def is_valid(self) -> bool:
        """Check if group has meaningful content."""
        return bool(self.products or self.brands or self.action)
    
    def has_quantity(self) -> bool:
        """Check if group has quantity information."""
        return bool(self.quantities)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (for backward compatibility)."""
        return {
            "action": self.action,
            "intent": self.intent,
            "intent_confidence": self.intent_confidence,
            "products": self.products,
            "brands": self.brands,
            "quantities": self.quantities,
            "units": self.units,
            "variants": self.variants,
        }


@dataclass
class GroupingResult:
    """
    Result from entity grouping stage (Stage 3).
    
    Contains one or more entity groups plus status information.
    """
    groups: List[EntityGroup] = field(default_factory=list)
    status: str = "ok"  # "ok", "error", "needs_llm"
    reason: Optional[str] = None
    
    def is_successful(self) -> bool:
        """Check if grouping was successful."""
        return self.status == "ok" and len(self.groups) > 0
    
    def get_all_products(self) -> List[str]:
        """Get all products from all groups."""
        return [
            product
            for group in self.groups
            for product in group.products
        ]


@dataclass
class ExtractionResult:
    """
    Final pipeline output.
    
    This is the complete result returned by the entity extraction pipeline,
    containing all intermediate results and final grouped entities.
    """
    status: ProcessingStatus
    original_sentence: str
    parameterized_sentence: str
    
    # Main output
    groups: List[EntityGroup] = field(default_factory=list)
    
    # Intermediate results (optional, for debugging)
    nlp_extraction: Optional[NLPExtraction] = None
    ner_prediction: Optional[NERPrediction] = None
    grouping_result: Optional[GroupingResult] = None
    
    # Metadata
    notes: str = ""
    index_map: Dict[str, str] = field(default_factory=dict)
    debug_info: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Validate extraction result."""
        if not self.original_sentence:
            raise ValueError("original_sentence is required")
    
    def is_successful(self) -> bool:
        """Check if extraction was successful."""
        return self.status == ProcessingStatus.SUCCESS and len(self.groups) > 0
    
    def has_errors(self) -> bool:
        """Check if there were errors."""
        return self.status == ProcessingStatus.ERROR
    
    def needs_llm_processing(self) -> bool:
        """Check if result needs LLM post-processing."""
        return self.status == ProcessingStatus.NEEDS_LLM
    
    def get_all_products(self) -> List[str]:
        """Get all products from all groups."""
        return [
            product
            for group in self.groups
            for product in group.products
        ]
    
    def get_all_brands(self) -> List[str]:
        """Get all brands from all groups."""
        return [
            brand
            for group in self.groups
            for brand in group.brands
        ]
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary format (for backward compatibility).
        
        This allows gradual migration from dict-based to typed approach.
        """
        return {
            "status": self.status.value,
            "original_sentence": self.original_sentence,
            "parameterized_sentence": self.parameterized_sentence,
            "grouped_entities": {
                "status": self.grouping_result.status if self.grouping_result else "ok",
                "reason": self.grouping_result.reason if self.grouping_result else None,
                "groups": [group.to_dict() for group in self.groups]
            },
            "nlp_entities": self.nlp_extraction.__dict__ if self.nlp_extraction else None,
            "notes": self.notes,
            "index_map": self.index_map,
        }


# Type aliases for common types
EntityList = List[str]
TokenList = List[str]
LabelList = List[str]
ScoreList = List[float]

