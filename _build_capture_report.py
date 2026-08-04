import json
from pathlib import Path

data = json.loads(Path("capture_t1_t2.json").read_text(encoding="utf-8"))
lines = []


def p(s: str = "") -> None:
    lines.append(s)


for art in data:
    t = art["turn"]
    p("=" * 80)
    p(f"## T{t}")
    p(f"User: {art['user_text']}")
    p()
    p("### Chat → Core HTTP request")
    p(json.dumps(art["chat_to_core_http_request"], indent=2, ensure_ascii=False))
    p()
    p("### Session state before turn")
    p(json.dumps(art["session_before"], indent=2, ensure_ascii=False))
    p()
    p("### Luma JSON request from Core (exact body)")
    p(json.dumps(art["luma_json_request_from_core"], indent=2, ensure_ascii=False))
    p()
    p("### Luma LLM calls (system prompt + user messages) — exact")
    for i, call in enumerate(art.get("luma_llm_calls") or [], 1):
        p(f"--- LLM call {i} model={call.get('model')} ---")
        p("SYSTEM PROMPT:")
        p(call.get("system") or "")
        p()
        p("MESSAGES:")
        p(json.dumps(call.get("messages"), indent=2, ensure_ascii=False))
        p()
        p("TOOLS:")
        p(json.dumps(call.get("tools"), indent=2, ensure_ascii=False))
        p()
        p("TOOL_CHOICE:")
        p(json.dumps(call.get("tool_choice"), indent=2, ensure_ascii=False))
        p()
    p("### Luma raw response")
    p(json.dumps(art["luma_raw_response"], indent=2, ensure_ascii=False))
    p()
    p("### Stage 01")
    p(json.dumps(art["stage01"], indent=2, ensure_ascii=False))
    p()
    p("### Stage 02")
    p(json.dumps(art["stage02"], indent=2, ensure_ascii=False))
    p()
    p("### Merged Luma / working payload")
    p(json.dumps(art.get("merged_luma_response"), indent=2, ensure_ascii=False))
    p()
    p("### Stage 08 / handler outcome")
    p(json.dumps(art["stage08_or_handler_outcome"], indent=2, ensure_ascii=False))
    p()
    p("### HTTP response returned to chat")
    p(json.dumps(art["http_response_to_chat"], indent=2, ensure_ascii=False))
    p()
    p("### Session after turn")
    p(json.dumps(art["session_after"], indent=2, ensure_ascii=False))
    p()

Path("capture_t1_t2_report.txt").write_text("\n".join(lines), encoding="utf-8")
print("ok", Path("capture_t1_t2_report.txt").stat().st_size)
print("T1 stage01", data[0]["stage01"]["decision"])
print("T2 stage01", data[1]["stage01"]["decision"])
