"""One-shot capture T1/T2. Ephemeral. Do not commit."""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _dump(obj: Any) -> Any:
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return repr(obj)


def main() -> None:
    import anthropic

    llm_calls: List[Dict[str, Any]] = []
    _OrigAnthropic = anthropic.Anthropic

    class CapturingAnthropic(_OrigAnthropic):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            _orig_create = self.messages.create

            def _c(**kwargs):
                llm_calls.append(
                    {
                        "system": kwargs.get("system"),
                        "messages": _dump(kwargs.get("messages")),
                        "model": kwargs.get("model"),
                        "tools": _dump(kwargs.get("tools")),
                        "tool_choice": _dump(kwargs.get("tool_choice")),
                    }
                )
                return _orig_create(**kwargs)

            self.messages.create = _c

    anthropic.Anthropic = CapturingAnthropic  # type: ignore

    import core.planning.pipeline.orchestrator as orch
    import core.planning.pipeline.stage01_intent as s01
    import core.planning.pipeline.stage02_working_turn as s02
    from core.adapters.clients.organization_client import OrganizationClient
    from core.adapters.nlu import LumaClient
    from core.engine.conversation_engine import ConversationEngine
    from core.session.session_manager import clear_session, get_session
    from core.session.turn_persistence import (
        project_and_persist_turn_result,
        resolve_projection_status,
    )
    from nlu.pipeline import NLUPipeline

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import chat as chat_mod

    ORG = 2
    USER = f"capture-{uuid.uuid4().hex[:10]}"
    TZ = "Europe/London"
    try:
        clear_session(ORG, USER)
    except Exception:
        pass

    org_client = OrganizationClient()
    engine = ConversationEngine()
    nlu_pipeline = NLUPipeline()
    stage_snap: Dict[str, Any] = {}

    class CapturingLuma(LumaClient):
        def __init__(self) -> None:
            super().__init__(base_url="http://127.0.0.1:9002")
            self.last_request: Optional[Dict[str, Any]] = None
            self.last_response: Optional[Dict[str, Any]] = None
            self.llm_calls_this_turn: List[Dict[str, Any]] = []

        def resolve(self, *args, **kwargs):
            llm_calls.clear()
            payload: Dict[str, Any] = {
                "user_id": kwargs.get("user_id") or (args[0] if args else None),
                "text": kwargs.get("text") or (args[1] if len(args) > 1 else None),
                "domain": kwargs.get("domain", "service"),
                "timezone": kwargs.get("timezone", "UTC"),
            }
            if kwargs.get("tenant_context"):
                payload["tenant_context"] = kwargs["tenant_context"]
            if kwargs.get("conversation_context"):
                payload["conversation_context"] = kwargs["conversation_context"]
            if kwargs.get("entity_schema"):
                payload["entity_schema"] = kwargs["entity_schema"]
            if kwargs.get("test_now"):
                payload["test_now"] = kwargs["test_now"]

            pr = nlu_pipeline.run(
                payload["text"],
                payload.get("tenant_context") or {},
                now=payload.get("test_now"),
                timezone=payload.get("timezone") or "UTC",
                conversation_context=payload.get("conversation_context"),
                entity_schema=payload.get("entity_schema"),
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
            self.last_request = copy.deepcopy(payload)
            self.last_response = copy.deepcopy(result)
            self.llm_calls_this_turn = list(llm_calls)
            return result

    capturing_luma = CapturingLuma()

    _orig_reconcile = s01.reconcile_intent

    def reconcile_wrap(**kwargs):
        stage_snap["stage01_incoming_luma"] = _dump(kwargs.get("luma_response"))
        stage_snap["stage01_session"] = _dump(kwargs.get("session_state"))
        decision, sess = _orig_reconcile(**kwargs)
        stage_snap["stage01_decision"] = _dump(
            {
                "planning_intent": decision.planning_intent,
                "raw_luma_intent": decision.raw_luma_intent,
                "turn_operation": decision.turn_operation,
                "handler_delegated": decision.handler_delegated,
                "handler_name": decision.handler_name,
                "non_durable_status": decision.non_durable_status,
                "delegated_search_query": decision.delegated_search_query,
                "delegated_slots": decision.delegated_slots,
                "confirm_booking_continuation": decision.confirm_booking_continuation,
                "session_reset_occurred": decision.session_reset_occurred,
                "gate_action": getattr(
                    decision.gate_action, "value", decision.gate_action
                ),
            }
        )
        return decision, sess

    s01.reconcile_intent = reconcile_wrap  # type: ignore
    orch.reconcile_intent = reconcile_wrap  # type: ignore

    _orig_build = s02.build_working_turn

    def build_wrap(**kwargs):
        ar = kwargs["attached_request"]
        stage_snap["stage02_pre"] = {
            "attached_request": _dump(
                {
                    "planning_intent": ar.planning_intent,
                    "turn_operation": ar.turn_operation,
                    "session_reset_occurred": ar.session_reset_occurred,
                    "confirm_booking_continuation": ar.confirm_booking_continuation,
                    "gate_action": getattr(ar.gate_action, "value", ar.gate_action),
                }
            ),
            "entity_schema": _dump(kwargs.get("entity_schema")),
            "session_state": _dump(kwargs.get("session_state")),
            "original_session_state": _dump(kwargs.get("original_session_state")),
            "luma_facts_before": _dump(
                (kwargs.get("luma_response") or {}).get("facts")
            ),
        }
        wt = _orig_build(**kwargs)
        stage_snap["stage02_post"] = {
            "payload_intent": _dump(wt.payload.get("intent")),
            "promoted_effective_slots": _dump(wt.effective_collected_slots),
            "payload_slots": _dump(wt.payload.get("slots")),
            "planning_evidence": wt.payload.get("_current_turn_planning_evidence"),
            "entity_schema": _dump(wt.payload.get("_entity_schema")),
            "effective_collected": _dump(wt.payload.get("_effective_collected_slots")),
        }
        return wt

    s02.build_working_turn = build_wrap  # type: ignore
    orch.build_working_turn = build_wrap  # type: ignore

    domain = chat_mod.resolve_booking_domain_for_org(ORG)
    artifacts: List[Dict[str, Any]] = []

    for i, text in enumerate(
        ["What services do you offer?", "Executive Oil Change"], 1
    ):
        stage_snap.clear()
        llm_calls.clear()
        http_request = chat_mod.build_message_payload(
            user_id=USER,
            text=text,
            timezone=TZ,
            org_id=ORG,
            booking_domain=domain,
        )
        session_before = _dump(get_session(ORG, USER))
        result = engine.process_turn(
            text=text,
            user_id=USER,
            organization_id=ORG,
            session_state=get_session(ORG, USER),
            luma_client=capturing_luma,
            organization_client=org_client,
            timezone=TZ,
        )
        outcome = result.get("outcome") or {}
        try:
            project_and_persist_turn_result(
                result=result,
                outcome=outcome,
                outcome_status=resolve_projection_status(outcome, result=result),
                organization_id=ORG,
                previous_session_state=session_before
                if isinstance(session_before, dict)
                else None,
                user_id=USER,
                working_session_state=result.get("_working_session")
                or get_session(ORG, USER),
                conversation_messages=[
                    {"role": "user", "text": text},
                    *(
                        [
                            {
                                "role": "assistant",
                                "text": result.get("text") or outcome.get("text"),
                            }
                        ]
                        if (result.get("text") or outcome.get("text"))
                        else []
                    ),
                ],
                save=True,
            )
        except Exception as exc:
            stage_snap["persist_error"] = str(exc)

        artifacts.append(
            {
                "turn": i,
                "user_text": text,
                "chat_to_core_http_request": http_request,
                "session_before": session_before,
                "luma_json_request_from_core": _dump(capturing_luma.last_request),
                "luma_llm_calls": _dump(capturing_luma.llm_calls_this_turn),
                "luma_raw_response": _dump(capturing_luma.last_response),
                "stage01": {
                    "incoming_payload": stage_snap.get("stage01_incoming_luma"),
                    "session_state": stage_snap.get("stage01_session"),
                    "decision": stage_snap.get("stage01_decision"),
                },
                "stage02": {
                    "pre": stage_snap.get("stage02_pre"),
                    "post": stage_snap.get("stage02_post"),
                },
                "stage08_or_handler_outcome": {
                    "status": outcome.get("status"),
                    "action": outcome.get("action"),
                    "intent_name": outcome.get("intent_name"),
                    "active_handler": outcome.get("active_handler"),
                    "missing_slots": outcome.get("missing_slots"),
                    "slots": outcome.get("slots"),
                    "ask_next": outcome.get("ask_next"),
                    "search_query": outcome.get("search_query"),
                    "plan": _dump(outcome.get("plan")),
                },
                "merged_luma_response": _dump(result.get("_merged_luma_response")),
                "http_response_to_chat": {
                    "success": result.get("success", True),
                    "outcome": _dump(outcome),
                    "text": result.get("text") or outcome.get("text"),
                    "error": result.get("error"),
                    "message": result.get("message"),
                },
                "session_after": _dump(get_session(ORG, USER)),
            }
        )

    out = Path(__file__).resolve().parent.parent / "capture_t1_t2.json"
    out.write_text(json.dumps(artifacts, indent=2, ensure_ascii=False), encoding="utf-8")
    print(out)
    print("T1 stage01", artifacts[0]["stage01"]["decision"])
    print("T2 stage01", artifacts[1]["stage01"]["decision"])
    print("T2 ctx", artifacts[1]["luma_json_request_from_core"].get("conversation_context"))


if __name__ == "__main__":
    main()
