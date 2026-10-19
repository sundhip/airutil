# Phase 6B Report: Docker Terminal Showcase & Read-Only Engine

**Project**: Airutil (*"Air-drop your files. Lock your logins. Beam anything."*)  
**Phase**: 6B - Docker Terminal Showcase  
**Date**: October 2026  
**Status**: Completed, Fully Tested, and Verified Live  

---

## 1. Overview & Objectives

Phase 6B introduces the **Docker Terminal Showcase**, an in-browser, high-fidelity terminal emulator powered by `xterm.js` that allows students, evaluators, and recruiters to interactively inspect container internals, execute whitelisted Docker commands, and observe Linux kernel primitives live.

### Core Architecture & Security Controls:
1. **Isolated Daemon Socket Proxy (`airutil_dockerproxy`)**:
   - Host `/var/run/docker.sock` is mounted read-only into an isolated proxy container (`tecnativa/docker-socket-proxy`).
   - The proxy is attached exclusively to a private internal bridge network (`showcase_net`). Neither `nginx`, `db`, `redis`, nor `worker` have access to this network.
   - All state-mutating HTTP methods (`POST`, `PUT`, `DELETE`) are strictly denied at the proxy layer. Only read-only endpoints (`GET /containers/*`, `GET /info`, `GET /version`) are reachable.
2. **Deterministic Whitelist Engine (`app/showcase/engine.py`)**:
   - Commands are parsed via Python `shlex.split()`.
   - Strict rejection of shell injection primitives: piping (`|`), sequencing (`;`), logical operators (`&&`, `||`), redirection (`>`, `<`), command substitution (backticks, `$()`), and subshells.
   - Exact command whitelist limited to non-destructive inspection operations:
     - `docker ps`, `docker stats --no-stream`, `docker top`, `docker inspect`, `docker version`, `docker info`
     - `cat /sys/fs/cgroup/*` (reading cgroups v2 memory and CPU parameters)
     - `ls -l /proc/*/ns` (inspecting Linux namespace inodes)
     - `cat /proc/self/mountinfo`, `cat /proc/1/cmdline`, `uname -a`, `help`, `clear`
     - 13 pre-packaged educational demos (`demo 1` through `demo 13`).
3. **SSE Real-Time Terminal Streaming**:
   - The `/api/terminal/run` endpoint streams stdout/stderr as Server-Sent Events (`text/event-stream`), providing a responsive typing and output experience.
   - Nginx proxy buffering is explicitly disabled (`proxy_buffering off; proxy_cache off;`) for `/api/terminal/run` to guarantee instantaneous event delivery.
4. **Token-Bucket Rate Limiting & Admin Authorization**:
   - Per-session rate limiter enforces a max of 30 commands per minute.
   - Only registered administrators (`is_user_admin(user_id)`) are granted access to `/terminal` and `/api/terminal/run`.
5. **Standalone Shell Showcase Runner (`scripts/airutil-showcase.sh`)**:
   - An automated terminal script executing all 13 architectural proofs sequentially or with interactive step-by-step confirmation (`--auto` / `-y`).

---

## 2. Changes Made

| Component | File | Description |
| :--- | :--- | :--- |
| **Docker Topology** | `docker-compose.yml` | Added 7th service `dockerproxy` with `tecnativa/docker-socket-proxy:latest`, private network `showcase_net`, read-only socket mount `:ro`, resource limits (`0.25` CPU, `64MB` RAM, `pids_limit: 100`). Connected `app1` and `app2` to both `vault_net` and `showcase_net`. |
| **Nginx Routing** | `nginx/nginx.conf` | Added dedicated `location /api/terminal/run` block with `proxy_buffering off;`, `proxy_cache off;`, and `chunked_transfer_encoding on;` to enable unbuffered SSE streaming. |
| **Showcase Engine** | `app/showcase/engine.py` | Built `CommandWhitelistParser`, `ShowcaseRateLimiter`, and `DockerProxyClient`. Implemented 13 live demo generators demonstrating cgroups, namespaces, OverlayFS, volume sync, and round-robin load distribution. |
| **Showcase Routes** | `app/showcase/routes.py` | Added `/terminal` UI route (admin-guarded), `/api/terminal/run` streaming SSE endpoint, and `/internal/oslab` internal probe. |
| **Frontend UI** | `app/templates/terminal.html` | Created mobile-first, accessible terminal interface integrating vendored `xterm.js` and `addon-fit.js`. Added quick command drawer and real-time container metrics banner. |
| **Vendored Assets** | `app/static/vendor/xterm/` | Vendored `xterm.js` (v5.3.0), `xterm.css`, and `addon-fit.js` locally to eliminate external CDN dependencies and ensure 100% offline functionality. |
| **Test Suite** | `tests/test_terminal_showcase.py` | Added 7 comprehensive unit and integration tests covering whitelist parsing, operator rejection, rate limiting, demo execution, role-based access control, and internal probes. |
| **CLI Showcase** | `scripts/airutil-showcase.sh` | Shell script verifying all 13 proofs with automated execution and color-coded output. |

---

## 3. The 13 OS and Docker Architectural Proofs

The showcase script (`scripts/airutil-showcase.sh`) and the in-browser terminal both execute these 13 proofs:

1. **Multi-Container Orchestration Topology**: Lists all 7 active containers across `vault_net` and `showcase_net`.
2. **Live Resource Telemetry**: Displays instantaneous CPU and memory metrics respecting configured resource limits.
3. **Kernel Cgroup Limits & Hardening**: Inspects `MemoryLimit` (128MB), `NanoCPUs` (0.5), `ReadOnlyRootfs: true`, and unprivileged UID 10001.
4. **Host vs Container Process Hierarchy**: Demonstrates PID 1 inside the container vs the parent Gunicorn PID in the host namespace via `docker top`.
5. **Network Namespaces & Bridge Isolation**: Dumps bridge IP assignments on `vault_net` (172.20.0.0/16) and isolation from `showcase_net`.
6. **Linux Kernel Namespaces**: Reads symlink inodes from `/proc/self/ns/{cgroup, ipc, mnt, net, pid, time, user, uts}`.
7. **Cgroups v2 Interface**: Directly reads `/sys/fs/cgroup/memory.max`, `/sys/fs/cgroup/memory.current`, and `/sys/fs/cgroup/cpu.max`.
8. **OverlayFS Union Filesystem**: Inspects rootfs mount layers (`lowerdir`, `upperdir`, `workdir`) in `/proc/self/mountinfo`.
9. **Named Volume Cross-Container Concurrency**: Synchronizes file data between `app1` and `app2` through atomic writes to `/vault_storage`.
10. **Redis Ephemeral Vault Key TTL (30s)**: Observes real-time key TTL decrementing from 30s to expiration.
11. **Nginx Layer-7 Round-Robin Balancing**: Sends 20 requests and confirms balanced routing via `X-Served-By` response headers.
12. **Zero-Downtime Failover Recovery**: Pauses `app1` and verifies that Nginx routes 100% of incoming traffic to `app2` without 502/504 errors.
13. **CFS CPU Quota Enforcement**: Runs a busy-loop CPU load and proves container throughput is throttled to 0.5 CPU.

---

## 4. Verification & QA Checklist

- [x] **All 7 Containers Running**: `nginx`, `app1`, `app2`, `worker`, `dockerproxy`, `redis`, `db` all healthy.
- [x] **Docker Socket Security**: `airutil_dockerproxy` rejects POST/PUT/DELETE commands; only read-only socket operations permitted.
- [x] **Whitelist Parser**: Shell operators (`|`, `&`, `;`, `$()`) return informative security rejection errors.
- [x] **SSE Streaming**: Output streams character-by-character with ANSI color codes preserved.
- [x] **Automated Showcase Proofs**: `bash scripts/airutil-showcase.sh --auto` runs all 13 proofs and exits 0.
- [x] **Role-Based Authorization**: Unauthenticated users or non-admin students attempting to access `/terminal` are redirected or return 403 Forbidden.
- [x] **Automated Tests**: Pytest tests pass cleanly (`7 passed in 0.9s`).
