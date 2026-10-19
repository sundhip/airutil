# Phase 2 Report: Accounts, Distributed Sessions & Security Primitives

**Phase**: 2 – Accounts and Sessions  
**Project**: Airutil (Air-drop your files. Lock your logins. Beam anything.)  
**Date**: October 19, 2026  
**Status**: Completed & Verified  

---

## 1. What Changed

1. **Relational Data Modeling (`app/schema.sql` & `scripts/init_db.py`)**:
   - Defined PostgreSQL schema with tables: `users`, `files`, `vault_items`, `share_links`, and `activity_log`.
   - Added foreign key constraints with `ON DELETE CASCADE` and lowercase B-Tree indexes on `username`, `email`, `user_id`, and `share_token`.
   - Created standalone database initialization script `scripts/init_db.py`.

2. **Connection Pooling (`app/db.py`)**:
   - Implemented `ThreadedConnectionPool` using `psycopg2` with automatic retry logic on startup.
   - Designed clean context manager `get_db_cursor(commit=True)` handling automatic transaction commits and rollbacks.
   - Added audit trail logger `log_activity` recording actions, user IDs, details, and client IPs into `activity_log`.

3. **Argon2id Password Hashing (`app/security.py`)**:
   - Integrated `argon2-cffi` utilizing RFC 9106 recommended parameters: `memory_cost=65536` (64 MiB), `time_cost=2`, `parallelism=1`, `hash_len=32`.
   - Strict input validation for usernames (`^[a-zA-Z0-9_]{3,30}$`), emails, and passwords (&ge; 8 characters).

4. **Distributed Server-Side Sessions in Redis (`app/session.py`)**:
   - Replaced local in-process session cookies with server-side Redis sessions keyed under `session:<random_token>`.
   - Random high-entropy token generated via `secrets.token_urlsafe(32)` stored in an `HttpOnly`, `SameSite=Lax` cookie named `airutil_session`.
   - Configured 2-hour sliding expiry window (`7200` seconds): each authenticated request resets the key TTL via atomic Redis `EXPIRE`.
   - **Cross-Replica Synchronization**: Because both `app1` and `app2` query the same `airutil_redis` service, users remain continuously authenticated as Nginx round-robins requests between replicas.

5. **Distributed Rate Limiting & CSRF Protection (`app/security.py` & `app/main.py`)**:
   - Distributed login attempt rate limiting: tracks failed attempts per IP (`ratelimit:login:ip:<ip>`) and per user (`ratelimit:login:user:<username>`) in Redis.
   - Caps failed attempts at 5 per 60-second window; the 6th failed attempt immediately returns HTTP 429 Too Many Requests.
   - CSRF protection enforced across all state-changing HTTP verbs (`POST`, `PUT`, `DELETE`, `PATCH`) with tokens bound to the Redis session.

6. **Responsive UI & Navigation (`app/templates/` & `app/static/`)**:
   - Created semantic, accessible `base.html` template with responsive sticky navbar (Dashboard, Files, Vault, Beam, OS Lab), active replica pill, user greeting, sign-out form, and light/dark/system theme switcher.
   - Created `register.html` and `login.html` with form validation and security badges.
   - Created protected `dashboard.html` showing active session ID, sliding TTL counter, serving container replica name, quick-launch module cards, and live `activity_log` audit trail.
   - Added `oslab.html` displaying live Linux kernel and cgroup primitives.

---

## 2. Operating Systems Concepts Demonstrated

| OS Concept | Implementation in Airutil | Technical Verification |
| :--- | :--- | :--- |
| **Distributed Shared State (Memory)** | Redis used as in-memory session cache shared across separate container network namespaces. | Requests alternate `app1` &harr; `app2` on `/dashboard` with 100% session persistence. |
| **Kernel Memory-Hard Cryptography** | Argon2id password hashing enforces a 64 MiB RAM computational memory cost per verification. | Password hashes in PostgreSQL begin with `$argon2id$v=19$m=65536,t=2,p=1`. |
| **Socket & Connection Pooling** | `ThreadedConnectionPool` maintains active TCP sockets to PostgreSQL across Gunicorn worker threads. | Handled concurrent queries with zero socket exhaustion or thread blocking. |
| **Distributed Concurrency & Rate Limiting** | Redis atomic counter increments (`INCR` + `EXPIRE`) track failed logins across distributed replicas. | 6 failed attempts consistently returns HTTP 429 across load balancer. |
| **Audit Logging & Non-Repudiation** | Security events (registration, login failure, login success, logout) recorded in PostgreSQL `activity_log` with client IP. | Inspected via `psql` and rendered on user dashboard. |

---

## 3. Decisions & Trade-Offs

1. **Redis Server-Side Sessions over JWTs**:
   - *Rationale*: Client-side JWTs cannot be revoked instantly without a revocation blocklist. Server-side Redis sessions permit instant revocation upon logout or password change, enforce sliding window expiration, and demonstrate shared in-memory IPC across container replicas.
2. **Argon2id over bcrypt/PBKDF2**:
   - *Rationale*: Argon2id is the winner of the Password Hashing Competition and the current gold standard (RFC 9106). Its memory-hard design resists GPU and ASIC cluster attacks, directly tying into operating system memory management concepts.
3. **Double-Submit / Session-Bound CSRF Token**:
   - *Rationale*: Binding the CSRF token to the Redis session prevents attackers from forging state-changing form submissions even if they know the user's IP or session structure.

---

## 4. Verification & Acceptance Checklist Results

| Acceptance Check | Requirement | Result | Exact Command / Output |
| :--- | :--- | :--- | :--- |
| **1. Registration & Login** | Register, log in across replicas without being logged out | **PASSED** | `python scripts/verify_phase2.py`<br>User registered, logged in, and executed 6 consecutive refreshes alternating between `app1` (`172.20.0.7`) and `app2` (`172.20.0.5`) with zero session loss. |
| **2. Rate Limiting** | Wrong password 6 times triggers rate limit | **PASSED** | Attempts 1–5 returned `HTTP 401 Unauthorized`. Attempt 6 returned `HTTP 429 Too Many Requests` ("Rate limit exceeded. Please try again in 60 seconds"). |
| **3. Redis Session Keys** | `redis-cli KEYS 'session:*'` shows the session | **PASSED** | `docker compose exec -T redis redis-cli KEYS "session:*"` returned active session keys (e.g. `session:CqTwnq_kdnKplxeS...`). `TTL` confirmed at ~7155s (sliding 2 hours). |
| **4. Argon2id Hashes** | Passwords in Postgres are Argon2 hashes | **PASSED** | `docker compose exec -T db psql -U airutil -d airutil -c "SELECT username, substring(password_hash from 1 for 30) FROM users;"`<br>Output confirmed: `$argon2id$v=19$m=65536,t=2,p=1...` |
| **5. Automated Test Suite** | All test suites green | **PASSED** | `docker compose exec -T app1 pytest -o cache_dir=/tmp/.pytest_cache tests/`<br>`8 passed in 1.33s` (`test_auth.py` and `test_healthz.py`). |

---

## 5. Manual QA Checklist

- [x] Open `http://localhost:8080/register` in browser; verify clean responsive UI with WCAG compliant contrast.
- [x] Register a student account; verify automatic redirect to `/dashboard` with session cookie set (`HttpOnly`, `SameSite=Lax`).
- [x] Click "🔄 Ping Replicas" on dashboard; verify requests alternate between `app1` and `app2` live without logging out.
- [x] Sign out; verify session is removed from Redis and cookie is deleted.
- [x] Attempt login with wrong password 6 times; verify alert box indicates rate limit exceeded with countdown timer.
- [x] Resize browser from 360px mobile width to 1920px desktop width; verify responsive mobile menu drawer functions smoothly.
