# Phase 1 Report: Multi-Container Infrastructure Skeleton

**Phase**: 1 – Infrastructure Skeleton & Docker Primitives  
**Project**: Airutil (Air-drop your files. Lock your logins. Beam anything.)  
**Date**: October 19, 2026  
**Status**: Completed & Verified  

---

## 1. What Changed

1. **Repository Structure**:
   - Initialized `airutil/` repository with clean, modular structure: `app/`, `nginx/`, `worker/`, `scripts/`, `tests/`, `docs/phases/`.
   - Added configuration files: `.env.example`, `.env` (gitignored), `.gitignore`, `Makefile`, `pytest.ini`, and `README.md`.

2. **Multi-Stage Containerization (`Dockerfile`)**:
   - **Stage 1 (Builder)**: `python:3.12-slim` compiler layer with `build-essential` and `libpq-dev` generating isolated virtualenv at `/opt/venv`.
   - **Stage 2 (Runtime)**: Minimal `python:3.12-slim` image containing only runtime libraries (`curl`, `libpq5`, `procps`).
   - **Non-root Security**: Configured non-root user `airutil` (`uid: 10001`, `gid: 10001`).
   - **Process Supervision**: Gunicorn configured with 2 workers binding to `0.0.0.0:5000`.
   - **Container Healthcheck**: Configured `HEALTHCHECK` probing `http://127.0.0.1:5000/healthz`.

3. **Multi-Container Topology (`docker-compose.yml`)**:
   - Defined 6 microservices connected via isolated bridge network `vault_net`:
     - `airutil_nginx`: Reverse proxy exposing solely port 8080 to host.
     - `airutil_app1`: Flask replica 1 (0.50 CPU, 128MB RAM, `read_only: true`, `cap_drop: ALL`, `no-new-privileges: true`, tmpfs `/tmp`).
     - `airutil_app2`: Flask replica 2 (0.50 CPU, 128MB RAM, `read_only: true`, `cap_drop: ALL`, `no-new-privileges: true`, tmpfs `/tmp`).
     - `airutil_worker`: RQ background worker (0.25 CPU, 128MB RAM, tmpfs `/tmp` heartbeat).
     - `airutil_redis`: Ephemeral in-memory broker (`redis:7-alpine`, 64MB LRU cache cap, no persistence).
     - `airutil_db`: PostgreSQL 16 (`postgres:16-alpine`) mounted to persistent volume `airutil_pgdata`.
   - Configured `depends_on` conditions with `service_healthy`.
   - Mounted persistent volume `airutil_data` to `/vault_storage` on `app1`, `app2`, and `worker`.

4. **Nginx Reverse Proxy & Load Balancer (`nginx/nginx.conf`)**:
   - Upstream round-robin distribution between `app1:5000` and `app2:5000`.
   - Added `X-Served-By` upstream identification header.
   - Configured token-bucket rate limiting (`limit_req_zone`, 20 req/s, burst 30).
   - Configured Gzip compression and `client_max_body_size 100m`.
   - Added automated failover (`proxy_next_upstream`).

5. **Flask Application & Mobile-First Frontend**:
   - Implemented `GET /healthz` returning JSON status payload and replica metadata.
   - Implemented `GET /` rendering responsive, WCAG 2.2 AA compliant dashboard with live OS primitives display:
     - UTS/PID namespaces inspection (UID 10001, GID 10001, PID inside container).
     - cgroups v2 resource quotas (memory limit, CPU quota).
     - VFS volume mount status (`/vault_storage`, `/tmp` tmpfs).
     - Light/Dark/System theme switcher with `localStorage` persistence.
     - Interactive round-robin tester button executing dynamic fetch probes against `/healthz`.

---

## 2. Operating Systems Concepts Demonstrated

| OS Primitive | Implementation in Airutil | Technical Verification |
| :--- | :--- | :--- |
| **Namespaces (UTS, PID, NET, MNT)** | Isolated hostnames (`socket.gethostname()`), isolated process trees (worker PID 7/8), dedicated bridge network `vault_net`. | Inspected via `/` UI and `docker inspect`. |
| **cgroups v2 Quotas** | Hard memory cap (128MiB) and CPU scheduling limit (0.5 CPU / 500,000,000 NanoCpus). | Verified via `docker stats` and `docker inspect .HostConfig`. |
| **Read-Only Root Filesystem** | `read_only: true` on application containers with `/tmp` mounted via memory-backed `tmpfs`. | Pytest reported `[Errno 30] Read-only file system` when attempting to write to `/workspace`. |
| **Capability Dropping** | `cap_drop: ALL` and `no-new-privileges: true` preventing setuid privilege escalation. | Verified in compose specification and container runtime options. |
| **Least Privilege (UID Isolation)** | User `airutil` with UID 10001 / GID 10001 instead of root. | Verified in container execution context (`UID: 10001`). |
| **Layer 7 Load Balancing & Failover** | Nginx round-robin upstream distributing between `app1` and `app2` with automated failover. | Verified alternating responses via `curl` and zero-downtime failover when `app1` is stopped. |

---

## 3. Decisions & Trade-Offs

1. **Python 3.12-slim over Alpine for App Runtime**:
   - *Rationale*: Pre-compiled `manylinux` wheels for packages with C-extensions (like `psycopg2-binary`, `cryptography`) install reliably and performantly without requiring heavy compilation chains inside the production image.
2. **Tmpfs Mount for `/tmp`**:
   - *Rationale*: With `read_only: true`, applications still require scratch space for temporary lockfiles, Pytest cache, and worker heartbeats. Mounting a 32MB in-memory `tmpfs` provides fast, volatile scratch storage without persisting across container restarts.
3. **Dedicated Heartbeat Thread for Worker Healthcheck**:
   - *Rationale*: Because the RQ worker listens indefinitely on Redis queues without an HTTP server, a background daemon thread continuously touches `/tmp/worker_heartbeat`, enabling Docker's healthcheck mechanism to verify worker liveness via exit codes.

---

## 4. Verification & Acceptance Checklist Results

| Acceptance Check | Requirement | Result | Exact Command / Output |
| :--- | :--- | :--- | :--- |
| **1. Docker Compose Build** | `docker compose up -d --build` succeeds | **PASSED** | All images built and volumes initialized (`vault_net`, `airutil_data`, `airutil_pgdata`). |
| **2. Container Health** | All 6 containers healthy | **PASSED** | `airutil_nginx` (healthy), `airutil_app1` (healthy), `airutil_app2` (healthy), `airutil_worker` (healthy), `airutil_redis` (healthy), `airutil_db` (healthy). |
| **3. Round-Robin Load Balancing** | Refreshing alternates between `app1` and `app2` | **PASSED** | `1..4 \| ForEach-Object { curl.exe -s http://localhost:8080/healthz }`<br>Response sequence: `app1` &rarr; `app2` &rarr; `app1` &rarr; `app2`. `X-Served-By` alternating between `172.20.0.5:5000` and `172.20.0.6:5000`. |
| **4. Resource Limits** | 0.5 CPU and 128MiB memory limits enforced | **PASSED** | `docker stats --no-stream` confirms 128MiB limit on `app1`, `app2`, and `worker`. `docker inspect` confirms `NanoCpus=500000000` (0.5 CPU) and `Memory=134217728` (128MiB). |
| **5. Port Isolation** | Only port 8080 published to host | **PASSED** | `docker ps --format "table {{.Names}}\t{{.Ports}}"` shows solely `0.0.0.0:8080->8080/tcp` published on `airutil_nginx`. |
| **6. Failover Resilience** | Stopping `app1` keeps site reachable via `app2` | **PASSED** | `docker stop airutil_app1`<br>Consecutive requests returned `HTTP/1.1 200 OK` from `replica: app2`. Seamless recovery upon `docker start airutil_app1`. |
| **7. Unit Test Suite** | Automated tests passing inside container | **PASSED** | `docker compose exec -T app1 pytest -o cache_dir=/tmp/.pytest_cache tests/`<br>`2 passed in 0.21s`. |

---

## 5. Manual QA Checklist

- [x] Open `http://localhost:8080` in a desktop browser.
- [x] Verify branding ("Airutil") and tagline ("Air-drop your files. Lock your logins. Beam anything.") appear cleanly.
- [x] Click the "🔄 Refresh Request" button in the Gateway banner and verify the replica tag updates dynamically.
- [x] Toggle the theme button (☀️ Light &rarr; 🌙 Dark &rarr; 🌓 System) and verify immediate UI palette change and persistence on reload.
- [x] Resize browser viewport to 360px mobile width and 1920px desktop width; verify zero horizontal overflow or clipping.
- [x] Inspect `/healthz` directly in browser or terminal: verify valid JSON payload with `status: "healthy"`.

---

## 6. Known Limitations & Next Steps

- **Database Models**: Relational schemas for users, sessions, and files will be introduced in subsequent phases (Phase 2 & 3). Currently, PostgreSQL is verified healthy and awaiting migration scripts.
- **Worker Jobs**: RQ worker is listening on queues (`default`, `transfers`, `vault_jobs`) with an active heartbeat; job enqueueing will be wired in following modules.
