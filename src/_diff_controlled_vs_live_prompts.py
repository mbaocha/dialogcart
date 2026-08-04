"""Ephemeral: diff controlled vs live prompts for 'The first one'. Do not commit."""
from __future__ import annotations

import copy
import json
import os
import sys
import uuid
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
                    tools.append({"name": block.name, "input": copy.deepcopy(block.input)})
                elif getattr(block, "type", None) == "text":
                    tools.append({"name": "__text__", "input": {"text": block.text}})
            _llm.append(
                {
                    "system": kwargs.get("system"),
                    "messages": copy.deepcopy(kwargs.get("messages")),
                    "raw": tools,
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

import core.adapters.nlu.luma_client as luma_mod
from core.adapters.nlu.conversation_memory import build_conversation_context
from core.api.main import app
from core.session.session_manager import clear_session, get_session
from fastapi.testclient import TestClient
from nlu.pipeline import NLUPipeline
from nlu.stages.shared.context import format_conversation_context

pipeline = NLUPipeline()
_last_ctx = None


def _resolve(self, *args, **kwargs):
    global _last_ctx
    _llm.clear()
    text = kwargs.get("text") or (args[1] if len(args) > 1 else "")
    tc = kwargs.get("tenant_context") or {}
    ctx = kwargs.get("conversation_context")
    _last_ctx = copy.deepcopy(ctx)
    schema = kwargs.get("entity_schema")
    pr = pipeline.run(
        text,
        tc,
        now=kwargs.get("test_now"),
        timezone=kwargs.get("timezone") or "UTC",
        conversation_context=ctx,
        entity_schema=schema,
    )
    result: Dict[str, Any] = {"intent": pr.intent, "facts": pr.facts}
    if pr.search_query is not None:
        result["search_query"] = pr.search_query
    if pr.service_candidates:
        result["service_candidates"] = pr.service_candidates
    if pr.understanding:
        result["turn"] = {"understanding": pr.understanding}
    return result


luma_mod.LumaClient.resolve = _resolve  # type: ignore

CONTROLLED_CTX = {
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

# --- A. Controlled ---
_llm.clear()
pr_a = pipeline.run(
    "The first one",
    TENANT,
    now="2026-08-03T12:00:00",
    timezone="UTC",
    conversation_context=CONTROLLED_CTX,
    entity_schema=ENTITY_SCHEMA,
)
llm_a = copy.deepcopy(_llm)
s1_a = next(c for c in llm_a if any(t["name"] == "classify_intent" for t in c["raw"]))
s2_a = [c for c in llm_a if c is not s1_a]

# --- B. Live ---
client = TestClient(app)
ORG = 2
USER = f"diff-ref-{uuid.uuid4().hex[:8]}"
clear_session(ORG, USER)

# T1 discovery
_llm.clear()
r1 = client.post(
    "/api/message",
    json={
        "user_id": USER,
        "organization_id": ORG,
        "text": "What services do you offer?",
        "timezone": "Europe/London",
        "domain": "service",
    },
)
session_after_t1 = get_session(ORG, USER) or {}
built_ctx = build_conversation_context(session_after_t1)
t1_reply = (r1.json().get("text") or (r1.json().get("outcome") or {}).get("text") or "")

# T2 the first one
_llm.clear()
_last_ctx = None
r2 = client.post(
    "/api/message",
    json={
        "user_id": USER,
        "organization_id": ORG,
        "text": "The first one",
        "timezone": "Europe/London",
        "domain": "service",
    },
)
llm_b = copy.deepcopy(_llm)
ctx_b = copy.deepcopy(_last_ctx)
s1_b = next(
    (c for c in llm_b if any(t["name"] == "classify_intent" for t in c["raw"])),
    None,
)
s2_b = [c for c in llm_b if c is not s1_b] if s1_b else list(llm_b)
body2 = r2.json()
outcome2 = body2.get("outcome") or {}

def _tool_in(call):
    if not call:
        return None
    for t in call["raw"]:
        if t["name"] != "__text__":
            return t
    return None

out = {
    "A_controlled": {
        "conversation_context": CONTROLLED_CTX,
        "formatted_context": format_conversation_context(CONTROLLED_CTX),
        "stage1_system": s1_a["system"],
        "stage1_messages": s1_a["messages"],
        "stage1_raw": _tool_in(s1_a),
        "stage2_systems": [c["system"] for c in s2_a],
        "stage2_messages": [c["messages"] for c in s2_a],
        "stage2_raw": [_tool_in(c) for c in s2_a],
        "final_intent": (pr_a.intent or {}).get("name"),
        "service_id": (pr_a.facts or {}).get("service_id"),
    },
    "B_live": {
        "t1_reply": t1_reply,
        "session_conversation": session_after_t1.get("conversation"),
        "build_conversation_context": built_ctx,
        "ctx_sent_to_nlu_t2": ctx_b,
        "formatted_context": format_conversation_context(ctx_b or {}),
        "stage1_system": (s1_b or {}).get("system"),
        "stage1_messages": (s1_b or {}).get("messages"),
        "stage1_raw": _tool_in(s1_b) if s1_b else None,
        "stage2_systems": [c["system"] for c in s2_b],
        "stage2_messages": [c["messages"] for c in s2_b],
        "stage2_raw": [_tool_in(c) for c in s2_b],
        "outcome": {
            "status": outcome2.get("status"),
            "intent": outcome2.get("intent_name"),
            "handler": outcome2.get("active_handler"),
            "text": (body2.get("text") or outcome2.get("text") or "")[:300],
        },
    },
}

path = Path(__file__).resolve().parent.parent / "controlled_vs_live_prompt_diff.json"
path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

# Print key diffs
print("=== T1 LIVE REPLY ===")
print(repr(t1_reply[:400]))
print("\n=== A FORMATTED CONTEXT ===")
print(out["A_controlled"]["formatted_context"])
print("\n=== B FORMATTED CONTEXT ===")
print(out["B_live"]["formatted_context"])
print("\n=== B CTX SENT ===")
print(json.dumps(ctx_b, indent=2, ensure_ascii=False)[:2000])
print("\n=== A STAGE1 RESULT ===", out["A_controlled"]["stage1_raw"], out["A_controlled"]["final_intent"], out["A_controlled"]["service_id"])
print("=== B STAGE1 RESULT ===", out["B_live"]["stage1_raw"])
print("=== B STAGE2 RESULT ===", out["B_live"]["stage2_raw"])
print("=== B OUTCOME ===", out["B_live"]["outcome"])
print("WROTE", path)
