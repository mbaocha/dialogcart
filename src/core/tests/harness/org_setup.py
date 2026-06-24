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
    Pre-populate org domain cache so orchestrator uses the scenario domain.

    Args:
        domain: "service" or "reservation"
    """
    from core.orchestration.cache.org_domain_cache import org_domain_cache

    business_category_id = 1 if domain == "service" else 2
    test_org_id = int(os.getenv("ORG_ID", "1"))
    org_domain_cache._mem_set(
        test_org_id, {"domain": domain, "businessCategoryId": business_category_id}
    )
