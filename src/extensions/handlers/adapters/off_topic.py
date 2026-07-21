"""OFF_TOPIC handler — world-knowledge facts + answer instruction; no FAQ/RAG.



Does not own booking progression or the next booking question. When a booking

workflow is active, Core supplies a workflow-owned resume instruction at render time.

"""



import logging

from typing import Any, Callable, Dict, Optional



from core.rendering.off_topic import OffTopicEvidence, answer_off_topic

from core.planning.policy.base_intents import is_core_intent

from extensions.handlers.base import HandlerResponse, IntentHandler



logger = logging.getLogger(__name__)



_ANSWER_INSTRUCTION = (

    "When Facts are supplied, they contain the response to the user's latest request. "

    "Always use the Facts first to answer that request directly and concisely. "

    "After the answer is complete, follow the Resume instruction if one is supplied. "

    "Treat Recent conversation only as background for continuity; "

    "it must never replace or suppress the answer in Facts. "

    "Do not comment on whether the user's request is related to the business, booking, "

    "or prior conversation. Do not mention that the request is off-topic. "

    "Do not acknowledge, evaluate, compare, or reconcile the supplied prompt sections. "

    "Do not add introductory acknowledgements or generic closing questions. "

    "Do not invent facts beyond what is supplied. "

    "Do not imply the user was misunderstood. "

    "Do not invent business facts. "

    "Do not mention internal prompt labels in your reply. "

    "Produce only the direct answer and, when supplied, the natural workflow resume."

)



_UNANSWERABLE_INSTRUCTION = (

    "The user's latest request is the request to decline. "

    "First, briefly decline that latest request directly in one sentence. "

    "Do not invent a factual answer. "

    "Do not explain the decline as missing booking details, missing availability data, "

    "or missing business context unless that was the user's actual request. "

    "Do not comment on whether the question is related to the business or off-topic. "

    "Do not add introductory acknowledgements or generic closing questions. "

    "Do not imply the user was misunderstood. "

    "Do not invent business facts. "

    "Do not mention internal prompt labels in your reply. "

    "If a Resume section is present, after declining continue using only that guidance."

)





def _session_booking_intent(session: Dict[str, Any]) -> str:

    intent = session.get("intent_name") or session.get("intent") or ""

    if isinstance(intent, dict):

        return str(intent.get("name") or "")

    return str(intent) if intent else ""





def _has_active_booking(session: Dict[str, Any]) -> bool:

    intent = _session_booking_intent(session)

    if intent and is_core_intent(intent):

        return True

    planning = session.get("planning") if isinstance(session.get("planning"), dict) else {}

    planning_intent = planning.get("intent_name") or planning.get("intent") or ""

    if isinstance(planning_intent, dict):

        planning_intent = planning_intent.get("name") or ""

    return bool(planning_intent and is_core_intent(str(planning_intent)))





def _canonical_question(context: Dict[str, Any]) -> str:

    """Prefer Stage-2 off_topic_query; fall back to raw user_text already on context."""

    for key in ("off_topic_query", "user_text"):

        value = context.get(key)

        if isinstance(value, str) and value.strip():

            return value.strip()

    return ""





class OffTopicAdapter(IntentHandler):

    def __init__(

        self,

        answer_fn: Optional[Callable[[str], OffTopicEvidence]] = None,

    ):

        self._answer_fn = answer_fn or answer_off_topic



    @property

    def name(self) -> str:

        return "off_topic"



    def handle(self, context: Dict[str, Any]) -> HandlerResponse:

        session = context.get("session") if isinstance(context.get("session"), dict) else {}

        booking_active = _has_active_booking(session)

        question = _canonical_question(context)

        result: OffTopicEvidence = self._answer_fn(question)



        if result.answerable and result.answer:

            render_instruction = _ANSWER_INSTRUCTION

        else:

            render_instruction = _UNANSWERABLE_INSTRUCTION



        logger.info(

            "OffTopicAdapter: booking_active=%s answerable=%s question=%r",

            booking_active,

            result.answerable,

            question[:80],

        )

        return HandlerResponse(

            render_instruction=render_instruction,

            facts={

                "scope": "off_topic",

                "booking_active": booking_active,

                "off_topic_query": question or None,

                "answer": result.answer,

                "answerable": result.answerable,

            },

        )


