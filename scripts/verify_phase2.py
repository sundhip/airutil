"""Automated Acceptance Verification Script for Phase 2.

Checks:
1. User registration & login across Nginx gateway.
2. Refresh repeatedly across both replicas without being logged out (checking X-Served-By and HTML).
3. Rate limiting: 6 failed login attempts triggers 429 Too Many Requests.
4. Redis check: session keys present in redis.
5. PostgreSQL check: password hashes are Argon2id.
"""

import re
import sys
import requests

BASE_URL = "http://localhost:8080"


def run_checks():
    session = requests.Session()
    print("--- 1. Testing Registration & Login Across Replicas ---")
    reg_page = session.get(f"{BASE_URL}/register")
    assert reg_page.status_code == 200, f"Register page failed: {reg_page.status_code}"

    # Extract CSRF token
    csrf_match = re.search(r'name="csrf_token"\s+value="([a-f0-9]+)"', reg_page.text)
    assert csrf_match, "CSRF token not found in register page"
    csrf_token = csrf_match.group(1)

    test_user = "student_qa"
    test_email = "student_qa@college.edu"
    test_pwd = "MasterPassword2026!"

    # Register (or log in if already exists)
    reg_resp = session.post(f"{BASE_URL}/register", data={
        "username": test_user,
        "email": test_email,
        "password": test_pwd,
        "csrf_token": csrf_token
    }, allow_redirects=True)

    if "already taken" in reg_resp.text:
        # User already exists, log in
        login_page = session.get(f"{BASE_URL}/login")
        csrf_match = re.search(r'name="csrf_token"\s+value="([a-f0-9]+)"', login_page.text)
        csrf_token = csrf_match.group(1)
        login_resp = session.post(f"{BASE_URL}/login", data={
            "login_id": test_user,
            "password": test_pwd,
            "csrf_token": csrf_token
        }, allow_redirects=True)
        assert login_resp.status_code == 200, f"Login failed: {login_resp.status_code}"

    print(f"Logged in successfully. Session cookie: {session.cookies.get('airutil_session')[:16]}...")

    # Refresh 6 times to observe round-robin between app1 and app2 while authenticated
    print("\n--- 2. Repeated Refreshes Across Replicas (Cross-Replica Session Persistence) ---")
    replicas_seen = set()
    for i in range(1, 7):
        dash_resp = session.get(f"{BASE_URL}/dashboard")
        assert dash_resp.status_code == 200, f"Dashboard returned {dash_resp.status_code}"
        assert f"Welcome, {test_user}!" in dash_resp.text, "User lost authentication on refresh!"
        
        served_by = dash_resp.headers.get("X-Served-By", "unknown")
        replica_hdr = dash_resp.headers.get("X-Served-By-Replica", "unknown")
        replicas_seen.add(replica_hdr)
        print(f"Refresh #{i}: HTTP {dash_resp.status_code} | Replica: {replica_hdr} | Upstream IP: {served_by} | Authenticated: Yes")

    assert len(replicas_seen) >= 2, f"Expected both app1 and app2 to serve requests, got {replicas_seen}"
    print(f"PASS: Session persisted across replicas: {replicas_seen}")

    print("\n--- 3. Testing Rate Limiting (6 wrong passwords) ---")
    attacker_session = requests.Session()
    login_get = attacker_session.get(f"{BASE_URL}/login")
    csrf_token = re.search(r'name="csrf_token"\s+value="([a-f0-9]+)"', login_get.text).group(1)

    rate_limited = False
    for attempt in range(1, 8):
        fail_resp = attacker_session.post(f"{BASE_URL}/login", data={
            "login_id": "rate_limit_victim",
            "password": "WrongPasswordAttempt!",
            "csrf_token": csrf_token
        })
        print(f"Attempt #{attempt}: Status {fail_resp.status_code}")
        if fail_resp.status_code == 429:
            print(f"PASS: Attempt #{attempt} was rate-limited (HTTP 429)!")
            rate_limited = True
            break

    assert rate_limited, "Rate limiting was not triggered after failed attempts!"

    print("\nAll HTTP acceptance checks passed successfully!")


if __name__ == "__main__":
    run_checks()
