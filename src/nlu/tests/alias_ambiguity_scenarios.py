# Alias ambiguity unit scenarios (cases 83–85).
# Run via: python -m nlu.tests.test_luma 83
# Or:      python -m pytest nlu/tests/test_alias_ambiguity.py
#
# These test catalog.resolve_service in isolation (no HTTP / Haiku).
# Cases 81–82 are integration scenarios in booking_scenarios.py.
#
# service_term is the raw LLM-extracted phrase (with typos preserved if any).
# The catalog resolver is responsible for fuzzy-matching it against the alias catalog.

alias_ambiguity_scenarios = [
    {
        # 83 — exact alias key: LLM extracted "massage"; must not map to "swedish massage"
        "text": "book massage tomorrow at 10am",
        "service_term": "massage",
        "aliases": {"massage": "massage", "swedish massage": "wellness.swedish"},
        "expected": "massage",
    },
    {
        # 84 — generic term ties across variants → null (complements integration case 82)
        "text": "book haircut tomorrow at 2pm",
        "service_term": "haircut",
        "aliases": {
            "premium haircut": "beauty.premium",
            "standard haircut": "beauty.standard",
        },
        "expected": None,
    },
    {
        # 85 — multi-word exact match: LLM extracted "deep tissue massage"
        "text": "book deep tissue massage tomorrow",
        "service_term": "deep tissue massage",
        "aliases": {
            "swedish massage": "wellness.swedish",
            "deep tissue massage": "wellness.deep_tissue",
        },
        "expected": "deep tissue massage",
    },
]
