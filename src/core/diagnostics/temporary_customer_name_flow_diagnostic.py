"""
TEMPORARY_CUSTOMER_NAME_FLOW_DIAGNOSTIC

Remove after the captured reproduction is reviewed.

One-shot dump of one customer-name collection / persistence turn when
DIALOGCART_DUMP_CUSTOMER_NAME_FLOW=1.

Distinguishes:
  H1 — submitted name differs from Commerce returned name
  H2 — NLU/Core authorized name is already wrong
  H3 — persistence returned correct name but render/projection differs

Operational containment (single-worker capture)
-----------------------------------------------
1. Stop all workers.
2. Delete any stale ``.customer_name_flow_dump.json`` if not needed.
3. Enable DIALOGCART_DUMP_CUSTOMER_NAME_FLOW=1.
4. Start exactly one application worker (core API / chat).
5. Reproduce one pending-profile name turn (e.g. reply ``Mma Helen``).
6. Require the CAPTURED stderr event.
7. Stop the worker; unset the env var.
8. Inspect the dump locally; do not commit names or the dump file.
9. Delete this module and all gated call sites.

Assumption: one worker process. No distributed locks.

This module must never raise into callers and must never mutate request,
response, session, plan, or Commerce payloads.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

# TEMPORARY_CUSTOMER_NAME_FLOW_DIAGNOSTIC
# Remove after the captured reproduction is reviewed.
_ENV = "DIALOGCART_DUMP_CUSTOMER_NAME_FLOW"
_DUMP_FILENAME = ".customer_name_flow_dump.json"
_TMP_PREFIX = ".customer_name_flow_dump."
_DIAGNOSTIC = "TEMPORARY_CUSTOMER_NAME_FLOW_DIAGNOSTIC"
_SCHEMA_VERSION = 1
_PENDING = "CUSTOMER_CONTACT_NAME"

_LOCK = threading.Lock()
_CAPTURE_COMPLETE = False
_ACTIVE: Optional[Dict[str, Any]] = None


def enabled() -> bool:
    return os.environ.get(_ENV) == "1"


def _emit(code: str, **extra: Any) -> None:
    try:
        payload = {"event": _DIAGNOSTIC, "code": code}
        payload.update(extra)
        print(
            json.dumps(payload, ensure_ascii=False, default=str),
            file=sys.stderr,
            flush=True,
        )
    except Exception:
        pass


def _dump_path() -> Path:
    # src/core/diagnostics -> parents[3] = repo root
    return Path(__file__).resolve().parents[3] / _DUMP_FILENAME


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_contact(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    stripped = value.strip()
    if not stripped:
        return None
    return {"sha256": _sha256(stripped), "length": len(stripped)}


def _text_hash(text: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str):
        return None
    return {"sha256": _sha256(text), "length": len(text)}


def _pending_profile(session_state: Optional[Mapping[str, Any]]) -> Any:
    if not isinstance(session_state, Mapping):
        return None
    planning = session_state.get("planning")
    if not isinstance(planning, Mapping):
        return None
    return planning.get("pending_profile_request")


def _schema_declares_contact_name(entity_schema: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(entity_schema, Mapping):
        return None
    fields = entity_schema.get("fields")
    if not isinstance(fields, list):
        return {"declared": False, "type": None}
    for field in fields:
        if not isinstance(field, Mapping):
            continue
        if field.get("name") == "customer_contact_name":
            return {
                "declared": True,
                "type": field.get("type"),
                "required": field.get("required"),
            }
    return {"declared": False, "type": None}


def _preceding_assistant_request_type(
    session_state: Optional[Mapping[str, Any]],
) -> str:
    if _pending_profile(session_state) == _PENDING:
        return "CUSTOMER_CONTACT_NAME"
    if not isinstance(session_state, Mapping):
        return "unknown"
    messages = session_state.get("messages")
    if not isinstance(messages, list) or not messages:
        conversation = session_state.get("conversation")
        history = (
            conversation.get("history")
            if isinstance(conversation, Mapping)
            else None
        )
        messages = history if isinstance(history, list) else []
    for item in reversed(messages):
        if not isinstance(item, Mapping):
            continue
        role = item.get("role") or item.get("speaker")
        if role not in ("assistant", "bot"):
            continue
        text = item.get("text") or item.get("content") or ""
        if not isinstance(text, str):
            return "other_assistant"
        lowered = text.casefold()
        if "contact details" in lowered or "your name" in lowered:
            return "CUSTOMER_CONTACT_NAME"
        return "other_assistant"
    return "unknown"


def _qualifies_before_nlu(
    session_state: Optional[Mapping[str, Any]],
    entity_schema: Any,
) -> bool:
    if _pending_profile(session_state) == _PENDING:
        return True
    decl = _schema_declares_contact_name(entity_schema)
    return bool(decl and decl.get("declared"))


def _contact_snapshot(session_state: Optional[Mapping[str, Any]]) -> Any:
    if not isinstance(session_state, Mapping):
        return None
    contact = session_state.get("customer_contact")
    if not isinstance(contact, Mapping):
        return None
    return {
        "customer_id": contact.get("customer_id"),
        "authoritative_name": contact.get("authoritative_name"),
        "name_status": contact.get("name_status"),
    }


def _session_identity(session_state: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(session_state, Mapping):
        return {
            "customer_id": None,
            "customer_contact": None,
            "has_customer_id": False,
            "has_customer_contact": False,
        }
    customer_id = session_state.get("customer_id")
    contact = _contact_snapshot(session_state)
    return {
        "customer_id": customer_id if isinstance(customer_id, int) else customer_id,
        "customer_contact": contact,
        "has_customer_id": isinstance(customer_id, int) and customer_id > 0,
        "has_customer_contact": contact is not None,
    }


def _active_intent(session_state: Optional[Mapping[str, Any]]) -> Any:
    if not isinstance(session_state, Mapping):
        return None
    intent = session_state.get("intent_name") or session_state.get("intent")
    if isinstance(intent, Mapping):
        return intent.get("name")
    return intent


def explain_authorization(
    session_state: Optional[Mapping[str, Any]],
    response: Any,
) -> Dict[str, Any]:
    """Mirror authorize_customer_contact_name gates for dump only (no mutation)."""
    from core.customer_identification import (
        PENDING_CUSTOMER_CONTACT_NAME,
        customer_name_confirmation_prerequisite,
        normalize_authoritative_name,
    )

    result: Dict[str, Any] = {
        "pending_profile_matched": False,
        "revising_name_before_confirmation": False,
        "schema_declared_text": False,
        "resolution_is_resolved": False,
        "authorized_name": None,
        "rejection_reason": None,
    }
    if not isinstance(session_state, Mapping) or not isinstance(response, Mapping):
        result["rejection_reason"] = "invalid_session_or_response"
        return result

    planning = session_state.get("planning")
    contact = session_state.get("customer_contact")
    awaiting_name = (
        isinstance(planning, Mapping)
        and planning.get("pending_profile_request") == PENDING_CUSTOMER_CONTACT_NAME
    )
    revising = (
        session_state.get("confirmation_state") == "pending"
        and customer_name_confirmation_prerequisite(session_state).satisfied
        and isinstance(contact, Mapping)
    )
    result["pending_profile_matched"] = bool(awaiting_name)
    result["revising_name_before_confirmation"] = bool(revising)
    if not (awaiting_name or revising):
        result["rejection_reason"] = "not_awaiting_or_revising_name"
        return result

    schema = response.get("_entity_schema")
    fields = schema.get("fields") if isinstance(schema, Mapping) else None
    declared = any(
        isinstance(field, Mapping)
        and field.get("name") == "customer_contact_name"
        and field.get("type") == "text"
        for field in fields
        if isinstance(fields, list)
    )
    result["schema_declared_text"] = declared
    if not declared:
        result["rejection_reason"] = "schema_missing_customer_contact_name_text"
        return result

    evidence = response.get("_entity_resolution_evidence")
    item = (
        evidence.get("customer_contact_name")
        if isinstance(evidence, Mapping)
        else None
    )
    if not isinstance(item, Mapping) or item.get("resolution") != "RESOLVED":
        result["rejection_reason"] = "resolution_not_resolved"
        result["resolution_raw"] = (
            {"resolution": item.get("resolution"), "value": item.get("value")}
            if isinstance(item, Mapping)
            else None
        )
        return result
    result["resolution_is_resolved"] = True
    value = normalize_authoritative_name(item.get("value"))
    if value is None:
        result["rejection_reason"] = "placeholder_or_empty_name"
        result["resolution_raw"] = {
            "resolution": item.get("resolution"),
            "value": item.get("value"),
        }
        return result
    result["authorized_name"] = value
    result["rejection_reason"] = None
    return result


def _nlu_evidence(response: Any, entity_schema: Any) -> Dict[str, Any]:
    if not isinstance(response, Mapping):
        return {"present": False}
    intent = response.get("intent")
    intent_name = intent.get("name") if isinstance(intent, Mapping) else None
    validated = response.get("validated_intent") or intent_name
    resolutions = response.get("entity_resolutions")
    contact_res = (
        resolutions.get("customer_contact_name")
        if isinstance(resolutions, Mapping)
        else None
    )
    mentions = response.get("_entity_mentions") or response.get("entity_mentions")
    mention = None
    if isinstance(mentions, Mapping):
        raw = mentions.get("customer_contact_name")
        if isinstance(raw, Mapping):
            mention = {
                "state": raw.get("state") or raw.get("status"),
                "raw_value": raw.get("raw_value") or raw.get("value"),
            }
        elif hasattr(raw, "state"):
            mention = {
                "state": str(getattr(raw, "state", None)),
                "raw_value": getattr(raw, "raw_value", None),
            }
    evidence = response.get("_entity_resolution_evidence")
    evidence_item = (
        evidence.get("customer_contact_name")
        if isinstance(evidence, Mapping)
        else None
    )
    facts = response.get("facts") if isinstance(response.get("facts"), Mapping) else {}
    turn = response.get("turn") if isinstance(response.get("turn"), Mapping) else {}
    return {
        "present": True,
        "validated_intent": validated,
        "intent_name": intent_name,
        "proposal_response": response.get("proposal_response"),
        "response_act": response.get("response_act") or turn.get("response_act"),
        "customer_contact_name_mention": mention,
        "entity_resolutions_customer_contact_name": (
            copy.deepcopy(contact_res)
            if isinstance(contact_res, Mapping)
            else contact_res
        ),
        "entity_resolution_evidence_customer_contact_name": (
            copy.deepcopy(evidence_item)
            if isinstance(evidence_item, Mapping)
            else evidence_item
        ),
        "facts_customer_contact_name": facts.get("customer_contact_name"),
        "entity_schema_customer_contact_name": _schema_declares_contact_name(
            entity_schema or response.get("_entity_schema")
        ),
        "turn_understanding": turn.get("understanding"),
    }


def maybe_begin_before_nlu(
    *,
    text: str,
    session_state: Optional[Mapping[str, Any]],
    entity_schema: Any,
    user_id: Optional[str] = None,
    transaction_id: Optional[str] = None,
) -> None:
    if not enabled():
        return
    try:
        with _LOCK:
            global _ACTIVE
            if _CAPTURE_COMPLETE or _ACTIVE is not None:
                return
            if not _qualifies_before_nlu(session_state, entity_schema):
                return
            correlation_id = (
                str(transaction_id).strip()
                if isinstance(transaction_id, str) and transaction_id.strip()
                else str(uuid.uuid4())
            )
            identity = _session_identity(session_state)
            _ACTIVE = {
                "diagnostic": _DIAGNOSTIC,
                "schema_version": _SCHEMA_VERSION,
                "correlation_id": correlation_id,
                "before_nlu": {
                    "correlation_id": correlation_id,
                    "user_id_sha256": _sha256(str(user_id)) if user_id else None,
                    "active_intent": _active_intent(session_state),
                    "confirmation_state": (
                        session_state.get("confirmation_state")
                        if isinstance(session_state, Mapping)
                        else None
                    ),
                    "pending_profile_request": _pending_profile(session_state),
                    "existing_customer": identity,
                    "preceding_assistant_request_type": _preceding_assistant_request_type(
                        session_state
                    ),
                    "user_text": _text_hash(text),
                    "entity_schema_customer_contact_name": _schema_declares_contact_name(
                        entity_schema
                    ),
                },
                "nlu": None,
                "authorization": None,
                "commerce": None,
                "projection": None,
                "render": None,
                "_session_before_persist": None,
            }
            _emit("BEGUN", correlation_id=correlation_id)
    except Exception:
        _emit("ERROR_BEGIN")


def maybe_record_nlu(
    *,
    luma_response: Any,
    entity_schema: Any,
    session_state: Optional[Mapping[str, Any]] = None,
) -> None:
    if not enabled():
        return
    try:
        with _LOCK:
            global _ACTIVE
            if _CAPTURE_COMPLETE or _ACTIVE is None:
                return
            _ = session_state
            _ACTIVE["nlu"] = _nlu_evidence(luma_response, entity_schema)
    except Exception:
        _emit("ERROR_NLU")


def _start_active(
    *,
    correlation_id: str,
    session_state: Optional[Mapping[str, Any]],
    note: str,
    organization_id: Optional[int] = None,
    merged_response: Any = None,
) -> None:
    global _ACTIVE
    identity = _session_identity(session_state)
    nlu = None
    if isinstance(merged_response, Mapping):
        nlu = _nlu_evidence(merged_response, merged_response.get("_entity_schema"))
    _ACTIVE = {
        "diagnostic": _DIAGNOSTIC,
        "schema_version": _SCHEMA_VERSION,
        "correlation_id": correlation_id,
        "before_nlu": {
            "correlation_id": correlation_id,
            "note": note,
            "active_intent": _active_intent(session_state),
            "confirmation_state": (
                session_state.get("confirmation_state")
                if isinstance(session_state, Mapping)
                else None
            ),
            "pending_profile_request": _pending_profile(session_state),
            "existing_customer": identity,
            "preceding_assistant_request_type": _preceding_assistant_request_type(
                session_state
            ),
            "user_text": None,
            "organization_id": organization_id,
        },
        "nlu": nlu,
        "authorization": None,
        "commerce": None,
        "projection": None,
        "render": None,
        "_session_before_persist": None,
    }


def record_persist_phase(
    *,
    session_state: Optional[Mapping[str, Any]],
    merged_response: Any,
    authorized_value: Optional[str],
    organization_id: int,
    customer_id: Optional[int],
    phone: Any,
    email: Any,
) -> None:
    """Record authorization at persistence entry (before Commerce I/O)."""
    if not enabled():
        return
    try:
        with _LOCK:
            global _ACTIVE
            if _CAPTURE_COMPLETE:
                return

            pending = _pending_profile(session_state)
            revising = (
                isinstance(session_state, Mapping)
                and session_state.get("confirmation_state") == "pending"
            )
            if _ACTIVE is None:
                if authorized_value is not None or pending == _PENDING or revising:
                    correlation_id = str(uuid.uuid4())
                    note = (
                        "capture_started_on_authorized_persist"
                        if authorized_value is not None
                        else "capture_started_at_persist"
                    )
                    _start_active(
                        correlation_id=correlation_id,
                        session_state=session_state,
                        note=note,
                        organization_id=organization_id,
                        merged_response=merged_response,
                    )
                    _emit("BEGUN_AT_PERSIST", correlation_id=correlation_id)
                else:
                    return

            assert _ACTIVE is not None
            explained = explain_authorization(session_state, merged_response)
            if authorized_value is not None:
                explained["authorized_name"] = authorized_value
                explained["rejection_reason"] = None
            _ACTIVE["authorization"] = explained
            if _ACTIVE.get("nlu") is None and isinstance(merged_response, Mapping):
                _ACTIVE["nlu"] = _nlu_evidence(
                    merged_response, merged_response.get("_entity_schema")
                )
            _ACTIVE["_session_before_persist"] = {
                "customer_contact": _contact_snapshot(session_state),
                "pending_profile_request": _pending_profile(session_state),
                "confirmation_state": (
                    session_state.get("confirmation_state")
                    if isinstance(session_state, Mapping)
                    else None
                ),
                "customer_id": (
                    session_state.get("customer_id")
                    if isinstance(session_state, Mapping)
                    else None
                ),
            }
            _ACTIVE["_persist_context"] = {
                "organization_id": organization_id,
                "customer_id": customer_id,
                "phone": _hash_contact(phone),
                "email": _hash_contact(email),
                "submitted_name": authorized_value,
            }
            if authorized_value is None:
                _ACTIVE["commerce"] = {
                    "attempted": False,
                    "reason": "authorization_returned_none",
                }
                _ACTIVE["projection"] = {
                    "before": _ACTIVE["_session_before_persist"],
                    "after": _ACTIVE["_session_before_persist"],
                    "customer_contact_name_change": None,
                    "note": "no_persist_attempted",
                }
                _ACTIVE["render"] = {
                    "awaited": False,
                    "reason": "authorization_returned_none",
                }
                _ACTIVE["hypothesis_hint"] = "auth_rejected_or_not_applicable"
                _write_locked()
    except Exception:
        _emit("ERROR_PERSIST_PHASE")


def record_commerce_result(
    *,
    operation: str,
    organization_id: int,
    customer_id: Optional[int],
    submitted_name: str,
    phone: Any,
    email: Any,
    returned_customer: Any = None,
    success: bool,
    exception_type: Optional[str] = None,
) -> None:
    if not enabled():
        return
    try:
        with _LOCK:
            if _CAPTURE_COMPLETE or _ACTIVE is None:
                return
            returned_id = None
            returned_name = None
            if isinstance(returned_customer, Mapping):
                returned_id = returned_customer.get("id")
                returned_name = returned_customer.get("name")
            _ACTIVE["commerce"] = {
                "attempted": True,
                "operation": operation,
                "organization_id": organization_id,
                "customer_id": customer_id,
                "submitted_name": submitted_name,
                "phone": _hash_contact(phone),
                "email": _hash_contact(email),
                "returned_customer_id": returned_id,
                "returned_name": returned_name,
                "success": success,
                "exception_type": exception_type,
                "submitted_equals_returned": (
                    submitted_name == returned_name
                    if success and isinstance(returned_name, str)
                    else None
                ),
            }
    except Exception:
        _emit("ERROR_COMMERCE")


def record_projection_after_persist(
    *,
    session_state: Optional[Mapping[str, Any]],
    name_change: Any,
    persist_ok: bool,
) -> None:
    if not enabled():
        return
    try:
        with _LOCK:
            if _CAPTURE_COMPLETE or _ACTIVE is None:
                return
            before = _ACTIVE.get("_session_before_persist")
            after = {
                "customer_contact": _contact_snapshot(session_state),
                "pending_profile_request": _pending_profile(session_state),
                "confirmation_state": (
                    session_state.get("confirmation_state")
                    if isinstance(session_state, Mapping)
                    else None
                ),
                "customer_id": (
                    session_state.get("customer_id")
                    if isinstance(session_state, Mapping)
                    else None
                ),
            }
            _ACTIVE["projection"] = {
                "persist_ok": persist_ok,
                "before": before,
                "after": after,
                "customer_contact_name_change": (
                    copy.deepcopy(name_change)
                    if isinstance(name_change, Mapping)
                    else name_change
                ),
            }
            change_to = (
                name_change.get("to") if isinstance(name_change, Mapping) else None
            )
            if not persist_ok or not change_to:
                _ACTIVE["render"] = {
                    "awaited": False,
                    "reason": "persist_failed_or_no_name_change",
                }
                commerce = _ACTIVE.get("commerce") or {}
                submitted = commerce.get("submitted_name")
                returned = commerce.get("returned_name")
                if (
                    isinstance(submitted, str)
                    and isinstance(returned, str)
                    and submitted != returned
                ):
                    _ACTIVE["hypothesis_hint"] = "H1_commerce_returned_different_name"
                else:
                    _ACTIVE["hypothesis_hint"] = "persist_incomplete"
                _write_locked()
            else:
                _ACTIVE["_awaiting_render"] = True
    except Exception:
        _emit("ERROR_PROJECTION")


def record_confirmation_render(
    *,
    customer_name_change: Any,
    plan_status: Any = None,
) -> None:
    if not enabled():
        return
    try:
        with _LOCK:
            if _CAPTURE_COMPLETE or _ACTIVE is None:
                return
            supplied = None
            if isinstance(customer_name_change, Mapping):
                supplied = customer_name_change.get("to")
            _ACTIVE["render"] = {
                "plan_status": plan_status,
                "customer_contact_name_change": (
                    copy.deepcopy(customer_name_change)
                    if isinstance(customer_name_change, Mapping)
                    else customer_name_change
                ),
                "name_supplied_to_confirmation_rendering": supplied,
            }
            commerce = _ACTIVE.get("commerce") or {}
            auth = _ACTIVE.get("authorization") or {}
            submitted = commerce.get("submitted_name") or auth.get("authorized_name")
            returned = commerce.get("returned_name")
            projected = (_ACTIVE.get("projection") or {}).get(
                "customer_contact_name_change"
            )
            projected_name = (
                projected.get("to") if isinstance(projected, Mapping) else None
            )
            hypothesis = "inconclusive"
            if (
                isinstance(submitted, str)
                and isinstance(returned, str)
                and submitted != returned
            ):
                hypothesis = "H1_commerce_returned_different_name"
            elif (
                isinstance(returned, str)
                and isinstance(supplied, str)
                and returned != supplied
            ):
                hypothesis = "H3_render_differs_from_commerce_return"
            elif (
                isinstance(projected_name, str)
                and isinstance(supplied, str)
                and projected_name != supplied
            ):
                hypothesis = "H3_render_differs_from_projection"
            elif (
                isinstance(submitted, str)
                and isinstance(returned, str)
                and submitted == returned
                and supplied == submitted
            ):
                hypothesis = "H2_or_matching_submit_and_return"
            _ACTIVE["hypothesis_hint"] = hypothesis
            _write_locked()
    except Exception:
        _emit("ERROR_RENDER")


def _write_locked() -> None:
    """Caller must hold _LOCK."""
    global _CAPTURE_COMPLETE, _ACTIVE
    if _CAPTURE_COMPLETE or _ACTIVE is None:
        return
    payload = {
        key: value
        for key, value in _ACTIVE.items()
        if not str(key).startswith("_")
    }
    dump_path = _dump_path()
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=_TMP_PREFIX,
            suffix=".json",
            dir=str(dump_path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
                handle.write("\n")
            os.replace(tmp_name, dump_path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        _CAPTURE_COMPLETE = True
        _ACTIVE = None
        _emit(
            "CAPTURED",
            path=str(dump_path),
            correlation_id=payload.get("correlation_id"),
            hypothesis_hint=payload.get("hypothesis_hint"),
        )
    except Exception:
        _emit("ERROR_WRITE")
