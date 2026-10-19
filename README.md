# Airutil ⚡

> **Air-drop your files. Lock your logins. Beam anything.**

Airutil is a high-performance, student-friendly, multi-container web platform created for an Operating Systems course (demonstrating Docker and Linux kernel primitives in action). It enables college students to organize files, manage passwords, and quickly beam data across devices with a clean, responsive, mobile-first interface.

---

## Architecture & Linux Kernel Primitives

Airutil runs across 6 containerized services linked via an isolated bridge network (`vault_net`). Only **port 8080** is published to the host gateway.

| Container | Image / Base | OS Primitives & Security Constraints | Memory / CPU Limits | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `airutil_nginx` | `nginx:1.27-alpine` | Reverse proxy, rate limiting, gzip compression | Standard | Public gateway, round-robin load balancer |
| `airutil_app1` | Python 3.12-slim | `cgroups v2`, `read_only: true`, `cap_drop: ALL`, `no-new-privileges`, non-root user (10001) | 0.5 CPU / 128 MiB | Web application replica 1 (Flask + Gunicorn) |
| `airutil_app2` | Python 3.12-slim | `cgroups v2`, `read_only: true`, `cap_drop: ALL`, `no-new-privileges`, non-root user (10001) | 0.5 CPU / 128 MiB | Web application replica 2 (Flask + Gunicorn) |
| `airutil_worker` | Python 3.12-slim | `cgroups v2`, `read_only: true`, `cap_drop: ALL`, `no-new-privileges`, tmpfs heartbeat | 0.25 CPU / 128 MiB | Background task worker (RQ) |
| `airutil_redis` | `redis:7-alpine` | Ephemeral memory broker (no persistence, LRU eviction) | Max 64 MB | Session cache and task queue broker |
| `airutil_db` | `postgres:16-alpine` | Persistent storage with named volume `airutil_pgdata` | Standard | Relational database (accounts, metadata) |

### Shared Storage
- **`airutil_data`**: Persistent volume mounted at `/vault_storage` in `app1`, `app2`, and `worker`.
- **`airutil_pgdata`**: Persistent PostgreSQL data volume.

---

## Quick Start

### 1. Prerequisites
- Docker Engine & Docker Compose v2 (e.g., Docker Desktop)
- Make (optional) or PowerShell

### 2. Configure Environment
```bash
cp .env.example .env
```

### 3. Launch the Stack
Using Make:
```bash
make up
```

Or using Docker Compose directly:
```bash
docker compose up -d --build
```

Or using the PowerShell helper on Windows:
```powershell
.\scripts\run.ps1 up
```

### 4. Open Application
Navigate your browser to:
[http://localhost:8080](http://localhost:8080)

---

## Acceptance Checks & Verification

1. **Service Healthchecks**:
   Verify all 6 containers report `healthy`:
   ```bash
   docker compose ps
   ```

2. **Round-Robin Load Balancing**:
   Refresh `http://localhost:8080` or execute:
   ```bash
   curl -i http://localhost:8080/healthz
   ```
   Inspect the `X-Served-By` response header and JSON payload — consecutive requests alternate between `app1:5000` and `app2:5000`.

3. **cgroup Resource Limits**:
   Verify hard CPU and memory limits enforced:
   ```bash
   docker stats --no-stream
   ```
   Confirm `airutil_app1` and `airutil_app2` have a 128MiB memory limit and 0.5 CPU limit; `airutil_worker` has 128MiB memory and 0.25 CPU.

4. **Port Exposure**:
   Inspect published ports:
   ```bash
   docker ps --format "table {{.Names}}\t{{.Ports}}"
   ```
   Only `airutil_nginx` exposes `0.0.0.0:8080->8080/tcp`. No backend services are directly exposed to the host.

5. **Failover Resilience**:
   Stop `app1` to verify Nginx fails over transparently to `app2`:
   ```bash
   docker stop airutil_app1
   curl http://localhost:8080/healthz
   docker start airutil_app1
   ```

---

## Project Structure

```
airutil/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── static/
│   │   ├── css/style.css
│   │   └── js/app.js
│   └── templates/
│       └── index.html
├── nginx/
│   ├── Dockerfile
│   └── nginx.conf
├── worker/
│   ├── __init__.py
│   └── worker.py
├── scripts/
│   └── run.ps1
├── tests/
│   ├── __init__.py
│   └── test_healthz.py
├── docs/
│   └── phases/
│       └── PHASE-1-REPORT.md
├── Dockerfile
├── docker-compose.yml
├── Makefile
├── requirements.txt
├── .env.example
├── .env
├── .gitignore
└── README.md
```
