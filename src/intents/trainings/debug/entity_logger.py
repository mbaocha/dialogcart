from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Text

from rasa.engine.graph import ExecutionContext, GraphComponent
from rasa.engine.recipes.default_recipe import DefaultV1Recipe
from rasa.engine.storage.resource import Resource
from rasa.engine.storage.storage import ModelStorage
from rasa.shared.nlu.training_data.message import Message
from rasa.shared.nlu.training_data.training_data import TrainingData


logger = logging.getLogger(__name__)


@DefaultV1Recipe.register(
    component_types=[DefaultV1Recipe.ComponentType.MESSAGE_FEATURIZER],
    is_trainable=False,
)
class SimpleEntityLogger(GraphComponent):
    """A simple no-op component that logs entities at its position in the pipeline.

    Configure multiple instances in the pipeline with different `label` values
    to observe entity state at different stages.
    """

    @staticmethod
    def get_default_config() -> Dict[Text, Any]:
        return {
            "label": "entity_logger",
            "log_level": "INFO",  # DEBUG|INFO|WARNING|ERROR
            "show_text": False,
            "show_intent": True,
            "show_entities": True,
            "max_entities": 25,  # guard for excessive output
        }

    def __init__(self, config: Dict[Text, Any]) -> None:
        self.config = config
        level_name = str(config.get("log_level", "INFO")).upper()
        self._level = getattr(logging, level_name, logging.INFO)
        self._label = str(config.get("label", "entity_logger"))

    @classmethod
    def create(
        cls,
        config: Dict[Text, Any],
        model_storage: ModelStorage,
        resource: Resource,
        execution_context: ExecutionContext,
    ) -> "SimpleEntityLogger":
        return cls(config)

    def process(self, messages: List[Message]) -> List[Message]:
        for index, msg in enumerate(messages):
            self._log_message(msg, batch_index=index)
        return messages

    def process_training_data(self, training_data: TrainingData) -> TrainingData:
        for index, ex in enumerate(training_data.training_examples):
            self._log_message(ex, batch_index=index, is_training=True)
        return training_data

    def _log_message(self, message: Message, batch_index: int, is_training: bool = False) -> None:
        try:
            payload: Dict[str, Any] = {"label": self._label, "batch_index": batch_index}

            if self.config.get("show_text", False):
                payload["text"] = message.get("text")

            if self.config.get("show_intent", True):
                intent_data = message.get("intent") or {}
                if isinstance(intent_data, dict):
                    payload["intent"] = {
                        "name": intent_data.get("name"),
                        "confidence": intent_data.get("confidence"),
                    }
                else:
                    payload["intent"] = str(intent_data)

            if self.config.get("show_entities", True):
                entities: List[Dict[str, Any]] = message.get("entities") or []
                max_entities = int(self.config.get("max_entities", 25))
                summarized: List[Dict[str, Any]] = []
                for ent in entities[:max_entities]:
                    if isinstance(ent, dict):
                        summarized.append(
                            {
                                "entity": ent.get("entity"),
                                "value": ent.get("value"),
                                "role": ent.get("role"),
                                "group": ent.get("group"),
                                "start": ent.get("start"),
                                "end": ent.get("end"),
                                "confidence": ent.get("confidence"),
                                "extractor": ent.get("extractor"),
                            }
                        )
                    else:
                        summarized.append({"raw": str(ent)})
                if len(entities) > max_entities:
                    summarized.append({"truncated": len(entities) - max_entities})
                payload["entities"] = summarized

            payload["phase"] = "training" if is_training else "runtime"
            logger.log(self._level, "ENTITY_LOG %s", json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            logger.debug("SimpleEntityLogger failed to log: %s", exc, exc_info=False)


