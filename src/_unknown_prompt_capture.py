"""Ephemeral: capture T2 LLM prompts/raw after discovery. Do not commit."""
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

llm_log: List[Dict[str, Any]] = []
_Orig = anthropic.Anthropic


class CapturingAnthropic(_Orig):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _create = self.messages.create

        def _c(**kwargs):
            raw = _create(**kwargs)
            content = []
            for block in raw.content:
                if block.type == "tool_use":
                    content.append(
                        {
                            "type": "tool_use",
                            "id": getattr(block, "id", None),
                            "name": block.name,
                            "input": block.input,
                        }
                    )
                elif block.type == "text":
                    content.append({"type": "text", "text": block.text})
                else:
                    content.append({"type": block.type, "repr": repr(block)})
            llm_log.append(
                {
                    "model": kwargs.get("model"),
                    "system": kwargs.get("system"),
                    "messages": copy.deepcopy(kwargs.get("messages")),
                    "tools": copy.deepcopy(kwargs.get("tools")),
                    "tool_choice": copy.deepcopy(kwargs.get("tool_choice")),
                    "raw_content": content,
                    "stop_reason": getattr(raw, "stop_reason", None),
                }
            )
            return raw

        self.messages.create = _c


anthropic.Anthropic = CapturingAnthropic  # type: ignore

# Force NLU modules to use capturing client after patch
import importlib

import nlu.stages.stage1.extractor as s1
import nlu.stages.stage2.groups.create as create_g
import nlu.stages.stage2.groups.faq as faq_g

importlib.reload(s1)
importlib.reload(faq_g)
importlib.reload(create_g)

import core.adapters.nlu.luma_client as luma_mod
from core.api.main import app
from core.session.session_manager import clear_session, get_session
from fastapi.testclient import TestClient
from nlu.pipeline import NLUPipeline

ORG = 2
USER = f"unknown-prompt-{uuid.uuid4().hex[:8]}"
pipeline = NLUPipeline()

# Route Core LumaClient to in-process NLU so Anthropic patch applies
_orig_resolve = luma_mod.LumaClient.resolve


def _resolve(self, *args, **kwargs):
    text = kwargs.get("text") or (args[1] if len(args) > 1 else "")
    tc = kwargs.get("tenant_context") or {}
    ctx = kwargs.get("conversation_context")
    schema = kwargs.get("entity_schema")
    now = kwargs.get("test_now")
    tz = kwargs.get("timezone") or "UTC"
    pr = pipeline.run(
        text,
        tc,
        now=now,
        timezone=tz,
        conversation_context=ctx,
        entity_schema=schema,
    )
    result: Dict[str, Any] = {"intent": pr.intent, "facts": pr.facts}
    if pr.time_constraint is not None:
        result["time_constraint"] = pr.time_constraint
    if pr.date_constraint is not None:
        result["date_constraint"] = pr.date_constraint
    if pr.search_query is not None:
        result["search_query"] = pr.search_query
    if pr.off_topic_query is not None:
        result["off_topic_query"] = pr.off_topic_query
    if pr.answerable is not None:
        result["answerable"] = pr.answerable
    if pr.answer is not None:
        result["answer"] = pr.answer
    if pr.service_candidates:
        result["service_candidates"] = pr.service_candidates
    if pr.operation is not None:
        result["operation"] = pr.operation
    if pr.declined_entities:
        result["declined_entities"] = list(pr.declined_entities)
    if pr.temporal is not None:
        result["temporal"] = pr.temporal
    if pr.understanding:
        result["turn"] = {"understanding": pr.understanding}
    return result


luma_mod.LumaClient.resolve = _resolve  # type: ignore

try:
    clear_session(ORG, USER)
except Exception:
    pass

client = TestClient(app)

# T1
llm_log.clear()
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
t1_calls = copy.deepcopy(llm_log)
session = get_session(ORG, USER)

# T2
llm_log.clear()
r2 = client.post(
    "/api/message",
    json={
        "user_id": USER,
        "organization_id": ORG,
        "text": "Executive Oil Change",
        "timezone": "Europe/London",
        "domain": "service",
    },
)
t2_calls = copy.deepcopy(llm_log)
body2 = r2.json()

out = {
    "t1_http": {
        "status": (r1.json().get("outcome") or {}).get("status"),
        "intent": (r1.json().get("outcome") or {}).get("intent_name"),
        "text_preview": (r1.json().get("text") or "")[:200],
    },
    "t1_llm_call_count": len(t1_calls),
    "session_before_t2_memory": (session or {}).get("conversation", {}).get("memory"),
    "t2_http": {
        "status": (body2.get("outcome") or {}).get("status"),
        "intent": (body2.get("outcome") or {}).get("intent_name"),
        "text_preview": (body2.get("text") or "")[:200],
    },
    "t2_llm_calls": t2_calls,
}

path = Path(__file__).resolve().parent.parent / "unknown_prompt_capture.json"
path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
print("calls_t2", len(t2_calls))
for i, c in enumerate(t2_calls, 1):
    print(f"--- call {i} ---")
    print("system_len", len(c.get("system") or ""))
    print("user", json.dumps(c.get("messages"), ensure_ascii=False)[:300])
    print("raw", json.dumps(c.get("raw_content"), ensure_ascii=False)[:500])
print("WROTE", path)
