"""Unit and integration tests for Phase 8: Security Hardening & Resilience."""

import os
import json
import secrets
import pytest
from app.main import app
from app.security import validate_production_secrets
from app.db import init_db, get_db_cursor, check_db_health, is_user_admin
from app.session import SessionManager, check_redis_health


@pytest.fixture(autouse=True)
def setup_database():
    """Ensure schema exists before tests."""
    init_db()


@pytest.fixture
def client():
    """Create Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_production_secrets_validator():
    """Verify that validate_production_secrets() blocks insecure keys in production."""
    # In testing/development mode, it should not raise
    os.environ["FLASK_ENV"] = "development"
    os.environ["AIRUTIL_ENV"] = "development"
    os.environ["SECRET_KEY"] = "dev-secret-key-change-in-production-2026"
    is_secure, warnings = validate_production_secrets(strict=False)
    assert isinstance(is_secure, bool)

    # In production mode with default secret, it must raise RuntimeError
    os.environ["FLASK_ENV"] = "production"
    os.environ["SECRET_KEY"] = "airutil_super_secret_session_key_production_grade_32bytes"
    with pytest.raises(RuntimeError) as exc_info:
        validate_production_secrets()
    assert "Insecure secrets detected in production" in str(exc_info.value)

    # Restoring development mode
    os.environ["FLASK_ENV"] = "development"


def test_health_checks():
    """Verify database and Redis health check functions return healthy status."""
    assert check_db_health() is True
    assert check_redis_health() is True


def test_is_user_admin():
    """Verify admin detection: min(id) is admin, higher id is student."""
    with get_db_cursor(commit=True) as cur:
        cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1;")
        first_user = cur.fetchone()
        if not first_user:
            cur.execute("""
                INSERT INTO users (username, email, password_hash)
                VALUES ('admin_test', 'admin_test@school.edu', '$argon2id$v=19$m=65536,t=3,p=4$fakehash')
                RETURNING id;
            """)
            first_user = cur.fetchone()

    admin_id = first_user["id"]
    assert is_user_admin(admin_id) is True
    assert is_user_admin(admin_id + 9999) is False


def test_error_handlers(client):
    """Verify 404 handler returns clean JSON for API and HTML for web requests."""
    # API 404
    api_res = client.get("/api/nonexistent_route")
    assert api_res.status_code == 404
    data = json.loads(api_res.data)
    assert data["status"] == "error"
    assert "not found" in data["error"].lower()

    # Web 404
    web_res = client.get("/nonexistent_page")
    assert web_res.status_code == 404
    assert b"404 - Node Not Found" in web_res.data


def test_pwa_and_offline_endpoints(client):
    """Verify manifest.json, sw.js, and /offline respond correctly."""
    # Manifest
    manifest_res = client.get("/manifest.json")
    assert manifest_res.status_code == 200
    manifest_data = json.loads(manifest_res.data)
    assert "Airutil" in manifest_data["name"]
    assert manifest_data["short_name"] == "Airutil"
    assert manifest_data["display"] == "standalone"

    # Service worker
    sw_res = client.get("/sw.js")
    assert sw_res.status_code == 200
    assert "javascript" in sw_res.content_type
    assert b"airutil-shell-v1" in sw_res.data

    # Offline shell page
    offline_res = client.get("/offline")
    assert offline_res.status_code == 200
    assert b"You are currently offline" in offline_res.data


def test_settings_page(client):
    """Verify settings page renders storage quota and user profile."""
    # Create test session
    with get_db_cursor() as cur:
        cur.execute("SELECT id, username, email FROM users ORDER BY id ASC LIMIT 1;")
        user = cur.fetchone()

    sm = SessionManager()
    session_id = sm.create_session(
        user_id=user["id"],
        username=user["username"],
        email=user["email"]
    )
    client.set_cookie("airutil_session", session_id)

    res = client.get("/settings")
    assert res.status_code == 200
    assert b"Settings &amp; Preferences" in res.data or b"Settings & Preferences" in res.data
    assert b"Storage Quota (500 MB)" in res.data
    assert user["username"].encode() in res.data


def test_vault_user_isolation(client):
    """Verify that User A cannot delete or access User B's files."""
    # Create User A and User B
    with get_db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO users (username, email, password_hash)
            VALUES ('user_alice', 'alice@school.edu', 'hash_alice')
            ON CONFLICT (username) DO UPDATE SET email = EXCLUDED.email
            RETURNING id;
        """)
        user_a_id = cur.fetchone()["id"]

        cur.execute("""
            INSERT INTO users (username, email, password_hash)
            VALUES ('user_bob', 'bob@school.edu', 'hash_bob')
            ON CONFLICT (username) DO UPDATE SET email = EXCLUDED.email
            RETURNING id;
        """)
        user_b_id = cur.fetchone()["id"]

        # Insert a file belonging to Alice
        cur.execute("""
            INSERT INTO files (user_id, filename, original_name, storage_name, file_path, file_size, mime_type, category, course)
            VALUES (%s, 'alice_secret.pdf', 'alice_secret.pdf', 'alice_stored.pdf', '/vault_storage/alice_secret.pdf', 1024, 'application/pdf', 'Documents', 'CSE101')
            RETURNING id;
        """, (user_a_id,))
        file_a_id = cur.fetchone()["id"]

    # Log in as Bob
    sm = SessionManager()
    bob_session = sm.create_session(
        user_id=user_b_id,
        username="user_bob",
        email="bob@school.edu"
    )
    client.set_cookie("airutil_session", bob_session)

    # Bob tries to download Alice's file -> 404 (isolated by user_id)
    dl_res = client.get(f"/files/download/{file_a_id}")
    assert dl_res.status_code == 404

    # Bob tries to delete Alice's file -> 404
    csrf_token = sm.get_csrf_token(bob_session)
    del_res = client.post(f"/files/delete/{file_a_id}", data={"csrf_token": csrf_token})
    assert del_res.status_code == 404
