# Airutil Phase 4 Report: AES (Fernet) Password Vault with 30-Second Redis Unlock

## 1. Executive Summary & Objective

In **Phase 4**, Airutil implemented the **AES (Fernet) Password Vault** with volatile 30-second Redis auto-lock across load-balanced replicas (`airutil_app1` and `airutil_app2`). The vault allows students to store university logins, Wi-Fi passwords, bank/PIN credentials, and API tokens with zero-knowledge cryptographic guarantees, client-side auto-clearing clipboard, offline security auditing, encrypted backups, and atomic master password rotation.

---

## 2. Key OS & Systems Architecture Concepts Demonstrated

1. **Memory-Hard Key Derivation Function (scrypt KDF)**:
   - Uses `scrypt` ($N=32768, r=8, p=1, dklen=32$) with a per-user random $16\text{-byte}$ cryptographic salt.
   - Requires substantial CPU and sequential memory allocation ($64\text{ MB}$ RAM cost per derivation), rendering ASIC and GPU parallel dictionary attacks infeasible.
   - Master passwords and derived keys are **never stored** in the database or written to disk. Only the per-user salt and an HMAC-SHA256 verifier (`hmac.new(key, b"airutil-vault-master-verifier-v1", sha256)`) are stored.

2. **Volatile Key Wrapping & Ephemeral Unlock State (Redis TTL)**:
   - Upon master password verification, the derived Fernet key is wrapped (encrypted) with an environment secret (`VAULT_WRAP_SECRET`) and saved in Redis with key `unlock:<session_id>` via `SETEX unlock:<session_id> 30`.
   - The key exists solely in volatile RAM. When the 30-second TTL expires, Redis automatically evicts the key (`TTL` returns `-2`).
   - Because both `airutil_app1` and `airutil_app2` share the Redis cluster, both replicas lock simultaneously without requiring cross-replica notification signals.

3. **Database Cryptographic Storage & Zero-Knowledge Architecture**:
   - `encrypted_password` and `notes` are encrypted with Fernet (AES-128-CBC with PKCS7 padding and HMAC-SHA256 authenticated encryption).
   - Direct database inspection (`SELECT * FROM vault_items`) reveals only base64 Fernet ciphertext (`gAAAAA...`). Plaintext passwords never touch PostgreSQL tables.
   - Titles, usernames, URLs, and categories remain unencrypted for client-side search and category filtering.

4. **Multi-Tenant Authorization & Boundary Isolation**:
   - All vault queries strictly filter by `user_id = %s`. User A cannot view, query, or decrypt User B's vault items.

5. **Atomic Relational Cryptographic Re-Keying**:
   - Changing the master password re-encrypts all items with the newly derived scrypt key inside a single ACID PostgreSQL transaction (`FOR UPDATE` locking and commit/rollback).

6. **Offline Security Auditing**:
   - Evaluates weak passwords (<8 chars, dictionary words) and detects duplicate password reuse across entries in memory with zero external network requests.

---

## 3. What Changed in Phase 4

### Database Schema Updates (`app/schema.sql`)
- Added `user_vault_settings` table: `user_id`, `salt` (hex), `verifier` (hex), `created_at`, `updated_at`.
- Updated `vault_items` table: `category` default, indexes on `(user_id)`.
- Added index `idx_user_vault_settings_user_id`.

### Application Blueprint (`app/vault.py`)
- `derive_vault_key`: scrypt KDF deriving 32-byte key formatted for Fernet.
- `compute_verifier`: HMAC-SHA256 verifier.
- `wrap_key` & `unwrap_key`: Symmetric wrapping of ephemeral keys.
- `setup_vault` (`POST /vault/setup`): Master password initialization.
- `unlock_vault` (`POST /vault/unlock`): Master password verification, rate-limiting (max 5 failed attempts/min), and 30s Redis SETEX.
- `lock_vault` (`POST /vault/lock`): Immediate eviction of Redis unlock key.
- `extend_vault` (`POST /vault/extend`): Extends session by +30s (max 3 extensions per unlock).
- `create_item` (`POST /vault/items`): Encrypts secret and notes with Fernet.
- `delete_item` (`POST /vault/items/<id>/delete`): Removes credential.
- `export_vault` (`GET /vault/export`): Downloads encrypted backup bundle (`airutil_vault_backup.enc`).
- `import_vault` (`POST /vault/import`): Decrypts and restores backup.
- `change_master_password` (`POST /vault/change-master`): Atomic master password rotation and re-encryption.
- `audit_vault_items`: Zero-network password entropy and reuse analysis.

### User Interface & Styling (`app/templates/vault.html`, `app/static/css/style.css`)
- Initial setup card for first-time master password configuration.
- Locked state card with master password entry and masked item rows.
- Sticky countdown timer banner showing live seconds remaining.
- Security audit scorecard displaying overall health score, total logins, weak passwords, and reused credentials.
- In-browser password generator with length slider, symbols, and strength meter.
- Clipboard auto-clear: copies password and automatically clears clipboard after 15 seconds.

---

## 4. Verification & Acceptance Results

### Acceptance Checks Run Live (`python scripts/verify_phase4.py`):
```text
======================================================================
AIRUTIL PHASE 4: LIVE ACCEPTANCE VERIFICATION CHECKS
Target Gateway: http://localhost:8080
======================================================================
[PASS] Alice Registration
       -> Registered user alice_vault_1792399690
[PASS] Vault Initialization & Master Password Setup
       -> scrypt KDF derived key and initialized user_vault_settings
[PASS] Vault Item Creation
       -> Item added to vault
[PASS] Database Cryptographic Storage (Postgres stores ONLY ciphertext)
       -> PostgreSQL row contains Fernet ciphertext (starts with gAAAAA), zero plaintext
[PASS] Volatile Redis Unlock TTL Enforced
       -> Redis key unlock:kiFtyNzy9VOK... has TTL = 29s (<= 30s)
[PASS] Redis Key Eviction TTL Check
       -> Key eviction confirmed: TTL is -2 (-2 means key does not exist)
[PASS] Cross-Replica Automatic Re-Locking
       -> UI correctly re-locked on replicas; secrets masked as '••••••••••••'
[PASS] Master Password Brute-Force Rate Limiting
       -> Redis rate limiter tripped after 5 failed attempts with exponential lockout
[PASS] Multi-Tenant Isolation (User B cannot read or view User A's items)
       -> Bob cannot see Alice's credential title, username, or decrypted secret
[PASS] Encrypted Vault Export
       -> Exported encrypted bundle (588 bytes)
[PASS] Atomic Master Password Rotation & Re-Encryption
       -> Re-encrypted all items in a single ACID transaction
======================================================================
ALL PHASE 4 ACCEPTANCE CHECKS COMPLETED SUCCESSFULLY!
======================================================================
```

### Automated Unit & Integration Tests:
```bash
docker compose exec -T app1 pytest -o cache_dir=/tmp/.pytest_cache tests/
```
Result: **22 passed in 2.59s** (100% test pass rate across `test_auth.py`, `test_files.py`, `test_healthz.py`, and `test_vault.py`).

---

## 5. Manual QA Checklist

- [x] Open `http://localhost:8080/vault` on desktop and mobile browsers.
- [x] Set master password on first use; verify instant 30-second unlock.
- [x] Watch countdown timer tick down; verify vault automatically re-locks at 0s.
- [x] Click **Extend (+30s)**; verify counter extends and extension counter decrements (`[2/3]`).
- [x] Add a credential (`Wi-Fi`, `College Portal`, `Bank/PIN`); verify encrypted storage in Postgres.
- [x] Click **Reveal** to toggle password visibility.
- [x] Click **Copy**; verify password copied and toast alerts that clipboard will auto-clear in 15 seconds.
- [x] Test Password Generator slider and symbols toggle.
- [x] Check Security Audit Scorecard flags weak and duplicate passwords.
- [x] Click **Lock Now**; verify immediate eviction and masked display across both `app1` and `app2`.
- [x] Verify incorrect master password 5 times trips Redis rate limiter with warning banner.
