from unittest.mock import patch

from core.adapters.nlu.conversation_memory import build_conversation_context
from core.catalogue import build_presentation, derive_service_catalogue, nlu_catalog_context
from core.planning.pipeline.stage01_intent import reconcile_intent
from core.session.session_schema_v2 import hydrate_v1_compat_shims, normalize_session_to_v2
from core.session.persist import _apply_catalogue_presentation_to_session


SERVICES = [
    {"id": "1001", "name": "Premium Haircut", "category": "Hair"},
    {"id": "2001", "name": "Manicure", "category": "Nails"},
]


def _tenant_context(catalogue):
    return {"booking_mode": "service", "catalog": nlu_catalog_context(catalogue)}


def test_list_categories_routes_before_generic_handler_delegation():
    response = {
        "intent": {"name": "GENERAL_INQUIRY"},
        "operation": "list_service_categories",
        "facts": {"service_id": None},
    }
    with patch(
        "core.planning.pipeline.stage01_intent.resolve_effective_intent",
        return_value=("GENERAL_INQUIRY", False),
    ):
        decision, _ = reconcile_intent(
            luma_response=response,
            session_state=None,
            user_id="user",
            organization_id=1,
        )
    assert decision.raw_luma_intent == "GENERAL_INQUIRY"
    assert decision.planning_intent == "CREATE_APPOINTMENT"
    assert decision.turn_operation == "INFORMATIONAL"
    assert decision.handler_delegated is False


def test_only_current_well_formed_presentation_is_supplied_to_nlu():
    catalogue = derive_service_catalogue(SERVICES)
    presentation = build_presentation(catalogue, kind="category")
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "missing_slots": ["service_id"],
        "catalogue_presentation": presentation,
    }
    context = build_conversation_context(
        session, tenant_context=_tenant_context(catalogue)
    )
    assert context["catalogue_presentation"] == presentation

    changed = derive_service_catalogue(SERVICES[:-1])
    stale = build_conversation_context(
        session, tenant_context=_tenant_context(changed)
    )
    assert "catalogue_presentation" not in stale

    malformed_session = {**session, "catalogue_presentation": {"kind": "service"}}
    malformed = build_conversation_context(
        malformed_session, tenant_context=_tenant_context(catalogue)
    )
    assert "catalogue_presentation" not in malformed


def test_session_v2_round_trip_preserves_presentation_outside_booking_slots():
    catalogue = derive_service_catalogue(SERVICES)
    presentation = build_presentation(catalogue, kind="category")
    normalized = normalize_session_to_v2({
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {},
        "missing_slots": ["service_id"],
        "catalogue_presentation": presentation,
    })
    assert normalized["planning"]["catalogue_presentation"] == presentation
    assert "catalogue_presentation" not in normalized["planning"]["slots"]
    hydrated = hydrate_v1_compat_shims(normalized)
    assert hydrated["catalogue_presentation"] == presentation
    assert hydrated["slots"] == {}


def test_session_without_presentation_remains_backward_compatible():
    normalized = normalize_session_to_v2({
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {},
        "missing_slots": ["service_id"],
    })
    assert normalized["planning"]["catalogue_presentation"] is None


def test_persistence_keeps_presentation_only_while_service_is_missing():
    catalogue = derive_service_catalogue(SERVICES)
    presentation = build_presentation(catalogue, kind="category")
    session = {"missing_slots": ["service_id"]}
    _apply_catalogue_presentation_to_session(
        session, {"_catalogue_presentation": presentation}
    )
    assert session["catalogue_presentation"] == presentation

    session["missing_slots"] = []
    _apply_catalogue_presentation_to_session(session, {})
    assert "catalogue_presentation" not in session


def test_category_revision_replaces_reference_and_old_ordinal_fails():
    catalogue = derive_service_catalogue(SERVICES)
    category_page = build_presentation(catalogue, kind="category")
    hair_page = build_presentation(
        catalogue, kind="service", services=catalogue.category("hair").services
    )
    nails_page = build_presentation(
        catalogue, kind="service", services=catalogue.category("nails").services
    )
    assert hair_page["reference"] != nails_page["reference"]

    from core.catalogue import resolve_presented_selection

    old_selection = {
        "presentation_ref": hair_page["reference"], "kind": "service", "option": 1
    }
    assert resolve_presented_selection(
        old_selection, presentation=nails_page, catalogue=catalogue
    ) is None
    assert category_page["reference"] not in {
        hair_page["reference"], nails_page["reference"]
    }
