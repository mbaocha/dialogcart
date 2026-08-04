"""Progress-step clarification for Decision (Stage 08).

``missing_slots`` remains planning-completeness evidence (Stage 04).
Final ``ask_next`` / ``awaiting`` follow the current progress execution step.

Precedence for final ask_next (Stage 08 authoritative):
1. Current progress step missing effective slots (candidate order)
2. Current progress step applicable unresolved promptables
3. Existing non-slot awaiting logic (confirmation, capability, …)
4. Stage 04 default ask / planning-completeness fallback
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core.planning.planner.promptable import unresolved_search_promptables


def unresolved_promptables_for_step(
    step: Optional[Mapping[str, Any]],
    promptable_slots: Sequence[str],
    entity_schema: Optional[Mapping[str, Any]],
) -> List[str]:
    """Promptables that defer a fact-eligible / selected progress step.

    - Policy ``optional_slots`` that remain promptable
    - Availability-criteria promptables when the step ``resolves`` availability
    """
    if not step or not promptable_slots:
        return []
    promptable = [str(k) for k in promptable_slots]
    ordered: List[str] = []
    seen = set()

    optional = step.get("optional_slots") or []
    if isinstance(optional, list):
        optional_set = {str(k) for k in optional}
        for key in promptable:
            if key in optional_set and key not in seen:
                ordered.append(key)
                seen.add(key)

    resolves = step.get("resolves") or []
    if isinstance(resolves, list) and "availability" in resolves:
        for key in unresolved_search_promptables(promptable, entity_schema):
            if key not in seen:
                ordered.append(key)
                seen.add(key)

    return ordered


def select_progress_candidate(
    candidates: Sequence[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Earliest policy-ordered candidate that is fact-eligible and slot-blocked.

    Fact-eligible: non-slot ``requires`` are satisfied (``missing_requirements`` empty).
    Slot-blocked: non-empty ``missing_slots`` from the candidate evaluator.
    Later commit candidates must not override an earlier exploratory progress step.
    """
    for raw in candidates:
        if not isinstance(raw, Mapping):
            continue
        missing_requirements = raw.get("missing_requirements") or []
        if missing_requirements:
            continue
        missing_slots = raw.get("missing_slots") or []
        if not missing_slots:
            continue
        return dict(raw)
    return None


def resolve_progress_ask(
    *,
    selected_step: Optional[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    promptable_slots: Sequence[str],
    entity_schema: Optional[Mapping[str, Any]],
    default_ask_next: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """Resolve final progress ask from execution candidates.

    Returns:
        ask_next, action_branch, progress_meta

    ``action_branch`` is set only when progress clarification overrides the
    default ask (slot-blocked progress step or promptable deferral of a
    selected/runnable step). Otherwise ``(default_ask_next, None, None)``.
    """
    # Selected / runnable step may still be deferred for applicable promptables.
    if selected_step is not None:
        promptables = unresolved_promptables_for_step(
            selected_step, promptable_slots, entity_schema
        )
        if promptables:
            return (
                promptables[0],
                "promptable_before_step",
                {
                    "progress_action": selected_step.get("action"),
                    "blocker": "promptable",
                    "missing_slots": [],
                    "promptables": list(promptables),
                },
            )
        return default_ask_next, None, None

    progress = select_progress_candidate(candidates)
    if progress is None:
        return default_ask_next, None, None

    missing = [str(s) for s in (progress.get("missing_slots") or []) if s]
    if missing:
        return (
            missing[0],
            "progress_step_clarification",
            {
                "progress_action": progress.get("id") or progress.get("action"),
                "blocker": "slots",
                "missing_slots": list(missing),
                "promptables": [],
            },
        )

    # Fact-eligible with empty missing_slots but unmatched should not happen;
    # still allow promptable deferral via step snapshot on the candidate.
    step_snap = {
        "action": progress.get("id"),
        "optional_slots": progress.get("optional_slots") or [],
        "resolves": progress.get("resolves") or [],
    }
    promptables = unresolved_promptables_for_step(
        step_snap, promptable_slots, entity_schema
    )
    if promptables:
        return (
            promptables[0],
            "promptable_before_step",
            {
                "progress_action": step_snap.get("action"),
                "blocker": "promptable",
                "missing_slots": [],
                "promptables": list(promptables),
            },
        )

    return default_ask_next, None, None
