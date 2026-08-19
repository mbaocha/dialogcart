"""
TEMPORARY_AVAILABILITY_RENDER_DIAGNOSTIC

Remove after the captured reproduction is reviewed.

One-shot, allow-listed dump of the first availability LlmRenderRequest + assembled
LLM user message when DIALOGCART_DUMP_AVAILABILITY_RENDER=1.

Operational containment (single-worker capture)
-----------------------------------------------
1. Stop all workers.
2. Check for an existing dump and temporary files without displaying content.
3. If an existing valid capture is still needed, preserve it and do not start a
   new capture.
4. Otherwise delete stale dump/temp files.
5. Enable DIALOGCART_DUMP_AVAILABILITY_RENDER=1.
6. Start exactly one application worker.
7. Reproduce once.
8. Require the CAPTURED event.
9. Stop the worker immediately.
10. Remove the environment variable.
11. Inspect locally and treat the dump as potentially sensitive despite redaction.
12. Do not commit, upload, paste into chat/tickets or back it up.
13. Delete promptly after analysis.
14. Do not claim deletion is guaranteed secure erasure on SSD/journaled storage.
15. Delete this module and the gated call site in ``llm_renderer.py``.

Assumption: one worker process for capture. No distributed locks.

This module must never raise into rendering callers and must never mutate the
render request or the assembled user message passed to the model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# TEMPORARY_AVAILABILITY_RENDER_DIAGNOSTIC
# Remove after the captured reproduction is reviewed.
_ENV = "DIALOGCART_DUMP_AVAILABILITY_RENDER"
_DUMP_FILENAME = ".availability_render_dump.json"
_TMP_PREFIX = ".availability_render_dump."
_REDACTED = "[REDACTED]"
_ORG_DESC_OMITTED = "[ORGANIZATION_DESCRIPTION_OMITTED]"
_SECTION_OMITTED = "[SECTION_OMITTED]"
_EXPECTED_SCHEMA_VERSION = 2
_EXPECTED_DIAGNOSTIC = "TEMPORARY_AVAILABILITY_RENDER_DIAGNOSTIC"

# Stable section markers from core.rendering.llm_renderer._build_user_message
_BK_HEADER = "Business Knowledge (Authoritative):"
_SUPPORTING_HEADER = "Supporting Evidence:"
_EXECUTION_HEADER = "Execution evidence:"
_RECOVERY_HEADER = "Recovery context:"
_FACTS_HEADER = "Facts:"
_AVAILABILITY_HEADER = "Availability:"
_CONVERSATION_HEADER = "Recent conversation:"
_USER_REQUEST_HEADER = "Current user request:"
_RESUME_HEADER = "Resume:"

_LOCK = threading.Lock()
_CAPTURE_COMPLETE = False

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)"
)
_VEHICLE_REG_RE = re.compile(
    r"\b(?:[A-Z]{2}\d{2}\s?[A-Z]{3}|[A-Z]{1,3}\d{1,4}\s?[A-Z]{0,3})\b",
    re.IGNORECASE,
)
_SECRET_INLINE_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|bearer|authorization|"
    r"cookie|set-cookie|x-api-key)\b\s*[:=]\s*[^\s,;\"']+"
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)^(.*(phone|email|mail|token|secret|password|passwd|api[_-]?key|"
    r"authorization|auth|cookie|customer[_-]?name|customer[_-]?id|user[_-]?id|"
    r"account[_-]?id|full[_-]?name|first[_-]?name|last[_-]?name|surname|"
    r"registration|vehicle[_-]?reg|reg[_-]?number|vrm|"
    r"national[_-]?insurance|ssn|dob|date[_-]?of[_-]?birth).*)$"
)
_ORG_DESCRIPTION_KEYS = frozenset(
    {
        "business_about",
        "businessabout",
        "about",
        "description",
        "organization_description",
        "organisation_description",
        "org_description",
        "business_description",
    }
)
_WORKFLOW_SCALAR_ALLOWLIST = frozenset(
    {
        "business_name",
        "opening_summary",
        "open_days",
        "closed_days",
        "hours",
        "opening_hours",
        "cancellation_summary",
        "rescheduling_summary",
        "timezone",
        "currency",
        "locale",
    }
)


def _emit_event(code: str) -> None:
    """Minimal stderr event — no paths, payloads, or exception text."""
    try:
        print(
            json.dumps(
                {
                    "event": "TEMPORARY_AVAILABILITY_RENDER_DIAGNOSTIC",
                    "code": code,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except Exception:
        pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    return bool(_SENSITIVE_KEY_RE.match(key.strip()))


def _is_org_description_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    return key.strip().lower() in _ORG_DESCRIPTION_KEYS


def _redact_text(text: str) -> str:
    if not text:
        return text
    redacted = _EMAIL_RE.sub(_REDACTED, text)
    redacted = _SECRET_INLINE_RE.sub(_REDACTED, redacted)
    redacted = _PHONE_RE.sub(_REDACTED, redacted)
    redacted = _VEHICLE_REG_RE.sub(_REDACTED, redacted)
    return redacted


def _org_description_placeholder(length: int) -> Dict[str, Any]:
    return {
        "content_omitted": True,
        "length": length,
        "sha256": _sha256_text(f"{_ORG_DESC_OMITTED}|length={length}"),
    }


def _section_omission(header: str, original_body: str) -> str:
    length = len(original_body)
    meta = {
        "content_omitted": True,
        "length": length,
        "sha256": _sha256_text(f"{_SECTION_OMITTED}|{header}|length={length}"),
    }
    return f"{header}\n{_canonical_json(meta)}"


def _redact_value(value: Any, *, key: Optional[str] = None) -> Any:
    """Recursively redact. Sensitive keys are replaced before values are read."""
    if key is not None and _is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for raw_key, raw_val in value.items():
            key_s = str(raw_key)
            if _is_sensitive_key(key_s):
                out[key_s] = _REDACTED
                continue
            out[key_s] = _redact_value(raw_val, key=key_s)
        return out
    return value


def _sanitize_business_knowledge_object(knowledge: Any) -> Any:
    """Allow-list scalars; omit org descriptions; never serialize unknown values."""
    if not isinstance(knowledge, Mapping):
        return {
            "content_omitted": True,
            "reason": "business_knowledge_not_object",
            "type": type(knowledge).__name__,
        }

    out: Dict[str, Any] = {}
    for raw_key, raw_val in knowledge.items():
        key_s = str(raw_key)
        key_l = key_s.lower()

        if _is_sensitive_key(key_s):
            out[key_s] = _REDACTED
            continue

        if _is_org_description_key(key_s):
            length = (
                len(raw_val)
                if isinstance(raw_val, (str, bytes, bytearray, list, dict))
                else len(str(raw_val))
            )
            placeholder = _org_description_placeholder(length)
            placeholder["key"] = key_s
            out[key_s] = placeholder
            continue

        if key_l in _WORKFLOW_SCALAR_ALLOWLIST and isinstance(
            raw_val, (str, bool, int, float)
        ):
            out[key_s] = _redact_text(raw_val) if isinstance(raw_val, str) else raw_val
            continue

        meta: Dict[str, Any] = {
            "type": type(raw_val).__name__,
            "value_omitted": True,
        }
        if isinstance(raw_val, (str, bytes, bytearray, list, dict, tuple, set)):
            meta["length"] = len(raw_val)
        out[key_s] = meta

    # Defence in depth: regex-redact any residual strings in allow-listed tree.
    return _redact_value(out)


def _redact_conversation_history(history: Any) -> List[Dict[str, str]]:
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes)):
        return []
    redacted: List[Dict[str, str]] = []
    for item in history:
        if not isinstance(item, Mapping):
            continue
        role = item.get("role")
        text = item.get("text")
        redacted.append(
            {
                "role": _redact_text(role) if isinstance(role, str) else "",
                "text": _redact_text(text) if isinstance(text, str) else "",
            }
        )
    return redacted


def _value_length(value: Any) -> Optional[int]:
    if isinstance(value, (str, bytes, bytearray, list, dict, tuple, set)):
        return len(value)
    return None


def _structured_context_sections(
    structured_context: Any,
) -> Tuple[List[str], Dict[str, Any], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if not isinstance(structured_context, Mapping):
        return [], {}, [], None

    keys = sorted(str(k) for k in structured_context.keys())
    workflow_scalars: Dict[str, Any] = {}
    other_keys: List[Dict[str, Any]] = []
    org_description: Optional[Dict[str, Any]] = None

    for raw_key, raw_val in structured_context.items():
        key_s = str(raw_key)
        key_l = key_s.lower()

        if _is_sensitive_key(key_s):
            other_keys.append({"key": key_s, "type": "redacted_sensitive_key"})
            continue

        if _is_org_description_key(key_s):
            length = _value_length(raw_val)
            if length is None:
                length = len(str(raw_val))
            org_description = {
                "key": key_s,
                **_org_description_placeholder(length),
            }
            continue

        if key_l in _WORKFLOW_SCALAR_ALLOWLIST and isinstance(
            raw_val, (str, bool, int, float)
        ):
            if isinstance(raw_val, str):
                workflow_scalars[key_s] = _redact_text(raw_val)
            else:
                workflow_scalars[key_s] = raw_val
            continue

        meta: Dict[str, Any] = {"key": key_s, "type": type(raw_val).__name__}
        length = _value_length(raw_val)
        if length is not None:
            meta["length"] = length
        other_keys.append(meta)

    return keys, workflow_scalars, other_keys, org_description


def _rewrite_business_knowledge_section(section: str) -> str:
    """Structurally redact BK JSON; fall back to full-section omission."""
    prefix = _BK_HEADER
    if not section.startswith(prefix):
        return _section_omission(_BK_HEADER, section)
    body = section[len(prefix) :]
    if body.startswith("\n"):
        body = body[1:]
    original_len = len(body)
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _section_omission(_BK_HEADER, body)
    sanitized = _sanitize_business_knowledge_object(parsed)
    rendered = json.dumps(sanitized, indent=2, ensure_ascii=False, default=str)
    return (
        f"{prefix}\n"
        f"# structurally_redacted diagnostic copy; original_json_length={original_len}\n"
        f"{rendered}"
    )


def _structurally_redact_assembled_message(user_message: str) -> str:
    """
    Build a structurally redacted diagnostic copy of the assembled LLM message.

    Uses stable section headers from _build_user_message. Does not mutate the
    model input. Not byte-identical to the exact model message.
    """
    if not isinstance(user_message, str):
        user_message = str(user_message)

    parts = user_message.split("\n\n")
    out_parts: List[str] = []
    for part in parts:
        if part.startswith(_BK_HEADER):
            out_parts.append(_rewrite_business_knowledge_section(part))
            continue
        if part.startswith(_SUPPORTING_HEADER):
            body = part[len(_SUPPORTING_HEADER) :].lstrip("\n")
            out_parts.append(_section_omission(_SUPPORTING_HEADER, body))
            continue
        if part.startswith(_EXECUTION_HEADER):
            body = part[len(_EXECUTION_HEADER) :].lstrip("\n")
            out_parts.append(_section_omission(_EXECUTION_HEADER, body))
            continue
        if part.startswith(_RECOVERY_HEADER):
            body = part[len(_RECOVERY_HEADER) :].lstrip("\n")
            out_parts.append(_section_omission(_RECOVERY_HEADER, body))
            continue
        if part.startswith(_FACTS_HEADER):
            body = part[len(_FACTS_HEADER) :].lstrip("\n")
            out_parts.append(_section_omission(_FACTS_HEADER, body))
            continue
        # Preserve diagnostically useful / low-structure sections with regex only.
        # Includes: Recent conversation, instruction text, Current user request,
        # Resume, Availability, and the static Business Knowledge presentation
        # guidance paragraph (not organization-authored prose).
        out_parts.append(_redact_text(part))

    return "\n\n".join(out_parts)


def _dump_path() -> Path:
    return Path(__file__).resolve().parents[3] / _DUMP_FILENAME


def _restrictive_permissions(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_capture_status(path: Path) -> str:
    """
    Return VALID, STALE_INVALID, or MISSING.

    VALID requires non-empty JSON with expected schema_version and diagnostic.
    """
    try:
        if not path.exists() or not path.is_file():
            return "MISSING"
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return "STALE_INVALID"
        data = json.loads(raw)
        if not isinstance(data, dict):
            return "STALE_INVALID"
        if data.get("schema_version") != _EXPECTED_SCHEMA_VERSION:
            return "STALE_INVALID"
        if data.get("diagnostic") != _EXPECTED_DIAGNOSTIC:
            return "STALE_INVALID"
        return "VALID"
    except Exception:
        return "STALE_INVALID"


def _try_unlink(path: Path) -> bool:
    try:
        path.unlink()
        return not path.exists()
    except OSError:
        return False


def _publish_capture(path: Path, payload: str) -> str:
    """
    Publish completed JSON without an empty final-name claim stage.

    Single-worker assumption. Sequence:
      1. Classify existing final path (VALID / STALE_INVALID / MISSING).
      2. Write complete payload to a restrictive temp file and fsync (mirror).
      3. Exclusively create the final path and write the complete payload
         directly (O_CREAT|O_EXCL), then fsync — never create an empty claim.
      4. Best-effort temp cleanup.

    Returns: CAPTURED | SKIP_VALID_EXISTS | STALE_INVALID_EXISTS | WRITE_FAILED
    """
    status = _read_capture_status(path)
    if status == "VALID":
        return "SKIP_VALID_EXISTS"
    if status == "STALE_INVALID":
        if not _try_unlink(path):
            return "STALE_INVALID_EXISTS"

    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=_TMP_PREFIX,
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _restrictive_permissions(tmp_path)

        # Re-check after temp write (another worker is out of scope; still safe).
        status = _read_capture_status(path)
        if status == "VALID":
            return "SKIP_VALID_EXISTS"
        if status == "STALE_INVALID":
            if not _try_unlink(path):
                return "STALE_INVALID_EXISTS"

        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            out_fd = os.open(str(path), flags, 0o600)
        except FileExistsError:
            status = _read_capture_status(path)
            if status == "VALID":
                return "SKIP_VALID_EXISTS"
            return "STALE_INVALID_EXISTS"

        try:
            with os.fdopen(out_fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            _restrictive_permissions(path)
            return "CAPTURED"
        except Exception:
            # Partial exclusive create — remove so it cannot stick as VALID.
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return "WRITE_FAILED"
    except OSError:
        return "WRITE_FAILED"
    except Exception:
        return "WRITE_FAILED"
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def _build_record(request: Any, user_message: str) -> Dict[str, Any]:
    facts = request.facts if isinstance(getattr(request, "facts", None), dict) else {}
    availability = facts.get("availability")
    time_resolution = facts.get("time_resolution")
    structured_context = facts.get("structured_context")

    instruction = getattr(request, "render_instruction", "") or ""
    if not isinstance(instruction, str):
        instruction = str(instruction)
    history = getattr(request, "conversation_history", None) or []
    # Local copy only — never mutate caller user_message / request.
    message_for_diag = user_message if isinstance(user_message, str) else str(user_message)

    redacted_instruction = _redact_text(instruction)
    redacted_history = _redact_conversation_history(history)
    redacted_availability = _redact_value(availability)
    redacted_time_resolution = _redact_value(time_resolution)
    structural_message = _structurally_redact_assembled_message(message_for_diag)

    (
        sc_keys,
        workflow_scalars,
        other_keys,
        org_description,
    ) = _structured_context_sections(structured_context)

    instruction_canon = redacted_instruction
    history_canon = _canonical_json(redacted_history)
    availability_canon = _canonical_json(redacted_availability)
    time_resolution_canon = _canonical_json(redacted_time_resolution)
    message_canon = structural_message

    return {
        "schema_version": _EXPECTED_SCHEMA_VERSION,
        "diagnostic": _EXPECTED_DIAGNOSTIC,
        "note": (
            "Temporary one-shot allow-listed dump under single-worker assumption. "
            "Stop the app and unset DIALOGCART_DUMP_AVAILABILITY_RENDER after capture. "
            "assembled_llm_user_message is a structurally redacted representation of "
            "the exact model message, not a byte-identical copy. "
            "Hashes cover redacted canonical forms only. "
            "Treat the dump as potentially sensitive despite redaction. "
            "Deletion is not guaranteed secure erasure on SSD/journaled storage."
        ),
        "assembled_llm_user_message_policy": (
            "structurally_redacted_representation_of_exact_model_message_not_byte_identical"
        ),
        "render_instruction": redacted_instruction,
        "conversation_history": redacted_history,
        "availability_facts": redacted_availability,
        "time_resolution_facts": redacted_time_resolution,
        "structured_context_keys": sc_keys,
        "structured_context_workflow_scalars": workflow_scalars,
        "structured_context_other_keys": other_keys,
        "organization_description": org_description,
        "assembled_llm_user_message": structural_message,
        "lengths": {
            "render_instruction": len(instruction_canon),
            "conversation_history": len(history_canon),
            "availability_facts": len(availability_canon),
            "time_resolution_facts": len(time_resolution_canon),
            "assembled_llm_user_message": len(message_canon),
        },
        "hashes": {
            "render_instruction_sha256": _sha256_text(instruction_canon),
            "conversation_history_sha256": _sha256_text(history_canon),
            "availability_facts_sha256": _sha256_text(availability_canon),
            "time_resolution_facts_sha256": _sha256_text(time_resolution_canon),
            "assembled_llm_user_message_sha256": _sha256_text(message_canon),
        },
        "hash_policy": "redacted_canonical_only",
        "concurrency_assumption": "single_worker_process",
    }


def maybe_dump_availability_render(request: Any, user_message: str) -> None:
    """
    TEMPORARY_AVAILABILITY_RENDER_DIAGNOSTIC

    Best-effort one-shot capture. Never mutates inputs. Never raises to caller.
    """
    global _CAPTURE_COMPLETE

    try:
        with _LOCK:
            if _CAPTURE_COMPLETE:
                return
            if os.environ.get(_ENV) != "1":
                return
            facts = getattr(request, "facts", None)
            if not isinstance(facts, dict):
                return
            availability = facts.get("availability")
            if not isinstance(availability, Mapping):
                return

            dump_path = _dump_path()
            existing = _read_capture_status(dump_path)
            if existing == "VALID":
                _CAPTURE_COMPLETE = True
                _emit_event("SKIP_VALID_EXISTS")
                return

            try:
                record = _build_record(request, user_message)
                payload = json.dumps(record, indent=2, ensure_ascii=False, default=str)
            except Exception:
                _emit_event("SERIALIZE_FAILED")
                return

            if existing == "STALE_INVALID":
                _emit_event("STALE_INVALID_EXISTS")

            result = _publish_capture(dump_path, payload)
            if result == "CAPTURED":
                _CAPTURE_COMPLETE = True
                _emit_event("CAPTURED")
                return
            if result == "SKIP_VALID_EXISTS":
                _CAPTURE_COMPLETE = True
                _emit_event("SKIP_VALID_EXISTS")
                return
            if result == "STALE_INVALID_EXISTS":
                # Do not mark process complete — operator can delete and retry.
                _emit_event("STALE_INVALID_EXISTS")
                return
            _emit_event("WRITE_FAILED")
    except Exception:
        try:
            _emit_event("UNEXPECTED_FAILURE")
        except Exception:
            pass
