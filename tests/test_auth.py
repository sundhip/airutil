"""Unit & Integration Tests for Phase 2: Authentication, Sessions & Security."""

import json
import secrets
import pytest
from app.main import app
from app.security import hash_password, verify_password, validate_registration_input
from app.session import SessionManager, get_redis_client
from app.db import get_db_cursor, init_db


@pytest.fixture(autouse=True)
def setup_database():
    """Ensure schema exists before tests."""
    init_db()


@pytest.fixture
def client():
    """Create test client."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_argon2_hashing():
    """Verify Argon2id password hashing and verification."""
    password = "SuperSecurePassword123!"
    hash_str = hash_password(password)
    assert hash_str.startswith("$argon2id$")
    assert verify_password(hash_str, password) is True
    assert verify_password(hash_str, "WrongPassword") is False


def test_registration_validation():
    """Verify registration input validation rules."""
    assert validate_registration_input("ab", "test@test.com", "validpassword") is not None
    assert validate_registration_input("valid_user", "invalid-email", "validpassword") is not None
    assert validate_registration_input("valid_user", "test@test.com", "short") is not None
    assert validate_registration_input("valid_user", "test@test.com", "validpassword123") is None


def test_registration_flow(client):
    """Test full registration and automatic session creation."""
    username = f"testuser_{secrets.token_hex(4)}"
    email = f"{username}@university.edu"
    password = "StrongPassword2026!"

    # Get CSRF token from registration page
    get_res = client.get("/register")
    assert get_res.status_code == 200

    # Extract CSRF token cookie or generate matching
    anon_csrf = client.get_cookie("airutil_anon_csrf")
    csrf_val = anon_csrf.value if anon_csrf else ""

    post_res = client.post("/register", data={
        "username": username,
        "email": email,
        "password": password,
        "csrf_token": csrf_val
    }, follow_redirects=True)

    assert post_res.status_code == 200
    assert bytes(username, "utf-8") in post_res.data

    # Verify password hash in PostgreSQL is an Argon2id hash
    with get_db_cursor(commit=False) as cur:
        cur.execute("SELECT password_hash FROM users WHERE username = %s", (username,))
        row = cur.fetchone()
        assert row is not None
        assert row["password_hash"].startswith("$argon2id$")


def test_csrf_protection_enforcement(client):
    """Verify that POST without valid CSRF token is rejected with 403 Forbidden."""
    res = client.post("/login", data={
        "login_id": "anyuser",
        "password": "anypassword",
        "csrf_token": "invalid_or_missing_csrf_token"
    })
    assert res.status_code == 403


def test_login_rate_limiting(client):
    """Verify that 6 failed login attempts trigger Redis rate limiting (429)."""
    # Prime anonymous CSRF token
    client.get("/login")
    anon_csrf = client.get_cookie("airutil_anon_csrf")
    csrf_val = anon_csrf.value if anon_csrf else ""

    test_ip_user = f"rate_target_{secrets.token_hex(3)}"

    # Attempt 5 failed logins
    for i in range(5):
        res = client.post("/login", data={
            "login_id": test_ip_user,
            "password": "WrongPassword!",
            "csrf_token": csrf_val
        })
        assert res.status_code == 401

    # 6th attempt must trigger rate limit (429 Too Many Requests)
    res6 = client.post("/login", data={
        "login_id": test_ip_user,
        "password": "WrongPassword!",
        "csrf_token": csrf_val
    })
    assert res6.status_code == 429
    assert b"Rate limit exceeded" in res6.data


def test_redis_session_storage_and_logout(client):
    """Verify session is stored in Redis under session:<id> and destroyed on logout."""
    username = f"session_user_{secrets.token_hex(4)}"
    email = f"{username}@test.edu"
    password = "ValidPassword123!"

    client.get("/register")
    anon_csrf = client.get_cookie("airutil_anon_csrf")
    csrf_val = anon_csrf.value if anon_csrf else ""

    # Register user
    client.post("/register", data={
        "username": username,
        "email": email,
        "password": password,
        "csrf_token": csrf_val
    })

    # Retrieve session cookie
    session_cookie = client.get_cookie("airutil_session")
    assert session_cookie is not None
    session_id = session_cookie.value

    # Check key in Redis
    r = get_redis_client()
    redis_data = r.get(f"session:{session_id}")
    assert redis_data is not None
    parsed = json.loads(redis_data)
    assert parsed["username"] == username

    # Access protected dashboard
    dash_res = client.get("/dashboard")
    assert dash_res.status_code == 200
    assert bytes(username, "utf-8") in dash_res.data

    # Log out
    user_csrf = parsed.get("csrf_token", "")
    logout_res = client.post("/logout", data={"csrf_token": user_csrf}, follow_redirects=True)
    assert logout_res.status_code == 200

    # Verify session is destroyed in Redis
    assert r.get(f"session:{session_id}") is None
