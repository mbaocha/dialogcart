"""NLU temporal proposals vs confirmed booking slots (Phase 2).

- date_proposal / time_proposal: extracted from NLU, constrain availability search
- slots.date / slots.time: confirmed datetime (after availability or explicit commit)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.orchestration.luma_facts_adapter import is_flexible_combined_utterance

logger = logging.getLogger(__name__)


def build_date_proposal(
    facts: Optional[Dict[str, Any]],
    date_constraint: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build date_proposal from NLU facts + date_constraint."""
    facts = facts or {}
    dates = facts.get("dates")
    if not isinstance(dates, list):
        dates = []

    mode = None
    if isinstance(date_constraint, dict):
        mode = date_constraint.get("mode")

    if not mode:
        if len(dates) >= 2:
            mode = "range"
        elif len(dates) == 1:
            mode = "single_day"
        else:
            raw_dr = facts.get("date_range")
            if isinstance(raw_dr, dict) and raw_dr.get("start"):
                mode = "range"
            else:
                return None

    proposal: Dict[str, Any] = {"mode": mode}

    raw_dr = facts.get("date_range")
    if isinstance(raw_dr, dict):
        start = raw_dr.get("start") or raw_dr.get("start_date")
        end = raw_dr.get("end") or raw_dr.get("end_date")
        if start:
            proposal["start"] = start
        if end:
            proposal["end"] = end
    elif len(dates) == 1:
        proposal["start"] = dates[0]
    elif len(dates) >= 2:
        proposal["start"] = dates[0]
        proposal["end"] = dates[-1]

    if not proposal.get("start"):
        return None
    return proposal


def build_time_proposal(
    facts: Optional[Dict[str, Any]],
    time_constraint: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build time_proposal from NLU facts + time_constraint."""
    facts = facts or {}
    times = facts.get("times")
    if isinstance(times, list) and times:
        return {"mode": "exact", "value": times[0]}

    if isinstance(time_constraint, dict):
        mode = time_constraint.get("mode")
        if mode == "exact" and time_constraint.get("start"):
            return {"mode": "exact", "value": time_constraint["start"]}
        if mode == "fuzzy":
            label = time_constraint.get("label")
            if label:
                return {
                    "mode": "fuzzy",
                    "label": label,
                    "start": time_constraint.get("start"),
                    "end": time_constraint.get("end"),
                }
    return None


def extract_nlu_proposals(
    luma_response: Optional[Dict[str, Any]],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Extract date/time proposals from a Luma/NLU response."""
    if not isinstance(luma_response, dict):
        return {"date_proposal": None, "time_proposal": None}
    facts = luma_response.get("facts", {})
    if not isinstance(facts, dict):
        facts = {}
    return {
        "date_proposal": build_date_proposal(
            facts, luma_response.get("date_constraint")
        ),
        "time_proposal": build_time_proposal(
            facts, luma_response.get("time_constraint")
        ),
    }


def merge_session_proposals(
    session_state: Optional[Dict[str, Any]],
    date_proposal: Optional[Dict[str, Any]],
    time_proposal: Optional[Dict[str, Any]],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Merge new NLU proposals with session (new turn overwrites when present)."""
    session_state = session_state or {}
    facts = session_state.get("facts")
    if not isinstance(facts, dict):
        facts = {}
    merged_date = session_state.get("date_proposal") or facts.get("date_proposal")
    merged_time = session_state.get("time_proposal") or facts.get("time_proposal")
    if date_proposal:
        merged_date = date_proposal
    if time_proposal:
        merged_time = time_proposal
    return {"date_proposal": merged_date, "time_proposal": merged_time}


def resolve_session_proposals(
    *,
    merged_luma_response: Optional[Dict[str, Any]] = None,
    outcome: Optional[Dict[str, Any]] = None,
    previous_session_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Resolve date/time proposals from response, outcome facts, or prior session."""
    date_proposal = None
    time_proposal = None
    for source in (
        merged_luma_response,
        (outcome or {}).get("facts") if isinstance(outcome, dict) else None,
        previous_session_state,
        (previous_session_state or {}).get("facts")
        if isinstance(previous_session_state, dict)
        else None,
    ):
        if not isinstance(source, dict):
            continue
        if date_proposal is None:
            date_proposal = source.get("date_proposal")
        if time_proposal is None:
            time_proposal = source.get("time_proposal")
    return {"date_proposal": date_proposal, "time_proposal": time_proposal}


def expand_slots_for_planning(
    slots: Dict[str, Any],
    date_proposal: Optional[Dict[str, Any]] = None,
    time_proposal: Optional[Dict[str, Any]] = None,
    date_constraint: Optional[Dict[str, Any]] = None,
    nlu_facts: Optional[Dict[str, Any]] = None,
    time_constraint: Optional[Dict[str, Any]] = None,
    intent_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Virtual slot view for missing_slots — proposals satisfy planning, not confirmed slots."""
    expanded = dict(slots or {})

    if is_flexible_combined_utterance(date_constraint, nlu_facts):
        return expanded

    if date_proposal and not expanded.get("date") and not expanded.get("date_range"):
        mode = date_proposal.get("mode")
        start = date_proposal.get("start")
        end = date_proposal.get("end")
        if mode == "single_day" and start:
            expanded["date"] = start
        elif start and end:
            expanded["date_range"] = {"start": start, "end": end}
            expanded["date"] = start
        elif start:
            expanded["date"] = start

    if time_proposal and not expanded.get("time"):
        if time_proposal.get("mode") == "exact" and time_proposal.get("value"):
            expanded["time"] = time_proposal["value"]
        elif time_proposal.get("mode") == "fuzzy" and time_proposal.get("label"):
            expanded["time"] = time_proposal["label"]

    # CREATE_APPOINTMENT exact time_constraint (legacy path)
    if (
        intent_name == "CREATE_APPOINTMENT"
        and not expanded.get("time")
        and isinstance(time_constraint, dict)
        and time_constraint.get("mode") == "exact"
        and time_constraint.get("start")
    ):
        expanded["time"] = time_constraint["start"]

    return expanded


def apply_confirmed_datetime(
    slots: Dict[str, Any],
    date_proposal: Optional[Dict[str, Any]] = None,
    time_proposal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Copy proposals into confirmed slots after availability is resolved."""
    confirmed = dict(slots or {})
    if date_proposal:
        mode = date_proposal.get("mode")
        start = date_proposal.get("start")
        end = date_proposal.get("end")
        if mode == "single_day" and start:
            confirmed["date"] = start
        elif start and end:
            confirmed["date_range"] = {"start": start, "end": end}
            confirmed["date"] = start
        elif start:
            confirmed["date"] = start
    if time_proposal:
        if time_proposal.get("mode") == "exact" and time_proposal.get("value"):
            confirmed["time"] = time_proposal["value"]
        elif time_proposal.get("mode") == "fuzzy" and time_proposal.get("label"):
            confirmed["time"] = time_proposal["label"]
    return confirmed


def apply_time_constraint_to_missing_slots(
    intent_name: str,
    missing_slots: list,
    time_constraint: Optional[Dict[str, Any]],
) -> list:
    """Remove 'time' from missing_slots when an exact time_constraint satisfies it.

    Only mode=exact satisfies the time requirement.  Fuzzy/window constraints do
    NOT — the user stated a preference window, not a committed time.
    """
    if intent_name != "CREATE_APPOINTMENT":
        return missing_slots
    if not isinstance(time_constraint, dict):
        return missing_slots
    if time_constraint.get("mode") == "exact" and "time" in missing_slots:
        return [s for s in missing_slots if s != "time"]
    return missing_slots


def resolve_execution_proposals(
    plan: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Resolve date/time proposals for execution (availability search / confirmation).

    Priority:
      1. plan.date_proposal / time_proposal  (set by plan_message from merged response)
      2. session_state.date_proposal / time_proposal  (prior turn, persisted)
      3. session_state.facts.date_proposal / time_proposal  (legacy fallback)
    """
    plan = plan or {}
    session_state = session_state or {}
    session_facts = session_state.get("facts") or {}

    date_proposal = (
        plan.get("date_proposal")
        or session_state.get("date_proposal")
        or (session_facts.get("date_proposal") if isinstance(session_facts, dict) else None)
    )
    time_proposal = (
        plan.get("time_proposal")
        or session_state.get("time_proposal")
        or (session_facts.get("time_proposal") if isinstance(session_facts, dict) else None)
    )
    return {"date_proposal": date_proposal, "time_proposal": time_proposal}


def build_datetime_range_from_slots(
    slots: Dict[str, Any],
    execution_result: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Construct a datetime_range from slots.date + slots.time.

    Returns the existing datetime_range if already present.
    Duration is read from the first availability slot when execution_result is provided;
    falls back to 60 minutes.
    Returns None when date/time are missing or unparseable.
    """
    if isinstance(slots.get("datetime_range"), dict):
        return slots["datetime_range"]

    date_str = slots.get("date")
    time_str = slots.get("time")
    if not date_str or not time_str:
        return None

    from datetime import datetime as _dt, timedelta as _td

    date_obj = None
    if isinstance(date_str, str):
        date_only = date_str.split("T")[0].split(" ")[0]
        try:
            date_obj = _dt.strptime(date_only, "%Y-%m-%d")
        except ValueError:
            try:
                date_obj = _dt.fromisoformat(date_only)
            except (ValueError, AttributeError):
                pass
    if not date_obj:
        return None

    time_lower = str(time_str).lower()
    is_pm = "pm" in time_lower
    is_am = "am" in time_lower
    time_clean = time_lower.replace("am", "").replace("pm", "").strip()
    parts = time_clean.split(":") if ":" in time_clean else [time_clean, "00"]
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if is_pm and hour < 12:
            hour += 12
        elif is_am and hour == 12:
            hour = 0
        start_dt = date_obj.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except (ValueError, IndexError, TypeError):
        return None

    duration_minutes = 60
    avail_slots = (execution_result or {}).get("slots", [])
    if isinstance(avail_slots, list) and avail_slots:
        first = avail_slots[0]
        if isinstance(first, dict):
            s_raw = first.get("starts_at") or first.get("start")
            e_raw = first.get("ends_at") or first.get("end")
            if s_raw and e_raw:
                try:
                    s = _dt.fromisoformat(str(s_raw).replace("Z", "+00:00"))
                    e = _dt.fromisoformat(str(e_raw).replace("Z", "+00:00"))
                    duration_minutes = int((e - s).total_seconds() / 60)
                except (ValueError, AttributeError):
                    pass

    end_dt = start_dt + _td(minutes=duration_minutes)
    return {"start": start_dt.isoformat(), "end": end_dt.isoformat()}


def slots_for_availability_search(
    slots: Dict[str, Any],
    date_proposal: Optional[Dict[str, Any]] = None,
    time_proposal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execution view: proposals constrain search when confirmed slots absent."""
    search_slots = dict(slots or {})
    if not search_slots.get("date") and date_proposal:
        start = date_proposal.get("start")
        end = date_proposal.get("end")
        if start:
            search_slots["date"] = start
        if end:
            search_slots["date_range"] = {"start": start, "end": end}
    if not search_slots.get("time") and time_proposal:
        if time_proposal.get("value"):
            search_slots["time"] = time_proposal["value"]
        elif time_proposal.get("label"):
            search_slots["time"] = time_proposal["label"]
    return search_slots
