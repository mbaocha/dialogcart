"""Ephemeral verification of conversational-answer fix. Do not commit."""
from __future__ import annotations

import copy
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

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

# Capture Stage1/Stage2 tool outputs from NLU LLM calls
_nlu_stage_log: List[Dict[str, Any]] = []
_Orig = anthropic.Anthropic


class CapturingAnthropic(_Orig):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _create = self.messages.create

        def _c(**kwargs):
            raw = _create(**kwargs)
            for block in raw.content:
                if getattr(block, "type", None) == "tool_use":
                    _nlu_stage_log.append(
                        {
                            "tool": block.name,
                            "input": copy.deepcopy(block.input),
                        }
                    )
            return raw

        self.messages.create = _c


anthropic.Anthropic = CapturingAnthropic  # type: ignore

import importlib
import nlu.stages.stage1.extractor as s1
import nlu.stages.stage2.groups.create as create_g
import nlu.stages.stage2.groups.faq as faq_g
import nlu.stages.stage2.groups.availability as avail_g
import nlu.stages.stage2.groups.modify as modify_g
import nlu.stages.stage2.groups.cancel as cancel_g
import nlu.stages.stage2.groups.view as view_g
import nlu.stages.stage2.dispatcher as disp

for mod in (s1, create_g, faq_g, avail_g, modify_g, cancel_g, view_g, disp):
    importlib.reload(mod)
disp._instances.clear()

import core.adapters.nlu.luma_client as luma_mod
from core.api.main import app
from core.session.session_manager import clear_session, get_session
from fastapi.testclient import TestClient
from nlu.pipeline import NLUPipeline

pipeline = NLUPipeline()
_last_nlu: Dict[str, Any] = {}


def _resolve(self, *args, **kwargs):
    global _last_nlu
    _nlu_stage_log.clear()
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
    for key, val in (
        ("time_constraint", pr.time_constraint),
        ("date_constraint", pr.date_constraint),
        ("search_query", pr.search_query),
        ("off_topic_query", pr.off_topic_query),
        ("answerable", pr.answerable),
        ("answer", pr.answer),
        ("operation", pr.operation),
        ("temporal", pr.temporal),
    ):
        if val is not None:
            result[key] = val
    if pr.service_candidates:
        result["service_candidates"] = pr.service_candidates
    if pr.declined_entities:
        result["declined_entities"] = list(pr.declined_entities)
    if pr.understanding:
        result["turn"] = {"understanding": pr.understanding}

    stage1 = None
    stage2 = None
    for entry in _nlu_stage_log:
        if entry["tool"] == "classify_intent":
            stage1 = entry["input"]
        else:
            stage2 = entry["input"]
    _last_nlu = {
        "result": copy.deepcopy(result),
        "stage1": stage1,
        "stage2": stage2,
        "conversation_context": copy.deepcopy(ctx),
    }
    return result


luma_mod.LumaClient.resolve = _resolve  # type: ignore

client = TestClient(app)
ORG_CAR = 2
ORG_SALON = 1  # typical salon — verify mapping


def _org_for_domain(domain: str) -> int:
    # chat.py maps: salon often 1, car_service 2 — confirm via chat module
    try:
        import chat as chat_mod

        # reverse: find org with domain
        for oid in (1, 2, 3, 4, 5):
            try:
                d = chat_mod.resolve_booking_domain_for_org(oid)
                if d == domain:
                    return oid
            except Exception:
                continue
    except Exception:
        pass
    return 2 if domain == "service" and False else (1 if domain == "service" else 2)


def _pick_orgs():
    import chat as chat_mod

    salon = car = None
    for oid in range(1, 20):
        try:
            d = chat_mod.resolve_booking_domain_for_org(oid)
        except Exception:
            continue
        # car service uses organization 2 historically
        name = None
        try:
            from core.adapters.clients.organization_client import OrganizationClient

            det = OrganizationClient().get_details(oid)
            org = (det or {}).get("organization") or det or {}
            name = (org.get("name") or org.get("slug") or "").lower()
        except Exception:
            name = ""
        if "car" in name or oid == 2:
            car = oid
        if "salon" in name or "hair" in name or oid == 1:
            salon = oid
    return salon or 1, car or 2


SALON_ORG, CAR_ORG = _pick_orgs()


def run_conversation(org: int, turns: List[str], label: str) -> Dict[str, Any]:
    user = f"verify-{label}-{uuid.uuid4().hex[:8]}"
    try:
        clear_session(org, user)
    except Exception:
        pass
    turn_reports = []
    for i, text in enumerate(turns, 1):
        _last_nlu.clear()
        resp = client.post(
            "/api/message",
            json={
                "user_id": user,
                "organization_id": org,
                "text": text,
                "timezone": "Europe/London",
                "domain": "service",
            },
        )
        body = resp.json()
        outcome = body.get("outcome") or {}
        session = get_session(org, user) or {}
        nlu = copy.deepcopy(_last_nlu)
        nlu_res = nlu.get("result") or {}
        facts = nlu_res.get("facts") or {}
        turn_reports.append(
            {
                "user_text": text,
                "http_status": resp.status_code,
                "stage1": nlu.get("stage1"),
                "stage2": nlu.get("stage2"),
                "nlu_final_intent": (nlu_res.get("intent") or {}).get("name"),
                "nlu_confidence": (nlu_res.get("intent") or {}).get("confidence"),
                "facts": facts,
                "service_id": facts.get("service_id"),
                "service_candidates": nlu_res.get("service_candidates"),
                "search_query": nlu_res.get("search_query"),
                "off_topic_query": nlu_res.get("off_topic_query"),
                "handler": outcome.get("active_handler"),
                "outcome_status": outcome.get("status"),
                "outcome_action": outcome.get("action"),
                "planning_intent": outcome.get("intent_name"),
                "outcome_slots": outcome.get("slots"),
                "outcome_missing": outcome.get("missing_slots"),
                "ask_next": outcome.get("ask_next"),
                "session_intent": session.get("intent_name"),
                "session_slots": session.get("slots"),
                "session_missing": session.get("missing_slots"),
                "session_status": session.get("status"),
                "reply": (body.get("text") or outcome.get("text") or "")[:500],
                "ctx_sent": {
                    "last_intent": (nlu.get("conversation_context") or {}).get(
                        "last_intent"
                    ),
                    "has_turns": bool(
                        (nlu.get("conversation_context") or {}).get("turns")
                    ),
                    "has_assistant_in_turns": any(
                        isinstance(t, dict) and t.get("assistant")
                        for t in (
                            (nlu.get("conversation_context") or {}).get("turns") or []
                        )
                    ),
                },
            }
        )
    return {"label": label, "org": org, "user": user, "turns": turn_reports}


results = {}

# Scenario 1
results["s1"] = run_conversation(
    CAR_ORG,
    ["What services do you offer?", "Executive Oil Change"],
    "s1-bug",
)

# Scenario 2
results["s2"] = run_conversation(
    CAR_ORG,
    ["What services do you offer?", "Tell me more about Executive Oil Change"],
    "s2-details",
)

# Scenario 3
results["s3"] = run_conversation(
    CAR_ORG,
    ["What services do you offer?", "The first one"],
    "s3-ref",
)

# Scenario 4 salon
results["s4"] = run_conversation(
    SALON_ORG,
    ["Book haircut", "Premium", "Tomorrow", "10am"],
    "s4-salon",
)

# Scenario 5 car book
results["s5"] = run_conversation(
    CAR_ORG,
    ["Book me for Executive Oil Change", "Petrol"],
    "s5-car",
)

# Scenario 6 off-topic mid booking
results["s6"] = run_conversation(
    SALON_ORG,
    ["Book haircut", "Who is the president of Nigeria?", "Premium"],
    "s6-offtopic",
)

out = Path(__file__).resolve().parent.parent / "verify_conversational_answer.json"
out.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
print("SALON_ORG", SALON_ORG, "CAR_ORG", CAR_ORG)
print("WROTE", out)
# compact print
for key, data in results.items():
    print("=" * 60, key)
    for t in data["turns"]:
        print(
            f"  U={t['user_text']!r}\n"
            f"    s1={t['stage1']} s2_intent={(t['stage2'] or {}).get('validated_intent') if t['stage2'] else None}\n"
            f"    nlu={t['nlu_final_intent']} sid={t['service_id']} sq={t['search_query']}\n"
            f"    plan={t['planning_intent']} status={t['outcome_status']} action={t['outcome_action']} handler={t['handler']}\n"
            f"    sess={t['session_intent']} slots={t['session_slots']} missing={t['session_missing']}\n"
            f"    reply={t['reply'][:120]!r}"
        )
