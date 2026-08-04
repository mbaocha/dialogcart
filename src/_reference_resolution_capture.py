"""Ephemeral: reference-resolution evidence capture. Do not commit."""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

_env = Path(__file__).resolve().parent / "nlu" / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

import anthropic

_llm: List[Dict[str, Any]] = []
_Orig = anthropic.Anthropic


class CapturingAnthropic(_Orig):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _create = self.messages.create

        def _c(**kwargs):
            raw = _create(**kwargs)
            tools = []
            for block in raw.content:
                if getattr(block, "type", None) == "tool_use":
                    tools.append(
                        {"name": block.name, "input": copy.deepcopy(block.input)}
                    )
                elif getattr(block, "type", None) == "text":
                    tools.append({"name": "__text__", "input": {"text": block.text}})
            _llm.append(
                {
                    "system": kwargs.get("system"),
                    "messages": copy.deepcopy(kwargs.get("messages")),
                    "raw_tools": tools,
                }
            )
            return raw

        self.messages.create = _c


anthropic.Anthropic = CapturingAnthropic  # type: ignore

import importlib
import nlu.stages.stage1.extractor as s1
import nlu.stages.stage2.groups.create as create_g
import nlu.stages.stage2.groups.faq as faq_g
import nlu.stages.stage2.dispatcher as disp

for mod in (s1, create_g, faq_g, disp):
    importlib.reload(mod)
disp._instances.clear()

from nlu.pipeline import NLUPipeline
from nlu.stages.shared.context import format_conversation_context

CTX = {
    "last_intent": "DISCOVERY",
    "last_search_query": "available services",
    "turns": [
        {
            "user": "What services do you offer?",
            "assistant": (
                "Which service would you like?\n\n"
                "**Executive Oil Change**\n"
                "**Premium Full Service**\n"
                "**Brake Pad Change**"
            ),
            "intent": "DISCOVERY",
            "search_query": "available services",
        }
    ],
}

TENANT = {
    "booking_mode": "service",
    "aliases": {
        "executive oil change": "26",
        "premium full service": "27",
        "brake pad change": "28",
    },
}

ENTITY_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "description": "Vehicle service requested.",
            "catalog": {
                "Executive Oil Change": 26,
                "Premium Full Service": 27,
                "Brake Pad change": 28,
            },
            "role": "bookable_item",
            "required": True,
        }
    ],
}

REFERENCES = [
    "The first one",
    "That one",
    "The cheaper one",
    "The premium one",
    "The last one",
]

pipeline = NLUPipeline()
rows = []
detail = {}

for ref in REFERENCES:
    _llm.clear()
    pr = pipeline.run(
        ref,
        TENANT,
        now="2026-08-03T12:00:00",
        timezone="UTC",
        conversation_context=CTX,
        entity_schema=ENTITY_SCHEMA,
    )
    stage1_call = next(
        (c for c in _llm if any(t.get("name") == "classify_intent" for t in c["raw_tools"])),
        None,
    )
    # First non-classify tool call = stage2 primary
    stage2_calls = [
        c
        for c in _llm
        if not any(t.get("name") == "classify_intent" for t in c["raw_tools"])
    ]
    s1_in = None
    if stage1_call:
        for t in stage1_call["raw_tools"]:
            if t["name"] == "classify_intent":
                s1_in = t["input"]
    s2_ins = []
    for c in stage2_calls:
        for t in c["raw_tools"]:
            if t["name"] != "__text__":
                s2_ins.append({"tool": t["name"], "input": t["input"]})

    ctx_block = format_conversation_context(CTX)
    facts = pr.facts or {}
    row = {
        "reference": ref,
        "stage1_intent": (s1_in or {}).get("intent"),
        "stage1_confidence": (s1_in or {}).get("confidence"),
        "stage2_tools": s2_ins,
        "final_intent": (pr.intent or {}).get("name"),
        "final_confidence": (pr.intent or {}).get("confidence"),
        "service_id": facts.get("service_id"),
        "search_query": pr.search_query,
        "service_candidates": pr.service_candidates,
        "understanding": pr.understanding,
    }
    rows.append(row)
    detail[ref] = {
        "formatted_context": ctx_block,
        "stage1_system": (stage1_call or {}).get("system"),
        "stage1_messages": (stage1_call or {}).get("messages"),
        "stage1_raw": s1_in,
        "stage2_systems": [c.get("system") for c in stage2_calls],
        "stage2_messages": [c.get("messages") for c in stage2_calls],
        "stage2_raw": s2_ins,
        "pipeline_result": {
            "intent": pr.intent,
            "facts": pr.facts,
            "search_query": pr.search_query,
            "service_candidates": pr.service_candidates,
        },
    }
    print(
        f"{ref!r}: s1={row['stage1_intent']}@{row['stage1_confidence']} "
        f"final={row['final_intent']} sid={row['service_id']} sq={row['search_query']} "
        f"s2={json.dumps(s2_ins, ensure_ascii=False)[:200]}"
    )

out = Path(__file__).resolve().parent.parent / "reference_resolution_capture.json"
out.write_text(
    json.dumps({"rows": rows, "detail": detail, "context_block": format_conversation_context(CTX)}, indent=2, ensure_ascii=False),
    encoding="utf-8",
)
print("WROTE", out)
print("--- CONTEXT BLOCK ---")
print(format_conversation_context(CTX))
