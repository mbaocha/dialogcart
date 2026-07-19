"""Non-HTTP compatibility entrypoint for tests and legacy callers.



Not on the production request path. Live traffic uses POST /api/message in

message.py, which runs capability/handler boundaries, SessionProjector, and

save_session after ConversationEngine.process_turn().



``handle_message`` preserves the historical three-fallback session load, delegates

to ConversationEngine.process_turn(), projects the complete result through

SessionProjectorV2, persists it, and returns the engine result dict.

Prefer ConversationEngine.process_turn() for new programmatic code.

"""



from __future__ import annotations



import logging

import os
import copy

from datetime import datetime

from typing import Any, Dict, Optional



from core.adapters.clients.organization_client import OrganizationClient

from core.adapters.nlu import LumaClient



logger = logging.getLogger(__name__)





def _resolve_org_id_from_env() -> int:

    """Resolve organization_id for local/test compatibility callers only."""

    value = os.getenv("ORG_ID", "1")

    try:

        org_id = int(value)

        if org_id <= 0:

            raise ValueError("ORG_ID must be positive")

        return org_id

    except Exception:  # noqa: BLE001

        logger.warning("Invalid ORG_ID env value '%s', defaulting to 1", value)

        return 1





def handle_message(

    text: str,

    user_id: str,

    luma_client: Optional[LumaClient] = None,

    availability_client: Optional[Any] = None,

    organization_client: Optional[OrganizationClient] = None,

    catalog_client: Optional[Any] = None,

    session_store: Optional[Any] = None,

    frozen_time: Optional[datetime] = None,

    organization_id: Optional[int] = None,

    **kwargs,  # Backward-compat shim: ignore unknown infra parameters (e.g., customer_id)  # noqa: ARG001

) -> Dict[str, Any]:

    """Compatibility wrapper around ConversationEngine.process_turn()."""

    resolved_org_id = (
        organization_id if organization_id is not None else _resolve_org_id_from_env()
    )
    session_state = None

    if session_store is not None:

        try:

            if hasattr(session_store, "get_session"):

                session_state = session_store.get_session(resolved_org_id, user_id)

            elif callable(session_store):

                session_state = session_store(resolved_org_id, user_id)

        except Exception as e:

            logger.warning(f"Failed to get session for user {user_id}: {e}")



    if session_state is None and "session_state" in kwargs:

        session_state = kwargs["session_state"]



    kwargs.pop("session_state", None)

    kwargs.pop("domain", None)

    if catalog_client is None:

        catalog_client = kwargs.pop("catalog_client", None)



    if session_state is None:

        try:

            from core.session.session_manager import get_session



            session_state = get_session(resolved_org_id, user_id)

            if session_state:

                logger.debug(

                    f"[SESSION_FALLBACK] Loaded session from default store for user_id={user_id} "

                    f"(session_store was None, kwargs.session_state was None or filtered out)"

                )

        except (ImportError, Exception) as e:

            logger.debug(

                f"[SESSION_FALLBACK] Could not load from default session store: {e}"

            )



    from core.engine.conversation_engine import ConversationEngine



    engine = ConversationEngine()

    # frozen_time kept on the public signature for callers; engine ignores it

    # (planning does not consume a clock override).

    previous_session_state = copy.deepcopy(session_state)
    result = engine.process_turn(

        text=text,

        user_id=user_id,

        organization_id=resolved_org_id,

        session_state=session_state,

        availability_client=availability_client,

        organization_client=organization_client,

        catalog_client=catalog_client,

        session_store=session_store,

        frozen_time=frozen_time,

        luma_client=luma_client,

        **kwargs,

    )

    outcome = result.get("outcome")
    if isinstance(outcome, dict):
        from core.session.turn_persistence import project_and_persist_turn_result

        assistant_text = result.get("text") or outcome.get("text")
        conversation_messages = [{"role": "user", "text": text}]
        if assistant_text:
            conversation_messages.append(
                {"role": "assistant", "text": assistant_text}
            )
        projected = project_and_persist_turn_result(
            result=result,
            organization_id=resolved_org_id,
            user_id=user_id,
            previous_session_state=previous_session_state,
            working_session_state=result.get("_working_session") or session_state,
            session_store=session_store,
            handler_conversation_update=result.get(
                "_handler_conversation_update"
            ),
            conversation_messages=conversation_messages,
        )
        if projected is not None:
            result["_projected_session_state"] = projected

    return result


