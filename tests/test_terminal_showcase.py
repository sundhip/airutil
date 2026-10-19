"""Unit and integration tests for Phase 6B: Docker Terminal Showcase."""

import json
import secrets
import pytest
from app.main import app
from app.db import init_db, get_db_cursor
from app.session import SessionManager
from app.showcase.engine import (
    validate_command,
    check_rate_limit,
    ShowcaseEngine,
)


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


def test_command_whitelist_validation():
    """Verify that only whitelisted commands are accepted and unsafe tokens are rejected."""
    # Valid commands
    assert validate_command("docker ps")[0] is True
    assert validate_command("docker stats --no-stream")[0] is True
    assert validate_command("docker inspect airutil_app1")[0] is True
    assert validate_command("docker network inspect vault_net")[0] is True
    assert validate_command("os ns")[0] is True
    assert validate_command("os cgroup")[0] is True
    assert validate_command("demo balance")[0] is True
    assert validate_command("demo ttl")[0] is True
    assert validate_command("help")[0] is True

    # Rejection of dangerous metacharacters and tokens
    assert validate_command("docker ps; rm -rf /")[0] is False
    assert validate_command("docker ps && ls")[0] is False
    assert validate_command("docker ps | grep app")[0] is False
    assert validate_command("cat /etc/passwd > /tmp/out")[0] is False
    assert validate_command("docker ps $(whoami)")[0] is False
    assert validate_command("docker ps `whoami`")[0] is False
    assert validate_command("bash -c 'echo pwned'")[0] is False
    assert validate_command("python -c 'import os; os.system(\"ls\")'")[0] is False


def test_rate_limiter():
    """Verify in-memory sliding window rate limiter via Redis."""
    user_id = secrets.randbelow(90000) + 10000
    allowed, wait_sec = check_rate_limit(user_id)
    assert allowed is True
    assert wait_sec == 0


def test_showcase_engine_demo_flows():
    """Verify ShowcaseEngine executes demo and help commands properly."""
    engine = ShowcaseEngine(user_id=1, client_ip="127.0.0.1")

    # Help output
    help_chunks = list(engine.run_command_stream("help"))
    help_text = "".join(help_chunks)
    assert "AIRUTIL DOCKER TERMINAL SHOWCASE" in help_text
    assert "docker ps" in help_text

    # Demo balance output
    balance_chunks = list(engine.run_command_stream("demo balance"))
    balance_text = "".join(balance_chunks)
    assert "ROUND-ROBIN" in balance_text.upper() or "Requests" in balance_text

    # Demo ttl output
    ttl_chunks = list(engine.run_command_stream("demo ttl"))
    ttl_text = "".join(ttl_chunks)
    assert "REDIS" in ttl_text.upper() or "TTL" in ttl_text


def test_terminal_access_control(client):
    """Verify authentication and authorization for /terminal."""
    # 1. Unauthenticated -> Redirect to login
    res = client.get("/terminal")
    assert res.status_code == 302
    assert "/login" in res.headers.get("Location", "")

    # 2. Authenticated non-admin (simulate user with id=999)
    sm = SessionManager()
    session_id = sm.create_session(
        user_id=999,
        username="student_regular",
        email="regular@school.edu"
    )
    client.set_cookie("airutil_session", session_id)

    res_student = client.get("/terminal")
    # Non-admin student must be forbidden (403)
    assert res_student.status_code == 403


def test_terminal_admin_access(client):
    """Verify that an admin user can access /terminal."""
    with get_db_cursor(commit=True) as cur:
        cur.execute("SELECT id, username, email FROM users ORDER BY id ASC LIMIT 1;")
        first_user = cur.fetchone()
        if not first_user:
            cur.execute("""
                INSERT INTO users (username, email, password_hash)
                VALUES ('admin_showcase', 'admin@school.edu', '$argon2id$v=19$m=65536,t=3,p=4$fakehash')
                RETURNING id, username, email;
            """)
            first_user = cur.fetchone()

    sm = SessionManager()
    admin_session_id = sm.create_session(
        user_id=first_user["id"],
        username=first_user["username"],
        email=first_user["email"]
    )
    client.set_cookie("airutil_session", admin_session_id)

    res_admin = client.get("/terminal")
    assert res_admin.status_code == 200
    assert b"Docker Terminal Showcase" in res_admin.data
    assert b"xterm.js" in res_admin.data or b"xterm" in res_admin.data


def test_terminal_run_api_validation(client):
    """Verify API input validation and SSE streaming headers."""
    with get_db_cursor() as cur:
        cur.execute("SELECT id, username, email FROM users ORDER BY id ASC LIMIT 1;")
        admin_user = cur.fetchone()

    sm = SessionManager()
    admin_session_id = sm.create_session(
        user_id=admin_user["id"],
        username=admin_user["username"],
        email=admin_user["email"]
    )
    client.set_cookie("airutil_session", admin_session_id)

    csrf_token = sm.get_csrf_token(admin_session_id)

    # Missing command
    res_bad = client.post("/api/terminal/run", json={}, headers={"X-CSRFToken": csrf_token})
    assert res_bad.status_code == 400

    # Whitelisted command returns SSE stream
    res_ok = client.post("/api/terminal/run", json={"command": "help"}, headers={"X-CSRFToken": csrf_token})
    assert res_ok.status_code == 200
    assert "text/event-stream" in res_ok.content_type
    assert "X-Accel-Buffering" in res_ok.headers


def test_internal_oslab_probe(client):
    """Verify that /internal/oslab requires valid internal bearer token."""
    # Without token
    res_no_token = client.get("/internal/oslab")
    assert res_no_token.status_code == 403

    # With invalid token
    res_bad_token = client.get("/internal/oslab", headers={"X-Internal-Token": "wrong-token"})
    assert res_bad_token.status_code == 403

    # With valid token
    import os
    valid_token = os.environ.get("INTERNAL_API_TOKEN", "airutil_internal_telemetry_token_64bytes_production")
    res_valid = client.get("/internal/oslab", headers={"X-Internal-Token": valid_token})
    assert res_valid.status_code == 200
    data = json.loads(res_valid.data)
    assert data["status"] == "ok"
    assert "replica" in data
    assert "output" in data
