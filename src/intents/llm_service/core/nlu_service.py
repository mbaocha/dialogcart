"""
NLU Service - extracted from llm.py
"""
from typing import List, Dict
from langchain_openai import ChatOpenAI
from .models import MultiIntentResult, FollowUpSlots
from .prompts import system_prompt, followup_prompt

class NLUService:
    def __init__(self, model: str = "gpt-4o"):
        self.model = model

    def classify(self, history: List[Dict[str, str]]) -> MultiIntentResult:
        """History-aware multi-intent + entities extraction (LLM-only)."""
        llm = ChatOpenAI(model=self.model, temperature=0).with_structured_output(MultiIntentResult)
        messages = [{"role": "system", "content": system_prompt()}] + history
        return llm.invoke(messages)

    def extract_followup(self, latest_user_msg: str, product: str, missing: List[str]) -> FollowUpSlots:
        """Narrow extractor for the latest message only—no history used."""
        llm = ChatOpenAI(model=self.model, temperature=0).with_structured_output(FollowUpSlots)
        messages = [
            {"role": "system", "content": followup_prompt(product, missing)},
            {"role": "user", "content": latest_user_msg}
        ]
        return llm.invoke(messages)
