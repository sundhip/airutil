"""Airutil AES (Fernet) Password Vault Module (Phase 4).

Operating Systems Concepts Demonstrated:
1. Cryptographic Key Derivation Function (KDF):
   scrypt (n=32768, r=8, p=1) enforces high CPU and memory costs to defeat GPU brute-forcing.
   Per-user random salt ensures unique keys even with identical passwords.
2. Volatile Key Wrapping & Ephemeral Storage (Redis TTL):
   Derived Fernet encryption keys reside solely in volatile RAM, wrapped via an environment
   secret, expiring after 30 seconds via Redis SETEX. Both replicas (app1, app2) share this state.
3. Cryptographic Storage & Zero-Knowledge Architecture:
   Master passwords and keys are NEVER stored in PostgreSQL or filesystem disk.
   All secrets and notes are encrypted before database persistence.
4. Defense-in-Depth & Non-Repudiation:
   Access events logged to activity_log with zero plaintext retention.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import string
import time
from typing import Any, Dict, List, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    Response,
    send_file,
    url_for,
)

from app.db import get_db_cursor
from app.session import get_redis_client

logger = logging.getLogger("airutil.vault")

vault_bp = Blueprint("vault", __name__)

VERIFIER_MAGIC: bytes = b"airutil-vault-master-verifier-v1"
WRAP_SECRET: str = os.getenv("VAULT_WRAP_SECRET", os.getenv("SECRET_KEY", "airutil_default_vault_wrap_secret_32bytes"))

CATEGORIES = ["Wi-Fi", "Bank/PIN", "College Portal", "API Key", "Other"]
COMMON_PASSWORDS = {
    "password", "123456", "12345678", "qwerty", "admin", "welcome", "student", "college",
    "password123", "abc123", "letmein", "airutil", "root", "toor"
}


# ==============================================================================
# Cryptographic Key Derivation & Wrapping Utilities
# ==============================================================================

def derive_vault_key(master_password: str, salt_bytes: bytes) -> Tuple[bytes, str]:
    """Derive 32-byte key via scrypt and format as urlsafe base64 for Fernet.

    OS Concept: Memory-hard KDF (scrypt n=2^15, r=8, p=1).
    High memory allocation prevents ASIC / GPU parallel password exhaustion attacks.
    """
    raw_key = hashlib.scrypt(
        master_password.encode("utf-8"),
        salt=salt_bytes,
        n=32768,
        r=8,
        p=1,
        maxmem=64 * 1024 * 1024,
        dklen=32,
    )
    fernet_key_str = base64.urlsafe_b64encode(raw_key).decode("utf-8")
    return raw_key, fernet_key_str


def compute_verifier(raw_key: bytes) -> str:
    """Compute HMAC-SHA256 verifier of static string with derived raw key."""
    return hmac.new(raw_key, VERIFIER_MAGIC, hashlib.sha256).hexdigest()


def get_wrapper_fernet() -> Fernet:
    """Derive master wrapper key from environment secret."""
    key_bytes = hashlib.sha256(WRAP_SECRET.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def wrap_key(fernet_key_str: str) -> str:
    """Encrypt derived user Fernet key before volatile storage in Redis."""
    wf = get_wrapper_fernet()
    return wf.encrypt(fernet_key_str.encode("utf-8")).decode("utf-8")


def unwrap_key(wrapped_key_str: str) -> Optional[str]:
    """Decrypt user Fernet key from wrapped Redis payload."""
    wf = get_wrapper_fernet()
    try:
        return wf.decrypt(wrapped_key_str.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.error("Failed to unwrap vault key: %s", e)
        return None


def get_active_vault_key(session_id: str) -> Optional[str]:
    """Retrieve active user Fernet key from Redis volatile unlock cache.

    OS Concept: Distributed Ephemeral State.
    Both app1 and app2 query the same Redis unlock:<session_id> key with 30s TTL.
    """
    if not session_id:
        return None
    r = get_redis_client()
    raw = r.get(f"unlock:{session_id}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return unwrap_key(data["wrapped_key"])
    except Exception:
        return None


def get_vault_ttl(session_id: str) -> int:
    """Get remaining unlock seconds in Redis (returns <= 0 if locked)."""
    if not session_id:
        return 0
    r = get_redis_client()
    ttl = r.ttl(f"unlock:{session_id}")
    return max(0, ttl) if ttl > 0 else 0


def get_vault_extensions_left(session_id: str) -> int:
    """Get remaining allowed unlock extensions (max 3)."""
    if not session_id:
        return 0
    r = get_redis_client()
    raw = r.get(f"unlock:{session_id}")
    if not raw:
        return 0
    try:
        data = json.loads(raw)
        return int(data.get("extensions_left", 0))
    except Exception:
        return 0


# ==============================================================================
# Security Audit & Offline Analyzer
# ==============================================================================

def audit_vault_items(decrypted_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Perform zero-network security analysis on decrypted vault credentials."""
    total = len(decrypted_items)
    weak_count = 0
    duplicates_count = 0
    password_map: Dict[str, List[str]] = {}

    for item in decrypted_items:
        pwd = item.get("secret", "")
        # Weak password rules: length < 8, or entirely numeric/lowercase, or in common dictionary
        is_weak = False
        if len(pwd) < 8 or pwd.lower() in COMMON_PASSWORDS:
            is_weak = True
        elif not (re.search(r"[A-Z]", pwd) and re.search(r"[0-9]", pwd) and re.search(r"[!@#$%^&*(),.?\":{}|<>]", pwd)):
            is_weak = True

        if is_weak:
            weak_count += 1
            item["is_weak"] = True

        # Track password reuse
        if pwd:
            if pwd not in password_map:
                password_map[pwd] = []
            password_map[pwd].append(item["title"])

    # Mark duplicates
    for item in decrypted_items:
        pwd = item.get("secret", "")
        if pwd and len(password_map.get(pwd, [])) > 1:
            item["is_duplicate"] = True
            duplicates_count += 1

    health_score = 100
    if total > 0:
        deductions = (weak_count * 15) + (duplicates_count * 10)
        health_score = max(10, 100 - deductions)

    return {
        "total": total,
        "weak_count": weak_count,
        "duplicates_count": duplicates_count,
        "health_score": health_score,
    }


# ==============================================================================
# Vault Views and Core Endpoints
# ==============================================================================

@vault_bp.route("/vault", methods=["GET"])
def view_vault():
    """Main Password Vault UI: setup, locked, or unlocked view."""
    if not g.user:
        flash("Please log in to access your vault.", "warning")
        return redirect(url_for("auth.login", next=request.url))

    user_id = g.user["user_id"]
    session_id = g.session_id

    # Check if user has set up vault
    vault_settings = None
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT salt, verifier, created_at FROM user_vault_settings WHERE user_id = %s",
                (user_id,)
            )
            vault_settings = cur.fetchone()
    except Exception as e:
        logger.error("Failed to load vault settings: %s", e)

    has_vault = vault_settings is not None
    active_key = get_active_vault_key(session_id) if (has_vault and session_id) else None
    is_unlocked = active_key is not None
    ttl_seconds = get_vault_ttl(session_id) if is_unlocked else 0
    extensions_left = get_vault_extensions_left(session_id) if is_unlocked else 0

    category_filter = request.args.get("category", "").strip()
    search_query = request.args.get("q", "").strip()

    # Query items for current user
    items: List[Dict[str, Any]] = []
    try:
        with get_db_cursor(commit=False) as cur:
            conditions = ["user_id = %s"]
            params: List[Any] = [user_id]

            if category_filter and category_filter in CATEGORIES:
                conditions.append("category = %s")
                params.append(category_filter)

            if search_query:
                conditions.append("(LOWER(title) LIKE %s OR LOWER(username) LIKE %s OR LOWER(url) LIKE %s)")
                like_term = f"%{search_query.lower()}%"
                params.extend([like_term, like_term, like_term])

            cur.execute(
                f"""
                SELECT id, title, username, encrypted_password, url, notes, category, created_at, updated_at
                FROM vault_items
                WHERE {' AND '.join(conditions)}
                ORDER BY title ASC
                """,
                tuple(params)
            )
            items = cur.fetchall() or []
    except Exception as e:
        logger.error("Failed to fetch vault items: %s", e)
        flash("Could not load vault items.", "danger")

    # Decrypt if unlocked, otherwise mask
    if is_unlocked and active_key:
        fernet = Fernet(active_key.encode("utf-8"))
        for item in items:
            # Decrypt password/secret
            try:
                dec_pw = fernet.decrypt(item["encrypted_password"].encode("utf-8")).decode("utf-8")
                item["secret"] = dec_pw
            except Exception:
                item["secret"] = "[Decryption Error]"

            # Decrypt notes
            if item.get("notes"):
                try:
                    dec_notes = fernet.decrypt(item["notes"].encode("utf-8")).decode("utf-8")
                    item["notes_decrypted"] = dec_notes
                except Exception:
                    item["notes_decrypted"] = item["notes"]
            else:
                item["notes_decrypted"] = ""
    else:
        for item in items:
            item["secret"] = "••••••••••••"
            item["notes_decrypted"] = "••••••••••••"

    # Run offline security audit on decrypted items
    audit_results = audit_vault_items(items) if is_unlocked else None

    return render_template(
        "vault.html",
        has_vault=has_vault,
        is_unlocked=is_unlocked,
        ttl_seconds=ttl_seconds,
        extensions_left=extensions_left,
        items=items,
        categories=CATEGORIES,
        current_category=category_filter,
        search_query=search_query,
        audit=audit_results,
    )


@vault_bp.route("/vault/setup", methods=["POST"])
def setup_vault():
    """Initial vault master password setup."""
    if not g.user:
        abort(401)

    user_id = g.user["user_id"]
    master_password = request.form.get("master_password", "").strip()
    confirm_password = request.form.get("confirm_password", "").strip()

    if not master_password or len(master_password) < 8:
        flash("Vault master password must be at least 8 characters long.", "danger")
        return redirect(url_for("vault.view_vault"))

    if master_password != confirm_password:
        flash("Vault master passwords do not match.", "danger")
        return redirect(url_for("vault.view_vault"))

    # Generate 16-byte random salt and derive key
    salt_bytes = secrets.token_bytes(16)
    salt_hex = salt_bytes.hex()
    raw_key, fernet_key_str = derive_vault_key(master_password, salt_bytes)
    verifier_hex = compute_verifier(raw_key)

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO user_vault_settings (user_id, salt, verifier)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE
                SET salt = EXCLUDED.salt, verifier = EXCLUDED.verifier, updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, salt_hex, verifier_hex)
            )
            cur.execute(
                "INSERT INTO activity_log (user_id, action, details, ip_address) VALUES (%s, %s, %s, %s)",
                (user_id, "vault_setup", "Initialized master password vault", request.remote_addr)
            )

        # Automatically unlock newly created vault for 30s
        wrapped = wrap_key(fernet_key_str)
        r = get_redis_client()
        payload = {
            "wrapped_key": wrapped,
            "extensions_left": 3,
            "unlocked_at": time.time(),
        }
        r.setex(f"unlock:{g.session_id}", 30, json.dumps(payload))

        flash("Vault master password set successfully! Vault is unlocked for 30 seconds.", "success")
    except Exception as e:
        logger.error("Vault setup failed: %s", e)
        flash("Failed to initialize vault", "danger")

    return redirect(url_for("vault.view_vault"))


@vault_bp.route("/vault/unlock", methods=["POST"])
def unlock_vault():
    """Verify master password and store derived key in volatile Redis cache with 30s TTL."""
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = g.user["user_id"]
    session_id = g.session_id
    master_password = request.form.get("master_password", "").strip()

    # Rate limiting: max 5 failed attempts per minute
    r = get_redis_client()
    rate_key = f"vault_failed:{user_id}"
    attempts = r.get(rate_key)
    if attempts and int(attempts) >= 5:
        ttl = r.ttl(rate_key)
        flash(f"Too many incorrect attempts. Vault locked for {ttl} seconds.", "danger")
        return redirect(url_for("vault.view_vault"))

    # Fetch salt & verifier
    vault_settings = None
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT salt, verifier FROM user_vault_settings WHERE user_id = %s",
                (user_id,)
            )
            vault_settings = cur.fetchone()
    except Exception as e:
        logger.error("Failed to load settings: %s", e)
        flash("Database error", "danger")
        return redirect(url_for("vault.view_vault"))

    if not vault_settings:
        flash("Vault is not initialized.", "warning")
        return redirect(url_for("vault.view_vault"))

    salt_bytes = bytes.fromhex(vault_settings["salt"])
    raw_key, fernet_key_str = derive_vault_key(master_password, salt_bytes)
    test_verifier = compute_verifier(raw_key)

    if not hmac.compare_digest(test_verifier, vault_settings["verifier"]):
        # Increment failure rate limit
        current_fails = r.incr(rate_key)
        if current_fails == 1:
            r.expire(rate_key, 60)
        remaining = 5 - current_fails
        flash(f"Incorrect master password. {remaining} attempt(s) remaining.", "danger")
        return redirect(url_for("vault.view_vault"))

    # Successful verification: clear failed attempts
    r.delete(rate_key)

    # Store wrapped key in Redis with 30-second TTL
    wrapped = wrap_key(fernet_key_str)
    payload = {
        "wrapped_key": wrapped,
        "extensions_left": 3,
        "unlocked_at": time.time(),
    }
    r.setex(f"unlock:{session_id}", 30, json.dumps(payload))

    # Log access event without secrets
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO activity_log (user_id, action, details, ip_address) VALUES (%s, %s, %s, %s)",
                (user_id, "vault_unlock", "Unlocked password vault for 30s", request.remote_addr)
            )
    except Exception as e:
        logger.warning("Failed to log vault unlock: %s", e)

    flash("Vault unlocked for 30 seconds.", "success")
    return redirect(url_for("vault.view_vault"))


@vault_bp.route("/vault/lock", methods=["POST"])
def lock_vault():
    """Immediately lock vault by evicting key from Redis."""
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401

    session_id = g.session_id
    r = get_redis_client()
    if session_id:
        r.delete(f"unlock:{session_id}")

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO activity_log (user_id, action, details, ip_address) VALUES (%s, %s, %s, %s)",
                (g.user["user_id"], "vault_lock", "Locked password vault", request.remote_addr)
            )
    except Exception:
        pass

    flash("Vault locked.", "info")
    return redirect(url_for("vault.view_vault"))


@vault_bp.route("/vault/extend", methods=["POST"])
def extend_vault():
    """Extend vault unlock by +30 seconds (maximum 3 times per unlock session)."""
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401

    session_id = g.session_id
    r = get_redis_client()
    key = f"unlock:{session_id}"
    raw = r.get(key)

    if not raw:
        flash("Vault is already locked. Please unlock again.", "warning")
        return redirect(url_for("vault.view_vault"))

    try:
        data = json.loads(raw)
        extensions_left = int(data.get("extensions_left", 0))
        if extensions_left <= 0:
            flash("Maximum extensions reached (3/3). Please re-enter master password.", "warning")
            return redirect(url_for("vault.view_vault"))

        data["extensions_left"] = extensions_left - 1
        current_ttl = r.ttl(key)
        new_ttl = min(60, current_ttl + 30)
        r.setex(key, new_ttl, json.dumps(data))

        try:
            with get_db_cursor(commit=True) as cur:
                cur.execute(
                    "INSERT INTO activity_log (user_id, action, details, ip_address) VALUES (%s, %s, %s, %s)",
                    (g.user["user_id"], "vault_extend", f"Extended vault session by +30s ({data['extensions_left']} left)", request.remote_addr)
                )
        except Exception:
            pass

        flash(f"Vault extended! {new_ttl} seconds remaining ({data['extensions_left']} extension(s) left).", "success")
    except Exception as e:
        logger.error("Failed to extend vault: %s", e)
        flash("Could not extend session", "danger")

    return redirect(url_for("vault.view_vault"))


# ==============================================================================
# Vault Item CRUD Endpoints
# ==============================================================================

@vault_bp.route("/vault/items", methods=["POST"])
def create_item():
    """Create a new vault item with Fernet-encrypted secret and notes."""
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = g.user["user_id"]
    active_key = get_active_vault_key(g.session_id)
    if not active_key:
        flash("Vault is locked. Please unlock to add items.", "warning")
        return redirect(url_for("vault.view_vault"))

    title = request.form.get("title", "").strip()
    username = request.form.get("username", "").strip()
    secret = request.form.get("secret", "").strip()
    url = request.form.get("url", "").strip()
    category = request.form.get("category", "Other").strip()
    notes = request.form.get("notes", "").strip()

    if not title or not secret:
        flash("Title and Secret/Password are required.", "danger")
        return redirect(url_for("vault.view_vault"))

    if category not in CATEGORIES:
        category = "Other"

    # Encrypt secret and notes with user's Fernet key
    fernet = Fernet(active_key.encode("utf-8"))
    encrypted_secret = fernet.encrypt(secret.encode("utf-8")).decode("utf-8")
    encrypted_notes = fernet.encrypt(notes.encode("utf-8")).decode("utf-8") if notes else ""

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO vault_items (user_id, title, username, encrypted_password, url, notes, category)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (user_id, title, username, encrypted_secret, url, encrypted_notes, category)
            )
            cur.execute(
                "INSERT INTO activity_log (user_id, action, details, ip_address) VALUES (%s, %s, %s, %s)",
                (user_id, "vault_item_create", f"Added vault item '{title}' ({category})", request.remote_addr)
            )
        flash(f"Item '{title}' securely saved in vault.", "success")
    except Exception as e:
        logger.error("Failed to create vault item: %s", e)
        flash("Failed to save vault item", "danger")

    return redirect(url_for("vault.view_vault"))


@vault_bp.route("/vault/items/<int:item_id>/delete", methods=["POST"])
def delete_item(item_id: int):
    """Delete vault item."""
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = g.user["user_id"]
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "DELETE FROM vault_items WHERE id = %s AND user_id = %s RETURNING title",
                (item_id, user_id)
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "INSERT INTO activity_log (user_id, action, details, ip_address) VALUES (%s, %s, %s, %s)",
                    (user_id, "vault_item_delete", f"Deleted vault item '{row['title']}'", request.remote_addr)
                )
        flash("Item removed from vault.", "info")
    except Exception as e:
        logger.error("Failed to delete item: %s", e)
        flash("Could not delete item", "danger")

    return redirect(url_for("vault.view_vault"))


# ==============================================================================
# Encrypted Backup Export / Import & Master Password Rotation
# ==============================================================================

@vault_bp.route("/vault/export", methods=["GET"])
def export_vault():
    """Export all user vault items as an encrypted JSON bundle.

    OS Concept: Key-Wrapped Data Portability.
    Exported file is encrypted with user's master Fernet key.
    """
    if not g.user:
        abort(401)

    user_id = g.user["user_id"]
    active_key = get_active_vault_key(g.session_id)
    if not active_key:
        flash("Please unlock your vault before exporting.", "warning")
        return redirect(url_for("vault.view_vault"))

    items: List[Dict[str, Any]] = []
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT title, username, encrypted_password, url, notes, category FROM vault_items WHERE user_id = %s",
                (user_id,)
            )
            items = cur.fetchall() or []
    except Exception as e:
        abort(500)

    # Decrypt all items to build plaintext JSON bundle
    fernet = Fernet(active_key.encode("utf-8"))
    export_list = []
    for itm in items:
        try:
            pw = fernet.decrypt(itm["encrypted_password"].encode("utf-8")).decode("utf-8")
            nt = fernet.decrypt(itm["notes"].encode("utf-8")).decode("utf-8") if itm.get("notes") else ""
            export_list.append({
                "title": itm["title"],
                "username": itm["username"],
                "secret": pw,
                "url": itm["url"],
                "category": itm["category"],
                "notes": nt,
            })
        except Exception:
            continue

    bundle = {
        "format": "airutil-vault-backup-v1",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "item_count": len(export_list),
        "items": export_list,
    }
    raw_json = json.dumps(bundle, indent=2)
    # Encrypt the entire bundle with the user's master key
    encrypted_bundle = fernet.encrypt(raw_json.encode("utf-8"))

    # Log export event
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO activity_log (user_id, action, details, ip_address) VALUES (%s, %s, %s, %s)",
                (user_id, "vault_export", f"Exported {len(export_list)} encrypted vault items", request.remote_addr)
            )
    except Exception:
        pass

    resp = Response(encrypted_bundle, mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = 'attachment; filename="airutil_vault_backup.enc"'
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@vault_bp.route("/vault/import", methods=["POST"])
def import_vault():
    """Import and decrypt backup file using active master key."""
    if not g.user:
        abort(401)

    user_id = g.user["user_id"]
    active_key = get_active_vault_key(g.session_id)
    if not active_key:
        flash("Please unlock your vault before importing.", "warning")
        return redirect(url_for("vault.view_vault"))

    file_obj = request.files.get("backup_file")
    if not file_obj or not file_obj.filename:
        flash("No backup file selected.", "danger")
        return redirect(url_for("vault.view_vault"))

    encrypted_data = file_obj.read()
    fernet = Fernet(active_key.encode("utf-8"))

    try:
        decrypted_json = fernet.decrypt(encrypted_data).decode("utf-8")
        bundle = json.loads(decrypted_json)
        imported_items = bundle.get("items", [])
    except Exception as e:
        logger.error("Vault import decrypt failed: %s", e)
        flash("Failed to decrypt backup file. Ensure it was exported with this master key.", "danger")
        return redirect(url_for("vault.view_vault"))

    count = 0
    try:
        with get_db_cursor(commit=True) as cur:
            for itm in imported_items:
                title = itm.get("title", "Imported Item").strip()
                username = itm.get("username", "").strip()
                secret = itm.get("secret", "").strip()
                url = itm.get("url", "").strip()
                category = itm.get("category", "Other").strip()
                notes = itm.get("notes", "").strip()

                if not secret:
                    continue

                enc_pw = fernet.encrypt(secret.encode("utf-8")).decode("utf-8")
                enc_nt = fernet.encrypt(notes.encode("utf-8")).decode("utf-8") if notes else ""

                cur.execute(
                    """
                    INSERT INTO vault_items (user_id, title, username, encrypted_password, url, notes, category)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, title, username, enc_pw, url, enc_nt, category)
                )
                count += 1

            cur.execute(
                "INSERT INTO activity_log (user_id, action, details, ip_address) VALUES (%s, %s, %s, %s)",
                (user_id, "vault_import", f"Imported {count} items into vault", request.remote_addr)
            )
        flash(f"Successfully imported {count} items into your vault!", "success")
    except Exception as e:
        logger.error("Failed to import items into DB: %s", e)
        flash("Database error during import", "danger")

    return redirect(url_for("vault.view_vault"))


@vault_bp.route("/vault/change-master", methods=["POST"])
def change_master_password():
    """Rotate master password and atomically re-encrypt all vault items in one transaction.

    OS Concept: Atomic Relational Cryptographic Re-keying.
    Guarantees no partial re-encryption or lost items during key rollover.
    """
    if not g.user:
        abort(401)

    user_id = g.user["user_id"]
    current_pw = request.form.get("current_master", "").strip()
    new_pw = request.form.get("new_master", "").strip()
    confirm_pw = request.form.get("confirm_new_master", "").strip()

    if not current_pw or not new_pw:
        flash("Current and new master passwords are required.", "danger")
        return redirect(url_for("vault.view_vault"))

    if len(new_pw) < 8:
        flash("New master password must be at least 8 characters long.", "danger")
        return redirect(url_for("vault.view_vault"))

    if new_pw != confirm_pw:
        flash("New master passwords do not match.", "danger")
        return redirect(url_for("vault.view_vault"))

    # Verify current master password
    vault_settings = None
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute("SELECT salt, verifier FROM user_vault_settings WHERE user_id = %s", (user_id,))
            vault_settings = cur.fetchone()
    except Exception as e:
        flash("Database error", "danger")
        return redirect(url_for("vault.view_vault"))

    if not vault_settings:
        flash("Vault not found.", "danger")
        return redirect(url_for("vault.view_vault"))

    old_salt = bytes.fromhex(vault_settings["salt"])
    old_raw, old_fernet_key = derive_vault_key(current_pw, old_salt)
    old_verifier = compute_verifier(old_raw)

    if not hmac.compare_digest(old_verifier, vault_settings["verifier"]):
        flash("Incorrect current master password.", "danger")
        return redirect(url_for("vault.view_vault"))

    # Derive new key with new random salt
    new_salt = secrets.token_bytes(16)
    new_salt_hex = new_salt.hex()
    new_raw, new_fernet_key = derive_vault_key(new_pw, new_salt)
    new_verifier_hex = compute_verifier(new_raw)

    old_fernet = Fernet(old_fernet_key.encode("utf-8"))
    new_fernet = Fernet(new_fernet_key.encode("utf-8"))

    # Single atomic ACID transaction to re-encrypt all items
    try:
        with get_db_cursor(commit=True) as cur:
            # 1. Fetch all items
            cur.execute("SELECT id, encrypted_password, notes FROM vault_items WHERE user_id = %s FOR UPDATE", (user_id,))
            rows = cur.fetchall() or []

            # 2. Re-encrypt each item
            for row in rows:
                pw_plain = old_fernet.decrypt(row["encrypted_password"].encode("utf-8")).decode("utf-8")
                new_enc_pw = new_fernet.encrypt(pw_plain.encode("utf-8")).decode("utf-8")

                new_enc_notes = ""
                if row.get("notes"):
                    notes_plain = old_fernet.decrypt(row["notes"].encode("utf-8")).decode("utf-8")
                    new_enc_notes = new_fernet.encrypt(notes_plain.encode("utf-8")).decode("utf-8")

                cur.execute(
                    "UPDATE vault_items SET encrypted_password = %s, notes = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (new_enc_pw, new_enc_notes, row["id"])
                )

            # 3. Update vault settings
            cur.execute(
                "UPDATE user_vault_settings SET salt = %s, verifier = %s, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s",
                (new_salt_hex, new_verifier_hex, user_id)
            )

            # 4. Log event
            cur.execute(
                "INSERT INTO activity_log (user_id, action, details, ip_address) VALUES (%s, %s, %s, %s)",
                (user_id, "vault_master_change", f"Re-encrypted {len(rows)} items with new master password", request.remote_addr)
            )

        # 5. Update active Redis unlock with newly wrapped key
        r = get_redis_client()
        wrapped = wrap_key(new_fernet_key)
        payload = {
            "wrapped_key": wrapped,
            "extensions_left": 3,
            "unlocked_at": time.time(),
        }
        r.setex(f"unlock:{g.session_id}", 30, json.dumps(payload))

        flash(f"Master password updated! All {len(rows)} items successfully re-encrypted.", "success")
    except Exception as e:
        logger.error("Failed to rotate master password: %s", e)
        flash("Transaction failed: master password rotation aborted.", "danger")

    return redirect(url_for("vault.view_vault"))
