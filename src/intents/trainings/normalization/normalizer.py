from __future__ import annotations

import io
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Text, Tuple

import yaml
from rasa.engine.graph import GraphComponent, ExecutionContext
from rasa.engine.recipes.default_recipe import DefaultV1Recipe
from rasa.engine.storage.resource import Resource
from rasa.engine.storage.storage import ModelStorage
from rasa.shared.nlu.training_data.message import Message
from rasa.shared.nlu.training_data.training_data import TrainingData

logger = logging.getLogger(__name__)

NUMERIC = r"(?P<num>\d+(?:[.,]\d+)?)"


@DefaultV1Recipe.register(
    component_types=[DefaultV1Recipe.ComponentType.MESSAGE_FEATURIZER],
    is_trainable=False,  # pre-processing only; no fit/train step
)
class SimpleTextNormalizer(GraphComponent):
    """Deterministic text normalizer for e-commerce assistants."""

    @staticmethod
    def get_default_config() -> Dict[Text, Any]:
        return {
            "lowercase": True,
            "config_file": None,
            "product_map": {},
            "unit_map": {},
            "synonym_map": {},
            "max_replacements_per_text": 50,
            "collapse_whitespace": True,
        }

    def __init__(self, config: Dict[Text, Any]) -> None:
        self.config = config
        maps = self._load_maps(
            config.get("config_file"),
            config.get("product_map") or {},
            config.get("unit_map") or {},
            config.get("synonym_map") or {},
        )
        self.product_map: Dict[str, List[str]] = maps["products"]
        self.unit_map: Dict[str, List[str]] = maps["units"]
        self.synonym_map: Dict[str, List[str]] = maps["synonyms"]

        self._replacements: List[Tuple[re.Pattern, str]] = self._compile_replacements(
            self.product_map, self.unit_map, self.synonym_map
        )
        self._unit_group_pattern: re.Pattern | None = self._build_unit_group_pattern(
            self.unit_map
        )

        logger.info(
            "✅ SimpleTextNormalizer initialized "
            "(products=%d, units=%d, synonyms=%d)",
            len(self.product_map),
            len(self.unit_map),
            len(self.synonym_map),
        )

    @classmethod
    def create(
        cls,
        config: Dict[Text, Any],
        model_storage: ModelStorage,
        resource: Resource,
        execution_context: ExecutionContext,
    ) -> "SimpleTextNormalizer":
        return cls(config)

    def process(self, messages: List[Message]) -> List[Message]:
        for m in messages:
            text = m.get("text") or ""
            if not text:
                continue
            normalized = self._normalize_text(text)
            if normalized != text:
                m.set("text", normalized)
        return messages

    def process_training_data(self, training_data: TrainingData) -> TrainingData:
        for ex in training_data.training_examples:
            text = ex.get("text") or ""
            if not text:
                continue
            normalized = self._normalize_text(text)
            if normalized != text:
                ex.set("text", normalized)
        return training_data

    # --- internal helpers ---

    def _load_maps(
        self,
        config_file: str | None,
        product_map_fallback: Dict[str, List[str]],
        unit_map_fallback: Dict[str, List[str]],
        synonym_map_fallback: Dict[str, List[str]],
    ) -> Dict[str, Dict[str, List[str]]]:
        products = product_map_fallback or {}
        units = unit_map_fallback or {}
        synonyms = synonym_map_fallback or {}

        if config_file:
            config_path = Path(config_file)
            if not config_path.is_absolute():
                # 👇 minimal fix: resolve relative to this file's directory
                base_dir = Path(__file__).resolve().parent
                config_path = (base_dir / config_file).resolve()

            if not config_path.exists():
                raise FileNotFoundError(
                    f"SimpleTextNormalizer: config_file not found: {config_path}"
                )

            with io.open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            products = data.get("products", products) or products
            units = data.get("units", units) or units
            synonyms = data.get("synonyms", synonyms) or synonyms
            logger.info("📄 Loaded normalization maps from %s", config_path)
        else:
            logger.info("📦 Using inline normalization maps (no config_file).")

        def _sanitize(d: Dict[Any, Any]) -> Dict[str, List[str]]:
            out: Dict[str, List[str]] = {}
            for k, v in d.items():
                key = str(k)
                if isinstance(v, list):
                    out[key] = [str(x) for x in v]
                elif v is None:
                    out[key] = []
                else:
                    out[key] = [str(v)]
            return out

        return {
            "products": _sanitize(products),
            "units": _sanitize(units),
            "synonyms": _sanitize(synonyms),
        }

    def _compile_replacements(
        self,
        product_map: Dict[str, List[str]],
        unit_map: Dict[str, List[str]],
        synonym_map: Dict[str, List[str]],
    ) -> List[Tuple[re.Pattern, str]]:
        reps: List[Tuple[re.Pattern, str]] = []

        def add_pairs(canonical: str, variants: List[str]) -> None:
            all_forms = set([canonical] + variants)
            for v in sorted(all_forms, key=len, reverse=True):
                pat = re.compile(rf"\b{re.escape(v)}\b", flags=re.IGNORECASE)
                reps.append((pat, canonical))

        for tgt, vars_ in product_map.items():
            add_pairs(tgt, vars_)
        for tgt, vars_ in unit_map.items():
            add_pairs(tgt, vars_)
        for tgt, vars_ in synonym_map.items():
            add_pairs(tgt, vars_)

        return reps

    def _build_unit_group_pattern(
        self, unit_map: Dict[str, List[str]]
    ) -> re.Pattern | None:
        variants: List[str] = []
        for canon, vs in unit_map.items():
            variants.append(re.escape(canon))
            variants.extend(re.escape(v) for v in vs)
        if not variants:
            return None
        alt = "|".join(sorted(set(variants), key=len, reverse=True))
        pattern = re.compile(NUMERIC + r"\s*(?P<unit>" + alt + r")\b", re.IGNORECASE)
        return pattern

    def _canon_unit(self, raw: str) -> str:
        raw_l = raw.lower()
        for canon, variants in self.unit_map.items():
            if raw_l == canon.lower() or raw_l in (v.lower() for v in variants):
                return canon
        return raw

    def _normalize_number_unit_glue(self, text: str) -> str:
        if not self._unit_group_pattern:
            return text

        def repl(m: re.Match) -> str:
            num = m.group("num").replace(",", ".")
            unit = m.group("unit")
            return f"{num} {self._canon_unit(unit)}"

        return self._unit_group_pattern.sub(repl, text)

    def _apply_replacements(self, text: str) -> str:
        replaced = 0
        for pat, tgt in self._replacements:
            text, n = pat.subn(tgt, text)
            if n:
                replaced += n
            if replaced >= int(self.config.get("max_replacements_per_text", 50)):
                break
        return text

    def _normalize_text(self, text: str) -> str:
        original = text

        if self.config.get("lowercase", True):
            text = text.lower()

        text = self._normalize_number_unit_glue(text)
        text = self._apply_replacements(text)

        if self.config.get("collapse_whitespace", True):
            text = re.sub(r"\s+", " ", text).strip()

        if text != original:
            logger.debug("🔄 Normalized: '%s' -> '%s'", original, text)
        return text
