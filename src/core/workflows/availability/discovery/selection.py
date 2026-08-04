"""AvailabilitySelectionPolicy — SelectionPolicy adapter for availability."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from core.discovery.models import SelectionRequest
from core.workflows.availability.presentation import normalize_search_date
from core.workflows.availability.selection import (
    _extract_user_time,
    _match_offers,
    _normalize_user_time,
    classify_selection_mode,
    user_time_omits_meridiem,
)


class AvailabilitySelectionPolicy:
    """Adapt availability matching to Discovery Selector.

    Reuses ``classify_selection_mode`` and ``_match_offers``.
    Expected ``SelectionRequest.criteria`` shape (opaque to Discovery)::

        {
            "user_facts": dict,
            "time_proposal": dict | None,
            "temporal": dict | None,
            "session_state": dict | None,   # for mode classification only
            "expected_date": str | None,    # optional match date override
            "staff": str | None,
            "location": str | None,
        }
    """

    def is_explicit(self, request: SelectionRequest) -> bool:
        criteria = _criteria(request)
        mode = classify_selection_mode(
            user_facts=criteria.get("user_facts"),
            time_proposal=criteria.get("time_proposal"),
            temporal=criteria.get("temporal"),
            session_state=criteria.get("session_state"),
        )
        return mode == "explicit_complete"

    def find_matches(
        self,
        items: Sequence[Mapping[str, Any]],
        request: SelectionRequest,
    ) -> List[Dict[str, Any]]:
        criteria = _criteria(request)
        user_facts = criteria.get("user_facts")
        user_facts = user_facts if isinstance(user_facts, dict) else {}

        user_time_raw = _extract_user_time(
            time_proposal=criteria.get("time_proposal"),
            temporal=criteria.get("temporal"),
            user_facts=user_facts,
        )
        user_time_norm = _normalize_user_time(user_time_raw) if user_time_raw else None
        if not user_time_norm:
            return []

        expected_date = criteria.get("expected_date")
        if expected_date:
            expected_date = normalize_search_date(expected_date)
        elif user_facts.get("date_from_current_turn") and user_facts.get("date"):
            expected_date = normalize_search_date(user_facts.get("date"))

        staff = criteria.get("staff_id") or criteria.get("staff")
        location = criteria.get("location")
        if staff is None and (
            user_facts.get("staff_id_from_current_turn")
            or user_facts.get("staff_from_current_turn")
        ):
            staff = (
                user_facts.get("staff_id")
                or user_facts.get("staff")
                or user_facts.get("resource")
            )
        if location is None and user_facts.get("location_from_current_turn"):
            location = user_facts.get("location")

        allow_clock_face = user_time_omits_meridiem(
            user_time_raw,
            time_proposal=criteria.get("time_proposal")
            if isinstance(criteria.get("time_proposal"), dict)
            else None,
            temporal=criteria.get("temporal")
            if isinstance(criteria.get("temporal"), dict)
            else None,
        )

        offers = [dict(item) for item in items if isinstance(item, Mapping)]
        return _match_offers(
            offers,
            user_time_norm=user_time_norm,
            expected_date=expected_date,
            staff=str(staff) if staff else None,
            location=str(location) if location else None,
            allow_clock_face_match=allow_clock_face,
        )


def _criteria(request: SelectionRequest) -> Dict[str, Any]:
    raw = request.get("criteria")
    return dict(raw) if isinstance(raw, dict) else {}


def selection_mode_for_request(request: SelectionRequest) -> str:
    """Expose availability selection mode for bridge short-circuits."""
    criteria = _criteria(request)
    return classify_selection_mode(
        user_facts=criteria.get("user_facts"),
        time_proposal=criteria.get("time_proposal"),
        temporal=criteria.get("temporal"),
        session_state=criteria.get("session_state"),
    )
