# Phase 6 Report: OS Lab - Linux Kernel & Container Isolation Primitives

**Project**: Airutil (*"Air-drop your files. Lock your logins. Beam anything."*)  
**Phase**: 6 - OS Lab  
**Date**: October 2026  
**Status**: Completed, Fully Tested, and Verified Live  

---

## 1. Overview & Objectives

Phase 6 implements the **OS Lab**, an interactive, pedagogical telemetry suite that transforms Airutil from a student utility into a live operating systems laboratory. Students directly inspect the Linux kernel mechanisms that enforce container isolation, monitor real-time resource constraints, and interactively provoke kernel scheduler throttling.

### Core Operating Systems Primitives Demonstrated:
1. **Control Groups v2 (cgroups v2)**:
   - Kernel memory hard limits (`/sys/fs/cgroup/memory.max` capped at 128 MiB = 134,217,728 bytes).
   - Live memory consumption (`memory.current`) and high-water mark (`memory.peak`).
   - Completely Fair Scheduler (CFS) bandwidth quotas (`cpu.max` = `50000 100000`, enforcing 0.50 CPU cores).
   - CFS throttling metrics (`cpu.stat`: `usage_usec`, `nr_periods`, `nr_throttled`, `throttled_usec`).
2. **Linux Namespaces**:
   - Process, mount, network, UTS, and IPC isolation inspected directly via `/proc/self/ns/*` symlink inodes.
   - Proof of PID 1 container isolation (`/proc/1/cmdline` showing Gunicorn master running as non-root UID 10001).
   - Independent hostname isolation per container (UTS namespace).
3. **OverlayFS & VFS Mounts**:
   - Parsing `/proc/self/mountinfo` to expose the union filesystem architecture.
   - Visualizing immutable Docker image layers (`lowerdir`, 13 layers), copy-on-write scratchpad (`upperdir`), and workdir.
   - Contrasting union rootfs with direct host VFS block storage (`/vault_storage` via named volume `airutil_data`).
   - Verifying `read_only: true` container rootfs security (`EROFS` protection).
4. **Reverse Proxy Load Balancing**:
   - Visualizing Nginx round-robin upstream traffic distribution between `app1` and `app2` using Redis counters (`replica_reqs:app1`, `replica_reqs:app2`).
   - Real-time split percentage indicator dynamically updating as requests alternate.
5. **CFS Bandwidth Throttling Live Demo**:
   - Interactive "Run 10s CPU Stress Test" button triggering a controlled single-thread busy loop.
   - Directly provokes Linux CFS scheduler throttling, visibly increasing `nr_throttled` while CPU remains strictly capped near 50%.
   - Mutex concurrency guard in Redis (`oslab_stress_lock`, `NX`, 12s TTL) returning HTTP 429 to prevent concurrent abuse.
6. **High Availability & Fault Tolerance**:
   - Interactive card with copyable `docker stop airutil_app1` and `docker start airutil_app1` commands.
   - Explains Nginx upstream failover (`fail_timeout=10s max_fails=3`) maintaining 100% uptime with zero 502 errors.
7. **Pedagogical Explanations**:
   - Accessible `<details>` / `<summary>` accordions on every card answering *"What am I looking at?"* with rigorous OS theory.

---

## 2. Changes Made

| File | Purpose / Description |
| :--- | :--- |
| `app/oslab.py` | New blueprint implementing kernel readers (`get_cgroups_info`, `get_namespaces_info`, `get_overlay_and_mounts_info`, `get_redis_telemetry`), `/oslab` view, `/api/oslab` JSON telemetry endpoint, and `/api/oslab/stress` stress worker thread with Redis lock. |
| `app/main.py` | Registered `oslab_bp`, removed legacy placeholder `/oslab` route, and preserved existing routes and middleware. |
| `app/templates/oslab.html` | Rich, responsive, accessible OS Lab UI featuring live telemetry gauges, namespace inode table, OverlayFS layer breakdown, round-robin split bar, stress test trigger, fault tolerance CLI card, and 1s auto-polling JavaScript. |
| `app/static/css/style.css` | Added styling for OS Lab grid, cgroup gauge bars, monospace telemetry boxes, namespace tables, replica split bars, terminal command cards, and dark/light theme tokens. |
| `tests/test_oslab.py` | 12 comprehensive unit and integration tests verifying cgroup parsing, quota calculations, namespace inspection, mountinfo regexes, telemetry endpoints, authentication guards, and stress test concurrency locks. |
| `scripts/verify_phase6.py` | Automated acceptance verification script testing cgroup limits, namespace inodes, PID 1 evidence, round-robin load distribution, stress test trigger, and live CFS throttling progression against `http://localhost:8080`. |

---

## 3. Key Decisions & Rationale

1. **Procfs & Sysfs Direct Introspection**:
   - Rather than relying on external host agents or Docker socket mounts (which create container escape vulnerabilities), Airutil inspects the standard Linux virtual filesystems (`/proc` and `/sys`) natively exposed inside the container by the kernel.
2. **Graceful v2/v1/Unlimited Fallbacks**:
   - While modern Docker Desktop and Linux servers use cgroup v2, `get_cgroups_info()` detects both v2 (`/sys/fs/cgroup/memory.max`) and v1 (`/sys/fs/cgroup/memory/memory.limit_in_bytes`), and handles `"max"` unlimited quotas safely.
3. **Controlled CPU Stress Thread with Auto-Release Lock**:
   - The stress test runs in a single daemon thread for exactly 10.0 seconds. A Redis lock (`oslab_stress_lock`) prevents simultaneous invocations. Even if an unexpected error occurs, a `try...finally` block immediately purges the lock, backed by a 12s TTL safety net.
4. **1-Second Polling Loop**:
   - Uses standard `fetch("/api/oslab")` every 1000ms. Updates DOM elements in-place without rebuilding DOM nodes or leaking event listeners.

---

## 4. Verification & Live Metrics

### A. Full Test Suite (`pytest` inside `airutil_app1` container):
```bash
docker compose exec -T app1 pytest -o cache_dir=/tmp/.pytest_cache tests/
```
**Output**:
```
============================= test session starts ==============================
platform linux -- Python 3.12.15, pytest-8.3.4, pluggy-1.7.0
cachedir: /tmp/.pytest_cache
rootdir: /workspace
collected 38 items

tests/test_auth.py ......                                                [ 15%]
tests/test_beam.py ....                                                  [ 26%]
tests/test_files.py ........                                             [ 47%]
tests/test_healthz.py ..                                                 [ 52%]
tests/test_oslab.py ............                                         [ 84%]
tests/test_vault.py ......                                               [100%]

============================== 38 passed in 4.32s ==============================
```

### B. Live Acceptance Verification (`scripts/verify_phase6.py`):
```bash
python scripts/verify_phase6.py
```
**Output**:
```
======================================================================
AIRUTIL PHASE 6: LIVE OS LAB ACCEPTANCE VERIFICATION CHECKS
Target Gateway: http://localhost:8080
======================================================================
[PASS] Nginx & Backend Healthcheck
       -> HTTP 200
[PASS] Student Session Authentication
       -> Authenticated as oslab_verifier_1792400887
[PASS] OS Lab HTML Inspection View
       -> All 4 core primitive cards rendered in UI
[PASS] OS Lab JSON Telemetry Endpoint
       -> HTTP 200
[PASS] cgroups v2 Memory Limit Enforced (128 MiB)
       -> memory.max = 134217728 bytes (128.0 MiB), usage = 88.5 MiB
[PASS] cgroups v2 CPU CFS Limit Enforced (0.50 Cores)
       -> cpu.max quota=50000µs period=100000µs -> 0.5 cores
[PASS] Linux Namespaces Inode Inspection (/proc/self/ns/*)
       -> Detected 10 namespaces: ['cgroup', 'ipc', 'mnt', 'net', 'pid', 'pid_for_children', 'time', 'time_for_children', 'user', 'uts']
[PASS] Container PID 1 Isolation Evidence
       -> PID 1 commandline: /opt/venv/bin/python /opt/venv/bin/gunicorn --workers=2 --bind=0.0.0.0:5000 --access-logfile=- --error-logfile=- app.main:app
[PASS] OverlayFS & Volume Mount Inspection
       -> Overlay lower layers: 13, volume: /vault_storage
[PASS] Nginx Round-Robin Upstream Distribution
       -> Replicas serving requests: ['app1', 'app2']
       -> Pre-stress CFS stats: nr_periods=38, nr_throttled=19
[PASS] CFS CPU Stress Test Trigger (POST /api/oslab/stress)
       -> HTTP 200: CPU stress test started! CFS bandwidth controller throttling will increase live.
[PASS] Stress Test Mutex Lock Concurrency Guard
       -> HTTP 429 (Properly rejected concurrent invocation)
       -> Monitoring live CFS throttling progression (5s sample)...
       -> Mid-stress CFS stats: nr_periods=159, nr_throttled=121 (+102)
[PASS] Live CFS CPU Scheduler Throttling Verification
       -> CFS periods advanced by 121, nr_throttled=121
======================================================================
ALL PHASE 6 ACCEPTANCE VERIFICATIONS PASSED SUCCESSFULLY!
======================================================================
```

---

## 5. Manual QA Checklist

- [x] **Authentication Protection**: Navigating to `http://localhost:8080/oslab` while signed out redirects to `/login` with a clear warning flash.
- [x] **Live Inode Display**: Inode numbers for `pid`, `net`, `mnt`, etc., display correctly from `/proc/self/ns/*`.
- [x] **cgroup Gauge Accuracy**: Memory progress bar reflects `88.5 / 128.0 MiB` matching `docker stats`.
- [x] **Round-Robin Alternation**: Refreshing the page increments `app1` and `app2` request counters and updates the serving replica badge in the navbar.
- [x] **CFS Throttling Trigger**: Clicking "Run 10s CPU Stress Test" displays a 10s countdown, locks the button, and visibly increments the `CPU Throttled` count as the Linux CFS scheduler throttles the thread.
- [x] **Failover Test**: Stopping `airutil_app1` (`docker stop airutil_app1`) results in zero downtime as Nginx routes 100% of traffic to `app2`. Restarting `airutil_app1` restores round-robin balance.
- [x] **Theme & Responsiveness**: Verified on mobile viewport (375px) and desktop (1440px) across light and dark themes.

---

## 6. Known Limits & Next Steps (Phase 7 & 8)

1. **Phase 7 UI Design System Polish**:
   - Further refine typography and mobile-first touch ergonomics across all pages.
2. **Phase 8 Docker Terminal Showcase & Host CLI**:
   - Implement the in-app interactive web terminal and companion host CLI script (`airutil-cli`) to allow students to run container and kernel inspection commands interactively.
