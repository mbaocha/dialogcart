from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from rasa.engine.graph import GraphComponent, ExecutionContext
from rasa.engine.recipes.default_recipe import DefaultV1Recipe
from rasa.engine.storage.resource import Resource
from rasa.engine.storage.storage import ModelStorage
from rasa.shared.nlu.training_data.message import Message

logger = logging.getLogger(__name__)


@DefaultV1Recipe.register(
    component_types=[DefaultV1Recipe.ComponentType.ENTITY_EXTRACTOR],
    is_trainable=False,
)
class UpdateOpInferrer(GraphComponent):
    """Infers update_op (set|increase|decrease) from text after DIET.

    - Only adds update_op for intent UPDATE_CART_QUANTITY
    - Heuristics based on common verbs and patterns
    - Injects an entity: {entity: "update_op", value: "set|increase|decrease"}
    """

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        return {
            "enabled": True,
            # customize keyword lists if needed
            "increase_keywords": ["increase", "raise", "bump up", "bump"],
            "decrease_keywords": ["decrease", "reduce", "cut down", "cut"],
            "set_keywords": ["set", "make", "change"],
        }

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self._inc_re = self._compile_keywords(config.get("increase_keywords", []))
        self._dec_re = self._compile_keywords(config.get("decrease_keywords", []))
        self._set_re = self._compile_keywords(config.get("set_keywords", []))
        logger.info("UpdateOpInferrer initialized (enabled=%s)", bool(config.get("enabled", True)))

    @classmethod
    def create(
        cls,
        config: Dict[str, Any],
        model_storage: ModelStorage,
        resource: Resource,
        execution_context: ExecutionContext,
    ) -> "UpdateOpInferrer":
        return cls(config)

    def process(self, messages: List[Message]) -> List[Message]:
        if not self.config.get("enabled", True):
            return messages

        for m in messages:
            intent = m.get("intent")
            intent_name: Optional[str] = None
            if isinstance(intent, dict):
                intent_name = intent.get("name")
            elif isinstance(intent, str):
                intent_name = intent

            # Only infer for UPDATE_CART_QUANTITY
            if intent_name != "UPDATE_CART_QUANTITY":
                continue

            text = (m.get("text") or "").strip()
            if not text:
                continue

            op, span = self._infer_update_op(text)
            if not op:
                continue

            # Append/update entities
            entities = list(m.get("entities") or [])
            entities.append(
                {
                    "entity": "update_op",
                    "value": op,
                    "start": span[0] if span else None,
                    "end": span[1] if span else None,
                    "confidence_entity": 1.0,
                    "extractor": "UpdateOpInferrer",
                }
            )
            m.set("entities", entities)
        return messages

    # -------------------
    # Internal helpers
    # -------------------
    @staticmethod
    def _compile_keywords(words: List[str]) -> Optional[re.Pattern]:
        words = [w for w in words if w]
        if not words:
            return None
        # longest first; allow whitespace within multi-word phrases
        escaped = [re.escape(w) for w in sorted(set(words), key=len, reverse=True)]
        pattern = r"|".join(escaped)
        return re.compile(rf"\b(?:{pattern})\b", flags=re.IGNORECASE)

    def _infer_update_op(self, text: str) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
        # Prioritize explicit verbs first
        if self._inc_re:
            m = self._inc_re.search(text)
            if m:
                return "increase", (m.start(), m.end())
        if self._dec_re:
            m = self._dec_re.search(text)
            if m:
                return "decrease", (m.start(), m.end())
        if self._set_re:
            m = self._set_re.search(text)
            if m:
                return "set", (m.start(), m.end())

        # Heuristic: presence of " by <num>" favors increase/decrease if hints exist
        if re.search(r"\bby\b\s*\d", text, flags=re.IGNORECASE):
            # if no explicit verb, default to increase for "by" patterns
            return "increase", None

        # Heuristic: presence of " to <num>" suggests set
        if re.search(r"\bto\b\s*\d", text, flags=re.IGNORECASE):
            return "set", None

        return None, None


