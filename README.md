# Airutil ⚡

> **Air-drop your files. Lock your logins. Beam anything.**

Airutil is a high-performance, student-friendly, multi-container web platform created for an Operating Systems course, demonstrating Docker and Linux kernel primitives in action. It enables college students to organize files, manage passwords with an ephemeral unlock mechanism, and beam burn-on-read clips across devices with a clean, responsive, mobile-first interface.

---

## 🏗️ Architecture & Linux Kernel Primitives

Airutil runs across **7 containerized microservices** linked via isolated Docker bridge networks (`vault_net` and `showcase_net`). Only **port 8080** is published to the host gateway.

```
                            [ Host Browser ]
                                   │
                           :8080 (TCP / HTTP)
                                   ▼
                        ┌─────────────────────┐
                        │    airutil_nginx    │  (Reverse Proxy / L7 Load Balancer)
                        │  (Rate Limit: 20r/s)│  (0.25 CPU, 64MB RAM, read_only)
                        └──────────┬──────────┘
                                   │
              ┌────────────────────┴────────────────────┐  vault_net (172.20.0.0/16)
              ▼                                         ▼
   ┌─────────────────────┐                   ┌─────────────────────┐
   │    airutil_app1     │                   │    airutil_app2     │
   │  (Flask + Gunicorn) │                   │  (Flask + Gunicorn) │
   │ (0.5 CPU, 128MB RAM)│                   │ (0.5 CPU, 128MB RAM)│
   │ (read_only, UID 10001)                 │ (read_only, UID 10001)
   └──────────┬──────────┘                   └──────────┬──────────┘
              │                                         │
              ├────────────────────┬────────────────────┤
              ▼                    ▼                    ▼
   ┌─────────────────────┐ ┌───────────────┐ ┌─────────────────────┐
   │    airutil_redis    │ │  airutil_db   │ │   airutil_worker    │
   │   (Redis 7 LRU)     │ │(Postgres 16)  │ │    (RQ Worker)      │
   │ (0.25 CPU, 96MB RAM)│ │(0.5 CPU, 256M)│ │(0.25 CPU, 128MB RAM)│
   └─────────────────────┘ └───────────────┘ └─────────────────────┘
              │
              └──────────────┐ showcase_net (Isolated)
                             ▼
                  ┌─────────────────────┐
                  │ airutil_dockerproxy │ (tecnativa/docker-socket-proxy)
                  │ (Read-Only Socket)  │ (0.25 CPU, 64MB RAM)
                  └─────────────────────┘
```

### Container Topology & Resource Caps

| Service | Image / Base | OS Primitives & Security Hardening | CPU / RAM Limits | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `airutil_nginx` | `nginx:1.27-alpine` | Layer-7 proxy, token-bucket rate limiting (20r/s), gzip compression, SSE unbuffered proxying | 0.25 CPU / 64 MiB | Gateway router & round-robin balancer |
| `airutil_app1` | Python 3.12-slim | `cgroups v2`, `read_only: true`, `cap_drop: ALL`, unprivileged user (UID 10001), tmpfs `/tmp` | 0.50 CPU / 128 MiB | Web replica 1 (Flask + Gunicorn) |
| `airutil_app2` | Python 3.12-slim | `cgroups v2`, `read_only: true`, `cap_drop: ALL`, unprivileged user (UID 10001), tmpfs `/tmp` | 0.50 CPU / 128 MiB | Web replica 2 (Flask + Gunicorn) |
| `airutil_worker` | Python 3.12-slim | `cgroups v2`, `read_only: true`, `cap_drop: ALL`, unprivileged user (UID 10001) | 0.25 CPU / 128 MiB | Asynchronous task worker (RQ) |
| `airutil_redis` | `redis:7-alpine` | `allkeys-lru` eviction policy, ephemeral in-memory storage (no disk persistence) | 0.25 CPU / 96 MiB | Sessions, rate limits, 30s unlock keys |
| `airutil_db` | `postgres:16-alpine` | Persistent ACID transactions, full-text search tsvectors, foreign keys | 0.50 CPU / 256 MiB | Relational data with `airutil_pgdata` |
| `airutil_dockerproxy` | `docker-socket-proxy`| Read-only host socket access, state-mutating HTTP methods (`POST`, `PUT`, `DELETE`) denied | 0.25 CPU / 64 MiB | Safe terminal telemetry on `showcase_net` |

---

## 🚀 Key Modules & Capabilities

1. **Smart AirDrop & Semester Organizer**:
   - Radar drag-and-drop file upload with animated pulse.
   - Automatic course code recognition (`CSE1005`, `BCSE201P`, `MAT2001`).
   - Magic byte MIME verification defending against executable extension spoofing.
   - Expiring, cryptographically random share links with access counts.
2. **AES-128 (Fernet) Password Vault with 30s Redis Ephemeral Key**:
   - Master password derivation using Scrypt (N=2^14, r=8, p=1).
   - Dynamic 30-second unlock timer backed by Redis `SETEX` and SVG radial countdown ring.
   - Master key securely zeroed from memory; auto-relocks after 30 seconds.
3. **Burn-on-Read Clip Beam**:
   - Single-use confidential text beam with atomic Redis `GETDEL`.
   - Optional 4-digit PIN protection with 3-strike burn logic.
   - Visual thermal burn animation upon reading.
4. **OS Lab**:
   - Live browser telemetry reading directly from `/sys/fs/cgroup` and `/proc/self/ns`.
   - Visual inspection of memory peak, CFS scheduler throttle events, and OverlayFS layers.
   - Interactive 10s CPU stress test provoker with Redis mutex locks.
5. **Docker Terminal Showcase**:
   - Web terminal powered by vendored `xterm.js` and Server-Sent Events (SSE).
   - Strict `shlex` whitelist parser denying shell chaining operators (`|`, `&`, `;`, `$()`).
   - Runs 13 automated proofs demonstrating Linux kernel and Docker primitives live.
6. **Command Palette (`Ctrl+K`) & Design System**:
   - Instant spotlight navigation (`Ctrl+K` or `Cmd+K`).
   - Pure vanilla HTML5/CSS3/ES6+ with semantic design tokens (`tokens.css`).
   - Dark, Light, and System theme toggling without page refresh.
   - PWA installability with service worker offline caching and fallback screen.

---

## 🛠️ Quick Start

### 1. Configure Environment
```bash
cp .env.example .env
```

### 2. Launch the Stack
Using Make:
```bash
make up
```

Or using Docker Compose directly:
```bash
docker compose up -d --build
```

### 3. Access Application
Open [http://localhost:8080](http://localhost:8080) in your browser.

---

## 🧪 Verification & Acceptance Suites

### 1. Run All 52 Automated Tests
```bash
make test
# or: docker compose exec -T app1 pytest -o cache_dir=/tmp/.pytest_cache -v tests/
```

### 2. Run OS & Docker Primitives Verification (10 Checks)
```bash
make verify-os
# or: bash scripts/verify_os.sh
```

### 3. Run Layer-7 Load Balancing Benchmark (50 Requests)
```bash
make load-test
# or: bash scripts/load_test.sh 50 http://127.0.0.1:8080/healthz
```

### 4. Run the 13 Automated OS & Docker Proofs
```bash
make showcase-auto
# or: bash scripts/airutil-showcase.sh --auto
```

### 5. Disaster Recovery Backup & Restore Round-Trip
```bash
# Create transactional snapshot
bash scripts/backup.sh

# Restore from snapshot
bash scripts/restore.sh backups/<timestamp>
```

---

## 🔒 Security Hardening Matrix

| Security Control | Implementation Mechanism | Verification Command |
| :--- | :--- | :--- |
| **Read-Only Rootfs** | `read_only: true` in Compose | `docker exec airutil_app1 touch /test` -> `Read-only file system` |
| **Least Privilege** | Non-root `UID 10001` (`airutil`) | `docker exec airutil_app1 id` -> `uid=10001(airutil)` |
| **Dropped Capabilities** | `cap_drop: [ALL]` on application containers | Confirmed in container inspection |
| **Fork Bomb Defense** | `pids_limit: 100` on app and worker | Verified via `verify_os.sh` |
| **Ephemeral Memory** | Redis `allkeys-lru` with 0 disk persistence | Inspected via Redis `CONFIG GET save` -> empty |
| **Zero Secrets in Git** | Dynamic `.env` validation (`validate_production_secrets`) | Tested via `test_security_resilience.py` |
| **Disaster Recovery** | `pg_dump --clean` + volume tar + SHA-256 | Validated via `scripts/backup.sh` & `scripts/restore.sh` |

---

## 📚 Phase Reports

Detailed architectural documentation for each implementation phase is available under [`docs/phases/`](file:///docs/phases/):
- [Phase 1: Multi-Container Foundation](docs/phases/PHASE-1-REPORT.md)
- [Phase 2: Authentication & Sessions](docs/phases/PHASE-2-REPORT.md)
- [Phase 3: Smart AirDrop & Semester Files](docs/phases/PHASE-3-REPORT.md)
- [Phase 4: AES Password Vault & 30s Redis Unlock](docs/phases/PHASE-4-REPORT.md)
- [Phase 5: Burn-on-Read Clip Beam](docs/phases/PHASE-5-REPORT.md)
- [Phase 6: OS Lab - Kernel & Container Telemetry](docs/phases/PHASE-6-REPORT.md)
- [Phase 6B: Docker Terminal Showcase & Socket Proxy](docs/phases/PHASE-6B-REPORT.md)
- [Phase 7: Design Tokens, Command Palette & PWA](docs/phases/PHASE-7-REPORT.md)
- [Phase 8: Security Hardening & Disaster Recovery](docs/phases/PHASE-8-REPORT.md)
- [Phase 9: Automated Test Suite & OS Verifications](docs/phases/PHASE-9-REPORT.md)
- [Security Threat Model & STRIDE Audit](docs/SECURITY.md)

---

## 📄 License
MIT License. Created for Operating Systems Coursework & Portfolio Showcase.
