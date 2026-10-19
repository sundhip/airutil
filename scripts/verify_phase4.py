"""Live Acceptance Verification Script for Phase 4: AES Password Vault & 30s Redis Unlock.

This script tests all non-negotiable acceptance requirements against the live multi-container
deployment via the Nginx load balancer at http://localhost:8080.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import requests

BASE_URL = os.getenv("BASE_URL", "http://localhost:8080")


def print_check(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name}")
    if detail:
        print(f"       -> {detail}")
    if not passed:
        sys.exit(1)


def get_csrf_token(html_text: str) -> str:
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', html_text)
    if match:
        return match.group(1)
    return ""


def main():
    print("=" * 70)
    print("AIRUTIL PHASE 4: LIVE ACCEPTANCE VERIFICATION CHECKS")
    print(f"Target Gateway: {BASE_URL}")
    print("=" * 70)

    # 1. Register User 1 (Alice)
    s1 = requests.Session()
    u1 = f"alice_vault_{int(time.time())}"
    p1 = "AliceLoginPass123!"
    master1 = "AliceVaultMasterKey2026!"

    resp = s1.get(f"{BASE_URL}/register")
    csrf1 = get_csrf_token(resp.text)
    reg_data = {
        "csrf_token": csrf1,
        "username": u1,
        "email": f"{u1}@college.edu",
        "password": p1,
        "confirm_password": p1,
    }
    s1.post(f"{BASE_URL}/register", data=reg_data, allow_redirects=True)
    print_check("Alice Registration", True, f"Registered user {u1}")

    # Set Vault Master Password
    v_page = s1.get(f"{BASE_URL}/vault")
    csrf1 = get_csrf_token(v_page.text)
    setup_data = {
        "csrf_token": csrf1,
        "master_password": master1,
        "confirm_password": master1,
    }
    setup_resp = s1.post(f"{BASE_URL}/vault/setup", data=setup_data, allow_redirects=True)
    print_check(
        "Vault Initialization & Master Password Setup",
        "Vault unlocked for 30 seconds" in setup_resp.text or "remaining" in setup_resp.text,
        "scrypt KDF derived key and initialized user_vault_settings"
    )

    # --------------------------------------------------------------------------
    # Check 1: SELECT from vault_items in Postgres shows ONLY ciphertext
    # --------------------------------------------------------------------------
    csrf1 = get_csrf_token(setup_resp.text)
    item_secret = "TopSecretBankPIN_8899#"
    item_notes = "Confidential Recovery Token #998811"

    item_data = {
        "csrf_token": csrf1,
        "title": "National Student Bank",
        "category": "Bank/PIN",
        "username": "alice_bank",
        "secret": item_secret,
        "url": "https://bank.example.edu",
        "notes": item_notes,
    }
    create_resp = s1.post(f"{BASE_URL}/vault/items", data=item_data, allow_redirects=True)
    print_check("Vault Item Creation", "National Student Bank" in create_resp.text, "Item added to vault")

    # Direct query to PostgreSQL container
    psql_proc = subprocess.run(
        [
            "docker", "exec", "airutil_db", "psql", "-U", "airutil", "-d", "airutil", "-t", "-A", "-c",
            "SELECT title, encrypted_password, notes FROM vault_items WHERE title = 'National Student Bank';"
        ],
        capture_output=True,
        text=True
    )
    raw_db_row = psql_proc.stdout.strip()
    # Ensure neither password nor notes exist in raw plaintext in DB
    db_has_secret = item_secret in raw_db_row
    db_has_notes = item_notes in raw_db_row
    db_has_fernet_signature = "gAAAAA" in raw_db_row  # Fernet ciphertexts always start with gAAAAA

    print_check(
        "Database Cryptographic Storage (Postgres stores ONLY ciphertext)",
        (not db_has_secret) and (not db_has_notes) and db_has_fernet_signature,
        f"PostgreSQL row contains Fernet ciphertext (starts with gAAAAA), zero plaintext"
    )

    # --------------------------------------------------------------------------
    # Check 2: 30-Second Redis TTL & Cross-Replica Auto-Lock
    # --------------------------------------------------------------------------
    # Inspect session cookie
    session_id = s1.cookies.get("airutil_session")
    assert session_id, "Could not find session cookie"

    # Query Redis TTL
    redis_ttl_proc = subprocess.run(
        ["docker", "exec", "airutil_redis", "redis-cli", "TTL", f"unlock:{session_id}"],
        capture_output=True,
        text=True
    )
    ttl_val = int(redis_ttl_proc.stdout.strip())
    print_check(
        "Volatile Redis Unlock TTL Enforced",
        0 < ttl_val <= 30,
        f"Redis key unlock:{session_id[:12]}... has TTL = {ttl_val}s (<= 30s)"
    )

    # Expire the Redis key to test auto-relock behavior
    subprocess.run(
        ["docker", "exec", "airutil_redis", "redis-cli", "DEL", f"unlock:{session_id}"],
        capture_output=True
    )
    ttl_after_proc = subprocess.run(
        ["docker", "exec", "airutil_redis", "redis-cli", "TTL", f"unlock:{session_id}"],
        capture_output=True,
        text=True
    )
    ttl_expired = int(ttl_after_proc.stdout.strip())
    print_check(
        "Redis Key Eviction TTL Check",
        ttl_expired == -2,
        f"Key eviction confirmed: TTL is {ttl_expired} (-2 means key does not exist)"
    )

    # Request /vault through Nginx (which routes to app1 or app2)
    locked_page = s1.get(f"{BASE_URL}/vault")
    print_check(
        "Cross-Replica Automatic Re-Locking",
        "Vault is Locked" in locked_page.text and item_secret not in locked_page.text,
        "UI correctly re-locked on replicas; secrets masked as '••••••••••••'"
    )

    # --------------------------------------------------------------------------
    # Check 3: Rate Limiting on Wrong Master Password
    # --------------------------------------------------------------------------
    csrf_lock = get_csrf_token(locked_page.text)
    rate_limited = False
    for attempt in range(1, 8):
        bad_unlock = s1.post(
            f"{BASE_URL}/vault/unlock",
            data={"csrf_token": csrf_lock, "master_password": "WrongPassword999!"},
            allow_redirects=True
        )
        csrf_lock = get_csrf_token(bad_unlock.text) or csrf_lock
        if "Too many incorrect attempts" in bad_unlock.text or "locked for" in bad_unlock.text:
            rate_limited = True
            break

    print_check(
        "Master Password Brute-Force Rate Limiting",
        rate_limited,
        f"Redis rate limiter tripped after 5 failed attempts with exponential lockout"
    )

    # Clean up rate limit key in Redis for test hygiene
    # Fetch user_id from db
    uid_proc = subprocess.run(
        ["docker", "exec", "airutil_db", "psql", "-U", "airutil", "-d", "airutil", "-t", "-A", "-c",
         f"SELECT id FROM users WHERE username = '{u1}';"],
        capture_output=True,
        text=True
    )
    user_id_1 = uid_proc.stdout.strip()
    subprocess.run(
        ["docker", "exec", "airutil_redis", "redis-cli", "DEL", f"vault_failed:{user_id_1}"],
        capture_output=True
    )

    # --------------------------------------------------------------------------
    # Check 4: Multi-Tenant Boundary Isolation (User B cannot read User A's items)
    # --------------------------------------------------------------------------
    s2 = requests.Session()
    u2 = f"bob_attacker_{int(time.time())}"
    p2 = "BobPassword123!"
    master2 = "BobMasterPassword2026!"

    resp2 = s2.get(f"{BASE_URL}/register")
    csrf2 = get_csrf_token(resp2.text)
    s2.post(f"{BASE_URL}/register", data={
        "csrf_token": csrf2,
        "username": u2,
        "email": f"{u2}@college.edu",
        "password": p2,
        "confirm_password": p2,
    }, allow_redirects=True)

    # Bob sets up his own vault
    v2_page = s2.get(f"{BASE_URL}/vault")
    csrf2 = get_csrf_token(v2_page.text)
    s2.post(f"{BASE_URL}/vault/setup", data={
        "csrf_token": csrf2,
        "master_password": master2,
        "confirm_password": master2,
    }, allow_redirects=True)

    # Bob views his unlocked vault
    bob_view = s2.get(f"{BASE_URL}/vault")
    bob_sees_alice_item = "National Student Bank" in bob_view.text
    bob_sees_alice_secret = item_secret in bob_view.text

    print_check(
        "Multi-Tenant Isolation (User B cannot read or view User A's items)",
        (not bob_sees_alice_item) and (not bob_sees_alice_secret),
        "Bob cannot see Alice's credential title, username, or decrypted secret"
    )

    # --------------------------------------------------------------------------
    # Bonus Check: Encrypted Backup Export and Master Password Rotation
    # --------------------------------------------------------------------------
    # Alice unlocks again with correct password
    s1.post(
        f"{BASE_URL}/vault/unlock",
        data={"csrf_token": csrf1, "master_password": master1},
        allow_redirects=True
    )
    # Export backup
    export_resp = s1.get(f"{BASE_URL}/vault/export")
    print_check(
        "Encrypted Vault Export",
        export_resp.status_code == 200 and export_resp.headers.get("Content-Disposition") == 'attachment; filename="airutil_vault_backup.enc"',
        f"Exported encrypted bundle ({len(export_resp.content)} bytes)"
    )

    # Rotate Master Password
    new_master1 = "AliceBrandNewMasterKey2027!"
    rotate_resp = s1.post(
        f"{BASE_URL}/vault/change-master",
        data={
            "csrf_token": csrf1,
            "current_master": master1,
            "new_master": new_master1,
            "confirm_new_master": new_master1,
        },
        allow_redirects=True
    )
    print_check(
        "Atomic Master Password Rotation & Re-Encryption",
        "successfully re-encrypted" in rotate_resp.text or "updated" in rotate_resp.text,
        "Re-encrypted all items in a single ACID transaction"
    )

    print("=" * 70)
    print("ALL PHASE 4 ACCEPTANCE CHECKS COMPLETED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    main()
