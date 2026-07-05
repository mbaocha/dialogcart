"""Shared fixtures for core REST API tests."""

import pytest
from fastapi.testclient import TestClient

from core.orchestration.api.main import app


@pytest.fixture
def api_client():
    return TestClient(app)
