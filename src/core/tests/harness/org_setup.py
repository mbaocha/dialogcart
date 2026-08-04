"""Organization and customer setup helpers for core tests."""

import os
from typing import Any, Dict, Optional


def get_customer_details() -> Dict[str, Optional[Any]]:
    """Load customer details from environment variables."""
    phone_number = os.getenv("TEST_CUSTOMER_PHONE")
    email = os.getenv("TEST_CUSTOMER_EMAIL")
    customer_id_str = os.getenv("TEST_CUSTOMER_ID")
    customer_id = int(customer_id_str) if customer_id_str else None

    return {"phone_number": phone_number, "email": email, "customer_id": customer_id}


def setup_test_org_domain(domain: str) -> None:
    """
    Pre-populate org cache so orchestrator uses the scenario booking domain.

    Args:
        domain: booking_domain ``service`` or ``reservation``
            (maps to default categories beauty_salon / hotel).
    """
    from core.adapters.cache.org_domain_cache import org_domain_cache

    if domain == "service":
        business_category = "beauty_salon"
        business_category_id = 1
    else:
        business_category = "hotel"
        business_category_id = 2

    test_org_id = int(os.getenv("ORG_ID", "1"))
    org_domain_cache._mem_set(
        test_org_id,
        {
            "business_category": business_category,
            "booking_domain": domain,
            "domain": domain,
            "businessCategoryId": business_category_id,
        },
    )


def setup_test_org_category(business_category: str) -> None:
    """Pre-populate org cache for an explicit business category."""
    from core.adapters.cache.org_domain_cache import org_domain_cache
    from core.adapters.cache.org_domain_cache import BUSINESS_CATEGORY_IDS
    from core.config.business_category_loader import get_booking_domain

    booking_domain = get_booking_domain(business_category)
    if not booking_domain:
        raise ValueError(f"Unknown business_category={business_category!r}")

    # Prefer stable numeric ids used by mocks when available.
    business_category_id: Any = business_category
    for key, name in BUSINESS_CATEGORY_IDS.items():
        if name == business_category and isinstance(key, int):
            business_category_id = key
            break

    test_org_id = int(os.getenv("ORG_ID", "1"))
    org_domain_cache._mem_set(
        test_org_id,
        {
            "business_category": business_category,
            "booking_domain": booking_domain,
            "domain": booking_domain,
            "businessCategoryId": business_category_id,
        },
    )
