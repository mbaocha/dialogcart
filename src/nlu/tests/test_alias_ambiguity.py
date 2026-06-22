"""
Unit tests for _resolve_alias_ambiguity — cases 83+.

Run via: python -m nlu.tests.test_luma 83
Or:      python -m pytest nlu/tests/test_alias_ambiguity.py
"""
import pytest

from .alias_ambiguity_scenarios import alias_ambiguity_scenarios
from .test_luma import assert_alias_ambiguity, resolve_alias_ambiguity_case


@pytest.mark.parametrize(
    "case_index",
    range(len(alias_ambiguity_scenarios)),
    ids=[str(83 + i) for i in range(len(alias_ambiguity_scenarios))],
)
def test_alias_ambiguity_scenario(case_index):
    case = alias_ambiguity_scenarios[case_index]
    result = resolve_alias_ambiguity_case(case)
    assert_alias_ambiguity(case, result)
