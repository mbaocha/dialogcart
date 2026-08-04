"""Ephemeral probe: real FastAPI /api/message path. Do not commit."""
from __future__ import annotations

import copy
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

# Load NLU .env for Anthropic if present
_env = Path(__file__).resolve().parent / "nlu" / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

import core.adapters.nlu.conversation_memory as conv_mem
import core.adapters.nlu.luma_client as luma_mod
import core.planning.pipeline.orchestrator as orch
import core.planning.pipeline.stage02_working_turn as s02
from core.adapters.nlu.conversation_memory import build_conversation_context
from core.api.main import app
from core.session.session_manager import clear_session, get_session
from fastapi.testclient import TestClient

ORG = 2
USER = f"http-turns-probe-{uuid.uuid4().hex[:8]}"
TZ = "Europe/London"

update_calls: List[Dict[str, Any]] = []
stage02_entered: List[bool] = []
nlu_payloads: List[Dict[str, Any]] = []
stage01_flags: List[Dict[str, Any]] = []

_orig_update = conv_mem.update_conversation


def _tracked_update(session, *, user_text, intent, search_query, assistant_text=None):
    out = _orig_update(
        session,
        user_text=user_text,
        intent=intent,
        search_query=search_query,
        assistant_text=assistant_text,
    )
    update_calls.append(
        {
            "user_text": user_text,
            "intent": intent,
            "search_query": search_query,
            "assistant_text": (assistant_text[:80] if isinstance(assistant_text, str) else assistant_text),
            "conversation_after": copy.deepcopy(out.get("conversation")),
            "caller_hint": "update_conversation",
        }
    )
    return out


conv_mem.update_conversation = _tracked_update  # type: ignore
# message.py imports the symbol by name at call time from module — patch module attr
import core.api.message as msg_mod

# stage02
_orig_build = s02.build_working_turn


def _build_wrap(**kwargs):
    stage02_entered[-1] = True
    return _orig_build(**kwargs)


s02.build_working_turn = _build_wrap  # type: ignore
orch.build_working_turn = _build_wrap  # type: ignore

# Stage01 non_durable via reconcile
import core.planning.pipeline.stage01_intent as s01

_orig_reconcile = s01.reconcile_intent


def _reconcile_wrap(**kwargs):
    decision, sess = _orig_reconcile(**kwargs)
    stage01_flags[-1] = {
        "planning_intent": decision.planning_intent,
        "non_durable_status": decision.non_durable_status,
        "handler_delegated": decision.handler_delegated,
        "handler_name": decision.handler_name,
        "delegated_search_query": decision.delegated_search_query,
    }
    return decision, sess


s01.reconcile_intent = _reconcile_wrap  # type: ignore
orch.reconcile_intent = _reconcile_wrap  # type: ignore

# Capture exact NLU conversation_context
_orig_resolve = luma_mod.LumaClient.resolve


def _resolve_wrap(self, *args, **kwargs):
    nlu_payloads.append(
        {
            "text": kwargs.get("text"),
            "conversation_context": copy.deepcopy(kwargs.get("conversation_context")),
        }
    )
    return _orig_resolve(self, *args, **kwargs)


luma_mod.LumaClient.resolve = _resolve_wrap  # type: ignore

# Also patch message.py's late import of update_conversation by patching the module
# it imports from at runtime: `from core.adapters.nlu.conversation_memory import update_conversation`
# That binds a local name inside the function each call — so module patch works.

try:
    clear_session(ORG, USER)
except Exception:
    pass

client = TestClient(app)
report: Dict[str, Any] = {"user_id": USER, "turns": []}

texts = ["What services do you offer?", "Executive Oil Change"]
for i, text in enumerate(texts, 1):
    update_calls.clear()
    stage02_entered.append(False)
    stage01_flags.append({})
    nlu_before = len(nlu_payloads)

    resp = client.post(
        "/api/message",
        json={
            "user_id": USER,
            "organization_id": ORG,
            "text": text,
            "timezone": TZ,
            "domain": "service",
        },
    )
    body = resp.json()
    session = get_session(ORG, USER) or {}
    conv = session.get("conversation") or {}
    memory = conv.get("memory") if isinstance(conv, dict) else None
    history = conv.get("history") if isinstance(conv, dict) else None
    # next-turn context as Core would build it now
    next_ctx = build_conversation_context(session)
    # NLU payload for THIS turn
    this_nlu = nlu_payloads[nlu_before:] if len(nlu_payloads) > nlu_before else []

    report["turns"].append(
        {
            "turn": i,
            "user_text": text,
            "http_status": resp.status_code,
            "response_text": body.get("text"),
            "outcome": {
                "status": (body.get("outcome") or {}).get("status"),
                "intent_name": (body.get("outcome") or {}).get("intent_name"),
                "active_handler": (body.get("outcome") or {}).get("active_handler"),
                "search_query": (body.get("outcome") or {}).get("search_query"),
            },
            "stage01": stage01_flags[-1],
            "stage02_entered": stage02_entered[-1],
            "update_conversation_calls": copy.deepcopy(update_calls),
            "conversation.memory": copy.deepcopy(memory),
            "conversation.history": copy.deepcopy(history),
            "session.messages_shim": copy.deepcopy(session.get("messages")),
            "persisted_session_conversation": copy.deepcopy(conv),
            "nlu_request_this_turn": this_nlu,
            "build_conversation_context_after_turn": copy.deepcopy(next_ctx),
        }
    )

out = Path(__file__).resolve().parent.parent / "http_turns_probe.json"
out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
print("WROTE", out)
