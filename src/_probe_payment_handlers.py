from pathlib import Path
import os

env = Path("nlu/.env")
if env.exists():
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from core.planning.pipeline.stage01_intent import reconcile_intent
from core.planning.policy.handler_router import resolve_handler
from nlu.pipeline import NLUPipeline
from nlu.registry.intent_groups import RAG_INTENTS, get_stage2_group

text = "I'm confused. Is the cost £105?"

luma = {
    "intent": {"name": "PAYMENT_STATUS", "confidence": 0.9},
    "facts": {
        "dates": [],
        "times": [],
        "date_time_pairs": [],
        "service_id": None,
        "booking_id": None,
    },
    "search_query": "cost 105",
    "turn": {"understanding": "UNDERSTOOD"},
}
d, _ = reconcile_intent(
    luma_response=luma,
    session_state=None,
    user_id="p1",
    organization_id=2,
    source_text=text,
)
print(
    "SCRIPTED",
    {
        "raw": d.raw_luma_intent,
        "planning": d.planning_intent,
        "resolve_handler": resolve_handler(d.planning_intent),
        "handler_delegated": d.handler_delegated,
        "handler_name": d.handler_name,
        "non_durable_status": d.non_durable_status,
        "delegated_search_query": d.delegated_search_query,
    },
)

pr = NLUPipeline().run(text, {"booking_mode": "service", "aliases": {}}, timezone="Europe/London")
name = (pr.intent or {}).get("name")
print(
    "NLU",
    {
        "intent": pr.intent,
        "search_query": pr.search_query,
        "stage2_group": get_stage2_group(name),
        "in_RAG_INTENTS": name in RAG_INTENTS,
        "facts": pr.facts,
    },
)
luma2 = {"intent": pr.intent, "facts": pr.facts, "search_query": pr.search_query}
if pr.understanding:
    luma2["turn"] = {"understanding": pr.understanding}
d2, _ = reconcile_intent(
    luma_response=luma2,
    session_state=None,
    user_id="p2",
    organization_id=2,
    source_text=text,
)
print(
    "LIVE_S01",
    {
        "raw": d2.raw_luma_intent,
        "planning": d2.planning_intent,
        "resolve_handler": resolve_handler(d2.planning_intent),
        "handler_delegated": d2.handler_delegated,
        "handler_name": d2.handler_name,
        "non_durable_status": d2.non_durable_status,
        "delegated_search_query": d2.delegated_search_query,
    },
)

for intent in ("DISCOVERY", "DETAILS", "GENERAL_INQUIRY", "PAYMENT", "PAYMENT_STATUS"):
    luma_i = {"intent": {"name": intent, "confidence": 0.9}, "facts": {}, "search_query": "q"}
    di, _ = reconcile_intent(
        luma_response=luma_i,
        session_state=None,
        user_id="cmp",
        organization_id=2,
        source_text="x",
    )
    print(
        "CMP",
        intent,
        di.handler_delegated,
        di.handler_name,
        di.non_durable_status,
        resolve_handler(intent),
    )
