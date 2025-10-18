"""
LLM Fallback for Entity Extraction

Provides LLM-based extraction for complex or ambiguous cases.
"""
from .fallback import (
    LLMExtractor,
    extract_with_llm,
)

__all__ = [
    "LLMExtractor",
    "extract_with_llm",
]

