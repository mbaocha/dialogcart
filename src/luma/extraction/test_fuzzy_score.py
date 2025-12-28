#!/usr/bin/env python3
"""Quick test to check fuzzy matching score."""

try:
    from rapidfuzz import fuzz
    score = fuzz.token_sort_ratio("premium suite", "premum suite")
    print(f"Fuzzy score for 'premium suite' vs 'premum suite': {score}")
    print(f"Score >= 90: {score >= 90}")
except ImportError:
    print("rapidfuzz not available")

