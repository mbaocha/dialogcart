"""Progress-clarification readiness evidence for Stage 08 Decision.

Projects progress-step ask overrides and selected execution-step metadata.
Stage 08 consumes this for outcome selection — Decision does not call
``resolve_progress_ask`` or construct progress metadata itself.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core.planning.planner.progress_clarification import resolve_progress_ask


def stage_for_execution_action(
    action: Optional[str],
    selected_step: Mapping[str, Any],
) -> Optional[str]:
    """Derive planner stage for a selected policy execution action."""
    if action == "FETCH_BOOKING":
        return "IDENTIFY"
    if action == "SEARCH_AVAILABILITY":
        return "AVAILABILITY"
    if action in (
        "CONFIRM_APPOINTMENT",
        "FINALIZE_RESERVATION",
        "APPLY_MODIFICATION",
        "CONFIRM_CANCELLATION",
    ):
        return "CONFIRM"
    mode = selected_step.get("mode", "exploratory")
    return "AVAILABILITY" if mode == "exploratory" else "CONFIRM"


@dataclass(frozen=True)
class ProgressClarificationEvidence:
    """Outcome-free progress-clarification inputs Decision consumes.

    Does not select ``action`` / ``status`` / ``stage`` / ``awaiting``.
    Does not mutate payload.
    """

    progress_branch: Optional[str] = None
    ask_next: Optional[str] = None
    progress_meta: Optional[Tuple[Tuple[str, Any], ...]] = None
    promptable_before_step: bool = False
    selected_execution_action: Optional[str] = None
    selected_policy_client: Optional[str] = None
    selected_stage: Optional[str] = None
    execution_step_selected: bool = False
    has_progress_clarification: bool = False

    def progress_meta_dict(self) -> Optional[Dict[str, Any]]:
        if self.progress_meta is None:
            return None
        meta = dict(self.progress_meta)
        # Nested lists were frozen as tuples; restore list shape for consumers.
        for key in ("missing_slots", "promptables"):
            value = meta.get(key)
            if isinstance(value, tuple):
                meta[key] = list(value)
        return meta


def _freeze_progress_meta(
    meta: Optional[Mapping[str, Any]],
) -> Optional[Tuple[Tuple[str, Any], ...]]:
    if not isinstance(meta, Mapping):
        return None
    frozen: List[Tuple[str, Any]] = []
    for key, value in meta.items():
        if isinstance(value, list):
            frozen.append((str(key), tuple(value)))
        else:
            frozen.append((str(key), deepcopy(value)))
    return tuple(frozen)


def build_progress_clarification_evidence(
    *,
    selected_step: Optional[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    promptable_slots: Sequence[str],
    entity_schema: Optional[Mapping[str, Any]],
    default_ask_next: Optional[str],
) -> ProgressClarificationEvidence:
    """Compute progress-clarification readiness without selecting an outcome."""
    ask_next, progress_branch, progress_meta = resolve_progress_ask(
        selected_step=selected_step,
        candidates=candidates,
        promptable_slots=promptable_slots,
        entity_schema=entity_schema,
        default_ask_next=default_ask_next,
    )

    promptable_before = progress_branch == "promptable_before_step"
    has_progress = progress_branch in (
        "progress_step_clarification",
        "promptable_before_step",
    )

    # Mirror Stage 08 ask_next fill when promptable meta exists but ask is empty.
    if promptable_before and not ask_next and isinstance(progress_meta, dict):
        promptables = progress_meta.get("promptables") or []
        if promptables:
            ask_next = promptables[0]

    selected_action: Optional[str] = None
    selected_client: Optional[str] = None
    selected_stage: Optional[str] = None
    execution_step_selected = False

    if not has_progress and selected_step is not None:
        selected_action = selected_step.get("action")
        if selected_action is not None:
            selected_action = str(selected_action)
        client = selected_step.get("client")
        selected_client = str(client) if client is not None else None
        selected_stage = stage_for_execution_action(selected_action, selected_step)
        execution_step_selected = True

    return ProgressClarificationEvidence(
        progress_branch=progress_branch,
        ask_next=ask_next,
        progress_meta=_freeze_progress_meta(progress_meta),
        promptable_before_step=promptable_before,
        selected_execution_action=selected_action,
        selected_policy_client=selected_client,
        selected_stage=selected_stage,
        execution_step_selected=execution_step_selected,
        has_progress_clarification=has_progress,
    )
