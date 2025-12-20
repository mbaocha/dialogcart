import pytest

from .normalization import post_normalize_parameterized_text


def test_post_normalize_preserves_servicetokenfamily():
    text = "book me in for servicetokenfamily datetoken at timetoken"
    normalized = post_normalize_parameterized_text(text)
    assert "servicetokenfamily" in normalized
    assert "servicetoken " not in normalized
    assert normalized.count("servicetokenfamily") == 1

