"""Discovery bridge for availability — sole production orchestration surface.

Composes public Discovery ``Search`` / ``Navigator`` / ``Selector`` with the
availability adapters. Translates to AvailabilityCache / PresentedAvailability
for session and planner consumers.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.discovery import (
    Navigator,
    Search,
    SearchRequest,
    SelectionRequest,
    Selector,
)
from core.workflows.availability.contracts import (
    AvailabilityCache,
    BrowseIntent,
    BrowseProjection,
    PresentedAvailability,
    SelectionResolution,
)
from core.workflows.availability.discovery.mapping import (
    cache_to_trusted,
    presented_to_window,
    selection_result_to_resolution,
    to_discovery_browse_intent,
    trusted_to_cache,
    window_to_presented,
)
from core.workflows.availability.discovery.navigation import (
    AvailabilityNavigationPolicy,
)
from core.workflows.availability.discovery.provider import (
    AvailabilityProvider,
    ExecuteSearch,
)
from core.workflows.availability.discovery.selection import (
    AvailabilitySelectionPolicy,
    selection_mode_for_request,
)
from core.workflows.availability.presentation import (
    DEFAULT_MAX_TIMES,
    project_presentation_to_date,
)
from core.workflows.availability.selection import (
    REASON_CACHE_MATCH,
    REASON_CRITERIA_CHANGED,
    REASON_INCOMPLETE,
    REASON_MULTIPLE_CACHE,
    REASON_MULTIPLE_PRESENTED,
    REASON_NO_OFFERS,
    REASON_NO_PRESENTED,
    REASON_NO_USER_TIME,
    REASON_NOT_IN_CACHE,
    REASON_PRESENTATION_MATCH,
    _create_bind_result,
    _extract_user_time,
    _normalize_user_time,
    _parse_offer_start_parts,
)


def search_via_discovery(
    criteria: Dict[str, Any],
    *,
    execute_search: ExecuteSearch,
    existing_cache: Optional[AvailabilityCache] = None,
) -> Tuple[AvailabilityCache, bool]:
    """Run Discovery Search with AvailabilityProvider.

    Returns ``(cache, reused)`` where ``reused`` is True when the existing
    trusted cache identity still matched.
    """
    provider = AvailabilityProvider(execute_search=execute_search)
    existing = cache_to_trusted(existing_cache)
    trusted = Search().search(
        SearchRequest(criteria=dict(criteria)),
        provider,
        existing=existing,
    )
    reused = existing is not None and trusted is existing
    if reused and isinstance(existing_cache, dict):
        return existing_cache, True
    cache = trusted_to_cache(trusted, template=existing_cache)
    return cache, False


def present_via_discovery(
    cache: AvailabilityCache,
    *,
    page_size: int = DEFAULT_MAX_TIMES,
    search_date: Optional[str] = None,
    slots: Optional[Dict[str, Any]] = None,
    date_proposal: Optional[Dict[str, Any]] = None,
    fingerprint_slots: Optional[Dict[str, Any]] = None,
) -> PresentedAvailability:
    """Build the initial presented window via Discovery Navigator."""
    trusted = cache_to_trusted(cache)
    if trusted is None:
        return PresentedAvailability(slots=[], times=[], more_count=0, total_unique=0)
    policy = AvailabilityNavigationPolicy(
        page_size=page_size,
        search_date=search_date or cache.get("search_date"),
        cache_template=cache,
        slots=slots,
        date_proposal=date_proposal,
        fingerprint_slots=fingerprint_slots,
    )
    window = Navigator(policy).present(trusted)
    return window_to_presented(window)


def browse_via_discovery(
    cache: AvailabilityCache,
    current: PresentedAvailability,
    browse_intent: BrowseIntent,
    *,
    page_size: int = DEFAULT_MAX_TIMES,
    slots: Optional[Dict[str, Any]] = None,
    date_proposal: Optional[Dict[str, Any]] = None,
    fingerprint_slots: Optional[Dict[str, Any]] = None,
) -> BrowseProjection:
    """Advance presentation via Discovery Navigator.

    Returns a BrowseProjection compatible with the legacy availability API.
    """
    trusted = cache_to_trusted(cache)
    if trusted is None:
        return {
            "presented": current,
            "moved": False,
            "reason_code": "exhausted",
        }
    policy = AvailabilityNavigationPolicy(
        page_size=page_size,
        search_date=cache.get("search_date"),
        cache_template=cache,
        slots=slots,
        date_proposal=date_proposal,
        fingerprint_slots=fingerprint_slots,
    )
    navigator = Navigator(policy)
    next_window = navigator.browse(
        to_discovery_browse_intent(browse_intent),
        trusted=trusted,
        current=presented_to_window(current),
    )
    projection = policy.last_projection or {}
    moved = bool(projection.get("moved"))
    reason_code = projection.get("reason_code") or (
        "moved" if moved else "exhausted"
    )
    return {
        "presented": window_to_presented(next_window),
        "moved": moved,
        "reason_code": reason_code,
    }


def project_via_discovery(
    cache: AvailabilityCache,
    target_date: str,
    *,
    current_presentation: Optional[PresentedAvailability] = None,
    page_size: Optional[int] = None,
) -> BrowseProjection:
    """Date projection via the same availability algorithm, Discovery-mapped.

    Navigator has no date-jump API; this wraps ``project_presentation_to_date``
    so orchestration still goes through the discovery bridge surface.
    """
    return project_presentation_to_date(
        cache,
        target_date,
        current_presentation=current_presentation,
        page_size=page_size,
    )


def resolve_via_discovery(
    *,
    user_facts: Optional[Dict[str, Any]] = None,
    presented_availability: Optional[PresentedAvailability] = None,
    availability_cache: Optional[AvailabilityCache] = None,
    slots: Optional[Dict[str, Any]] = None,
    date_proposal: Optional[Dict[str, Any]] = None,
    time_proposal: Optional[Dict[str, Any]] = None,
    temporal: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
) -> SelectionResolution:
    """Resolve selection via Discovery Selector + AvailabilitySelectionPolicy.

    Short-circuits availability modes that Discovery does not encode
    (``criteria_changed``, ``explicit_incomplete``, missing time) using the
    same classifier/guards as the legacy path, then delegates matching to
    Selector. Reason codes match the legacy SelectionResolution vocabulary.
    """
    _ = date_proposal  # must not activate cache selection by itself
    slots = dict(slots or {})
    presented = presented_availability or PresentedAvailability(slots=[])
    cache = availability_cache

    criteria: Dict[str, Any] = {
        "user_facts": user_facts,
        "time_proposal": time_proposal,
        "temporal": temporal,
        "session_state": session_state,
    }
    request = SelectionRequest(criteria=criteria)

    mode = selection_mode_for_request(request)
    if mode == "criteria_changed":
        return SelectionResolution(
            status="criteria_changed",
            source=None,
            slot=None,
            reason_code=REASON_CRITERIA_CHANGED,
            bind_result=None,
        )
    if mode == "explicit_incomplete":
        return SelectionResolution(
            status="ambiguous",
            source=None,
            slot=None,
            reason_code=REASON_INCOMPLETE,
            bind_result=None,
        )

    user_time_raw = _extract_user_time(
        time_proposal=time_proposal,
        temporal=temporal,
        user_facts=user_facts if isinstance(user_facts, dict) else None,
    )
    if not user_time_raw:
        return SelectionResolution(
            status="not_found",
            source=None,
            slot=None,
            reason_code=REASON_NO_USER_TIME,
            bind_result=None,
        )
    if not _normalize_user_time(user_time_raw):
        return SelectionResolution(
            status="not_found",
            source=None,
            slot=None,
            reason_code="normalize_failed",
            bind_result=None,
        )

    if mode == "ambiguous":
        presented_slots = (
            presented.get("slots") if isinstance(presented, dict) else None
        )
        if not isinstance(presented_slots, list) or not presented_slots:
            return SelectionResolution(
                status="not_found",
                source="presentation",
                slot=None,
                reason_code=REASON_NO_OFFERS,
                bind_result=None,
            )
        criteria = {
            **criteria,
            "expected_date": presented.get("search_date"),
        }
        request = SelectionRequest(criteria=criteria)
    elif mode == "explicit_complete" and not isinstance(cache, dict):
        return SelectionResolution(
            status="not_found",
            source="cache",
            slot=None,
            reason_code=REASON_NOT_IN_CACHE,
            bind_result=None,
        )

    policy = AvailabilitySelectionPolicy()
    selector = Selector(policy)
    window = presented_to_window(presented)
    trusted = cache_to_trusted(cache)
    result = selector.resolve(request, window=window, trusted=trusted)

    reason_code = _legacy_reason_code(result, mode=mode)
    bind_result = None
    item = result.get("item")
    if result.get("status") == "matched" and isinstance(item, dict):
        parsed = _parse_offer_start_parts(item.get("starts_at") or item.get("start"))
        if not parsed:
            return SelectionResolution(
                status="not_found",
                source=_legacy_source(result.get("source")),
                slot=None,
                reason_code="parse_failed",
                bind_result=None,
            )
        offer_date, offer_time = parsed
        execution_result = None
        if isinstance(cache, dict):
            execution_result = {
                "type": cache.get("type"),
                "status": cache.get("status"),
                "slots": cache.get("slots"),
                "search_date": cache.get("search_date"),
            }
        # Canonical time comes from the bound presented offer, not NLU's clock.
        bind_result = _create_bind_result(
            slots=slots,
            offer=item,
            offer_date=offer_date,
            user_time_norm=offer_time,
            execution_result=execution_result,
        )
        if not bind_result:
            return SelectionResolution(
                status="not_found",
                source=_legacy_source(result.get("source")),
                slot=None,
                reason_code="no_datetime_range",
                bind_result=None,
            )

    return selection_result_to_resolution(
        result, reason_code=reason_code, bind_result=bind_result
    )


def _legacy_source(source: Optional[str]) -> Optional[str]:
    if source == "search":
        return "cache"
    if source == "presentation":
        return "presentation"
    return None


def _legacy_reason_code(result: Dict[str, Any], *, mode: str) -> str:
    status = result.get("status")
    source = result.get("source")
    if status == "matched":
        return (
            REASON_CACHE_MATCH if source == "search" else REASON_PRESENTATION_MATCH
        )
    if status == "ambiguous":
        return (
            REASON_MULTIPLE_CACHE
            if source == "search"
            else REASON_MULTIPLE_PRESENTED
        )
    if status == "not_found":
        if source == "search" or mode == "explicit_complete":
            return REASON_NOT_IN_CACHE
        return REASON_NO_PRESENTED
    if status == "criteria_changed":
        return REASON_CRITERIA_CHANGED
    return str(result.get("reason_code") or "no_match")
