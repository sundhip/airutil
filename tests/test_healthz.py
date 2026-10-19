"""Unit tests for Airutil application endpoints."""

import json
import pytest
from app.main import app


@pytest.fixture
def client():
    """Create Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_healthz_endpoint(client):
    """Test that /healthz returns 200 and healthy JSON payload."""
    res = client.get("/healthz")
    assert res.status_code == 200
    data = json.loads(res.data)
    assert data["status"] == "healthy"
    assert data["service"] == "airutil"
    assert "replica" in data
    assert "timestamp" in data


def test_index_endpoint(client):
    """Test that / returns 200 HTML content with branding and replica info."""
    res = client.get("/")
    assert res.status_code == 200
    assert b"Airutil" in res.data
    assert b"Student Vault &amp; High-Performance File Beam" in res.data or b"Student Vault" in res.data
