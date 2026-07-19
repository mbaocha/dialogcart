"""Search — obtain and trust discovery results.

Responsible for:
- obtaining trusted results
- defining search identity (via SearchProvider)
- deciding trust / reuse of TrustedResult
- invoking the provider only when necessary

Not responsible for:
- presentation
- navigation
- selection
- planner policy
"""

from core.discovery.search.engine import Search
from core.discovery.search.provider import SearchProvider

__all__ = [
    "Search",
    "SearchProvider",
]
