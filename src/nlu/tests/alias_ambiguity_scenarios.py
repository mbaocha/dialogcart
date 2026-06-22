# Alias ambiguity unit scenarios (cases 83–85).
# Run via: python -m nlu.tests.test_luma 83
# Or:      python -m pytest nlu/tests/test_alias_ambiguity.py
#
# These test _resolve_alias_ambiguity in isolation (no HTTP / Haiku).
# Cases 81–82 are integration scenarios in booking_scenarios.py.

alias_ambiguity_scenarios = [
    {
        # 83 — exact alias key: correct Haiku over-pick, must not false-null
        "text": "book massage tomorrow at 10am",
        "haiku_service_id": "swedish massage",
        "aliases": {"massage": "massage", "swedish massage": "wellness.swedish"},
        "expected": "massage",
    },
    {
        # 84 — generic term ties across variants → null (complements integration case 82)
        "text": "book haircut tomorrow at 2pm",
        "haiku_service_id": "premium haircut",
        "aliases": {
            "premium haircut": "beauty.premium",
            "standard haircut": "beauty.standard",
        },
        "expected": None,
    },
    {
        # 85 — multi-word exact match: correct to full alias key
        "text": "book deep tissue massage tomorrow",
        "haiku_service_id": "swedish massage",
        "aliases": {
            "swedish massage": "wellness.swedish",
            "deep tissue massage": "wellness.deep_tissue",
        },
        "expected": "deep tissue massage",
    },
]
