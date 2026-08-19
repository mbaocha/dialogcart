"""
Turn planner — canonical staged planning pipeline entry.

Delegates to core.planning.pipeline.run_planning_pipeline.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from core.adapters.cache.catalog_cache import catalog_cache
from core.adapters.cache.org_domain_cache import org_domain_cache
from core.adapters.clients.catalog_client import CatalogClient
from core.adapters.clients.organization_client import OrganizationClient
from core.adapters.nlu import LumaClient
from core.adapters.nlu.catalog_projection import (
    aliases_from_projection,
    project_catalog_collections,
)
from core.adapters.nlu.entity_schema_builder import build_entity_schema
from core.config.business_category_loader import (
    get_catalog_collection_keys,
    is_configured_category,
)
from core.catalogue import derive_service_catalogue, nlu_catalog_context
from core.planning.pipeline.orchestrator import run_planning_pipeline

logger = logging.getLogger(__name__)
turn_logger = logging.getLogger("core.turn_log")


def _build_tenant_context_from_projection(
    booking_domain: str,
    projected_collections: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Build tenant_context; aliases are a compatibility view of the projection."""
    tenant_context: Dict[str, Any] = {"booking_mode": booking_domain}
    alias_map = aliases_from_projection(projected_collections)
    if alias_map:
        tenant_context["aliases"] = alias_map
    return tenant_context


def build_nlu_request_context(
    organization_id: int,
    business_category: str,
    booking_domain: str,
    catalog_client: CatalogClient,
    session_state: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Build tenant_context and entity_schema from one catalog projection.

    ``business_category`` owns the entity schema and which catalog collections
    to project. ``booking_domain`` selects the catalog API endpoint.
    """
    if not is_configured_category(business_category):
        return None, None
    catalog_data = catalog_cache.get_catalog(
        organization_id, catalog_client, domain=booking_domain
    )
    collection_keys = get_catalog_collection_keys(business_category)
    projected = project_catalog_collections(
        catalog_data,
        collection_keys=collection_keys or None,
    )
    tenant_context = _build_tenant_context_from_projection(booking_domain, projected)
    if booking_domain == "service":
        # Additive structured catalogue evidence.  The legacy aliases remain for
        # older NLU deployments and flat-catalogue behaviour.
        service_catalogue = derive_service_catalogue(catalog_data.get("services"))
        if service_catalogue.services and any(
            service.description is not None or service.category is not None
            for service in service_catalogue.services
        ):
            tenant_context["catalog"] = nlu_catalog_context(service_catalogue)
    entity_schema = build_entity_schema(
        business_category,
        catalog_data=catalog_data,
        projected_collections=projected,
    )
    if isinstance(session_state, dict):
        planning = session_state.get("planning")
        pending = planning.get("pending_profile_request") if isinstance(planning, dict) else None
        contact = session_state.get("customer_contact")
        contact_name_revision_available = (
            session_state.get("confirmation_state") == "pending"
            and isinstance(contact, dict)
            and contact.get("name_status") == "authoritative"
            and isinstance(contact.get("authoritative_name"), str)
            and bool(contact.get("authoritative_name").strip())
        )
        if pending == "CUSTOMER_CONTACT_NAME" or contact_name_revision_available:
            from core.adapters.nlu.entity_schema_builder import with_customer_contact_name_request
            entity_schema = with_customer_contact_name_request(entity_schema)
    return tenant_context, entity_schema


def plan_turn(
    user_id: str,
    text: str,
    organization_id: int,
    timezone: str = "UTC",
    phone_number: Optional[str] = None,
    email: Optional[str] = None,
    customer_id: Optional[int] = None,
    luma_client: Optional[LumaClient] = None,
    catalog_client: Optional[CatalogClient] = None,
    organization_client: Optional[OrganizationClient] = None,
    verbose: bool = False,
    session_state: Optional[Dict[str, Any]] = None,
    transaction_id: Optional[str] = None,
    planning_only: bool = False,
    apply_domain_filter: bool = True,
) -> Dict[str, Any]:
    """Run one planning turn through the canonical staged pipeline."""
    _ = (phone_number, email, customer_id, verbose)

    if luma_client is None:
        luma_client = LumaClient()
    if catalog_client is None:
        catalog_client = CatalogClient()
    if organization_client is None:
        organization_client = OrganizationClient()

    business_category, booking_domain, _ = org_domain_cache.resolve(
        organization_id, organization_client, force_refresh=False
    )
    tenant_context, entity_schema = build_nlu_request_context(
        organization_id, business_category, booking_domain, catalog_client, session_state
    )

    return run_planning_pipeline(
        user_id=user_id,
        text=text,
        organization_id=organization_id,
        derived_domain=booking_domain,
        timezone=timezone,
        tenant_context=tenant_context,
        entity_schema=entity_schema,
        session_state=session_state,
        luma_client=luma_client,
        organization_client=organization_client,
        transaction_id=transaction_id,
        planning_only=planning_only,
        apply_domain_filter=apply_domain_filter,
    )
