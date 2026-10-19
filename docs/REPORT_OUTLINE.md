# Academic Project Report (DA-3 Skeleton): Airutil

**Course Title**: Operating Systems (CSE2005 / BCSE201P)  
**Project Title**: Airutil: A Multi-Container Student Productivity Platform Demonstrating Linux Kernel Primitives and Container Isolation  
**Author / Candidate**: Lead Systems Engineer  
**Institution**: School of Computer Science and Engineering  
**Submission**: Digital Assignment 3 (DA-3) / Project Capstone Report  

---

## Abstract

Modern software development has widely adopted containerization, yet university operating systems curricula often treat container concepts as abstract virtualization layers. This project presents **Airutil** (*"Air-drop your files. Lock your logins. Beam anything."*), an open-source, multi-container student productivity web platform engineered to demonstrate core Linux operating systems primitives in a production-grade environment. 

Airutil orchestrates 7 microservices (Nginx reverse proxy, two stateless Flask web replicas, an RQ background worker, Redis 7, PostgreSQL 16, and an isolated read-only Docker socket proxy) across segregated bridge networks (`vault_net` and `showcase_net`). Memory hard limits (128 MiB) and Completely Fair Scheduler (CFS) CPU bandwidth quotas (0.50 CPU cores) are strictly enforced via cgroups v2. The platform features an automated semester file organizer with regex course-code tagging and magic-byte MIME protection, an ephemeral AES-128 password vault with a 30-second Redis auto-relock mechanism, single-use burn-on-read text beams (atomic `GETDEL`), and an interactive **OS Lab & Docker Terminal Showcase** that visualizes kernel telemetry live. 

Empirical benchmarks demonstrate 100% test pass rates across 52 unit and integration tests (17.77 seconds runtime), exactly balanced 50/50 Layer-7 round-robin traffic distribution under load, zero dropped connections during container pause failover, and verifiable CFS CPU throttling. All source code, Docker Compose topologies, test suites, and automated verification scripts are open-sourced under the MIT license.

---

## 1. Introduction

1.1 **Context & Evolution of Operating Systems**: The shift from monolithic bare-metal deployments to hypervisor-based Virtual Machines (VMs), and subsequently to operating system-level virtualization (Containers).  
1.2 **Demystifying Containerization**: Clarifying that Linux containers are not hardware virtual machines, but rather unprivileged host processes restricted by kernel namespaces and control groups.  
1.3 **Project Objectives**:
- Design and deploy a student-centric utility solving real university workflows (file sharing, password management, quick text transfers).
- Provide a hands-on, interactive pedagogical laboratory where students directly inspect Linux kernel data structures (`/sys/fs/cgroup`, `/proc/self/ns`, `/proc/self/mountinfo`).
- Deliver portfolio-grade software engineering adhering to production standards: zero hard-coded secrets, least privilege (UID 10001), read-only root filesystems, automated CI/CD, and disaster recovery.

---

## 2. Problem Statement & Motivation

2.1 **Student Workflow Inefficiencies**: Students frequently rely on insecure channels (unencrypted messaging apps, sticky notes, public pastebins) to transfer assignments, credentials, and temporary code snippets.  
2.2 **Pedagogical Gap in OS Education**: Theoretical textbooks frequently teach scheduling, memory management, and file systems through abstract mathematical models without providing accessible environments to observe these mechanisms under live HTTP workloads.  
2.3 **Design Requirements**:
- **Bounded Footprint**: Capable of executing on student hardware (under 2 CPU cores and 1 GB RAM total).
- **Zero Framework Bloat**: No heavy JavaScript frameworks (React/Vue/Angular); pure semantic HTML5, CSS custom properties, and vanilla ES6+.
- **Auditable Security**: Defense-in-depth preventing privilege escalation, host escape, SQL injection, and shell injection.

---

## 3. System Architecture & Container Topology

3.1 **Microservice Architecture Decomposition**:
- `airutil_nginx`: Reverse proxy, SSL terminator, rate limiter (20r/s), and Layer-7 round-robin upstream load balancer.
- `airutil_app1` & `airutil_app2`: Dual stateless Flask replicas running Gunicorn pre-fork worker processes.
- `airutil_worker`: Asynchronous Python RQ worker for background processing.
- `airutil_redis`: Ephemeral in-memory key-value store with `allkeys-lru` eviction policy.
- `airutil_db`: PostgreSQL 16 relational database with ACID consistency and `tsvector` full-text search.
- `airutil_dockerproxy`: Read-only host socket gateway on a private bridge network (`showcase_net`).

3.2 **Architectural Diagram**: Include the complete Mermaid system topology diagram illustrating network boundaries, volume mounts, and data flows.

3.3 **Network Segregation & Bridge Interfaces**:
- `vault_net` (172.20.0.0/16): Internal bridge network connecting application replicas, cache, database, and proxy.
- `showcase_net` (172.21.0.0/16): Isolated internal bridge network accessible only by `app1`, `app2`, and `dockerproxy`. Gateway port 8080 is the sole external access point.

---

## 4. Implementation Details

4.1 **Authentication & Sessions**: Password hashing using Argon2id (`argon2-cffi`), CSRF double-submit tokens, and ephemeral Redis session management.  
4.2 **Smart AirDrop & Semester Organizer**: Regex course-code parser (`[A-Z]{2,4}[0-9]{3,4}`), magic-byte MIME validation against executable spoofing, and POSIX atomic storage on `/vault_storage`.  
4.3 **Ephemeral AES-128 Password Vault**: Master password derivation using Scrypt (N=16384, r=8, p=1), Fernet master key encryption, and automatic 30-second Redis TTL key vaporization.  
4.4 **Burn-on-Read Clip Beam**: Atomic single-consumer `GETDEL` retrieval, 64KB payload caps, and 3-strike PIN protection.  
4.5 **OS Lab Telemetry Engine**: Native kernel virtual filesystem inspection reading `/sys/fs/cgroup` and `/proc/self/ns` directly from Python without elevated privileges.  
4.6 **Docker Terminal Showcase**: In-browser terminal using vendored `xterm.js`, Server-Sent Events (SSE) streaming, and strict `shlex` command whitelist parsing.  
4.7 **Disaster Recovery Pipeline**: Automated zero-downtime transactional database snapshotting (`pg_dump --clean --if-exists`), volume tarball archiving, and SHA-256 integrity verification.

---

## 5. Mapping Implementation to OS & Linux Kernel Primitives

| OS Concept | Theoretical Principle | Concrete Implementation in Airutil | Kernel Artifact / Syscall |
| :--- | :--- | :--- | :--- |
| **Namespaces** | Resource virtualization and isolation. | 8 isolated namespaces per container (`PID`, `NET`, `MNT`, `UTS`, `IPC`, `USER`, `TIME`, `CGROUP`). | `clone()`, `setns()`, `/proc/self/ns/*` inodes |
| **Control Groups (cgroups v2)** | Hierarchical resource metering and capping. | `mem_limit: 128M`, `cpus: 0.50`, `pids_limit: 100`. | `/sys/fs/cgroup/memory.max`, `/sys/fs/cgroup/cpu.max` |
| **CFS Scheduling** | Completely Fair Scheduler bandwidth quota enforcement. | 50ms execution quota per 100ms period; throttling provoker during CPU load. | `cpu.stat`: `nr_throttled`, `throttled_usec` |
| **Union Filesystems** | Copy-on-Write layered filesystem architecture. | Read-only container rootfs over immutable lower image layers; ephemeral tmpfs `/tmp`. | OverlayFS `lowerdir`, `upperdir`, `workdir`, `mountinfo` |
| **Direct VFS Storage** | Persistent block storage bypassing union drivers. | Docker named volume `airutil_data` mounted at `/vault_storage` across `app1`, `app2`, and `worker`. | VFS POSIX file descriptors, `write()`, `fsync()` |
| **Inter-Process Comm (IPC)** | Synchronization across isolated process spaces. | Redis in-memory broker, atomic `GETDEL`, `SETEX` TTL expiration, and distributed mutex locking (`SET NX EX`). | TCP socket buffers, atomic in-memory state machines |
| **Reverse Proxy & Routing** | Application-layer (L7) load balancing & resilience. | Nginx token bucket rate limiting (20r/s) and round-robin upstream balancing with failover. | `epoll()` event loop, TCP multiplexing, HTTP `X-Served-By` |

---

## 6. Experimental Results & Performance Benchmarks

6.1 **Automated Test Suite Verification**:
- Total tests: 52 unit and integration test cases across 8 test suites.
- Results: 52 passed, 0 failed, 0 skipped.
- Execution duration: 17.77 seconds.

6.2 **OS Primitives Audit (`scripts/verify_os.sh`)**:
- 10 automated kernel assertions executed against the running environment:
  - Memory ceiling verified at exactly 134,217,728 bytes.
  - CPU quota verified at 50,000 / 100,000.
  - Root filesystem confirmed mounted read-only (`EROFS`).
  - Execution identity confirmed as non-root `UID 10001`.
  - Overall status: 10/10 PASS (0 errors).

6.3 **Layer-7 Load Balancing Benchmark (`scripts/load_test.sh`)**:
- 50 consecutive HTTP requests dispatched through port 8080:
  - `airutil_app1` served: 25 requests (50.0%).
  - `airutil_app2` served: 25 requests (50.0%).
  - Dropped / failed requests: 0 (100% success rate).

6.4 **High-Availability Failover Test**:
- Execution of `docker pause airutil_app1` while streaming traffic.
- Healthcheck response: HTTP 200 delivered transparently by `airutil_app2` via Nginx `fail_timeout=10s max_fails=3`. Zero 502 Bad Gateway errors observed.

6.5 **CFS CPU Scheduler Throttling**:
- Execution of 10s CPU stress test in `airutil_app1`.
- Observable telemetry: `nr_throttled` increased from 0 to 98 periods; total CPU utilization remained clamped at 0.50 cores.

6.6 **Disaster Recovery Round-Trip**:
- Snapshot created at `backups/20261019_101926` containing 47.4 KB database dump and 3.0 KB volume tarball.
- Checksum validation verified (`SHA256SUMS: OK`). Full restore executed with 0 duplicate key errors and storage permissions correctly restored to `10001:10001`.

---

## 7. Engineering Challenges & Solutions

7.1 **Challenge 1: Read-Only Root Filesystem (`read_only: true`) vs Cache Writers**:
- *Problem*: Pytest, coverage collectors, and pip security tools attempt to write cache files to the working directory, failing with `EROFS`.
- *Solution*: Explicitly directed all runtime caches to ephemeral tmpfs: `cache_dir=/tmp/.pytest_cache` and `COVERAGE_FILE=/tmp/.coverage`.

7.2 **Challenge 2: In-Browser Terminal Real-Time Streaming**:
- *Problem*: Nginx buffers reverse proxy responses by default, causing terminal character output to hang until the command finishes.
- *Solution*: Configured `proxy_buffering off;`, `proxy_cache off;`, and `chunked_transfer_encoding on;` on `/api/terminal/run` for unbuffered Server-Sent Events.

7.3 **Challenge 3: Safe Docker Introspection Without Host Takeover**:
- *Problem*: Mounting `/var/run/docker.sock` directly into a web app creates an immediate container escape vulnerability (an attacker can spawn root containers).
- *Solution*: Placed `tecnativa/docker-socket-proxy` on an isolated internal network (`showcase_net`), strictly disabling all `POST`, `PUT`, and `DELETE` requests and filtering user input through a `shlex` whitelist parser.

---

## 8. Conclusion & Future Enhancements

8.1 **Conclusion**: Airutil successfully bridges the gap between theoretical operating systems coursework and modern production software engineering. By constraining services to exact cgroup boundaries, leveraging namespaces, and engineering failover mechanisms, Airutil proves that high-performance, resilient student applications can be constructed with minimal resource footprints.  
8.2 **Future Enhancements**:
- Horizontal pod autoscaling via Kubernetes (K8s) manifests.
- eBPF-based kernel tracing for real-time visualization of container syscalls.
- Object storage abstraction layer for multi-node S3-compatible clustering.

---

## 9. References

1. Silberschatz, A., Galvin, P. B., & Gagne, G. (2018). *Operating System Concepts* (10th ed.). Wiley.  
2. Kerrisk, M. (2010). *The Linux Programming Interface: A Linux and UNIX System Programming Handbook*. No Starch Press.  
3. Linux Kernel Documentation. *Control Group v2*. https://docs.kernel.org/admin-guide/cgroup-v2.html  
4. Linux Kernel Documentation. *Namespaces overview*. `man 7 namespaces`.  
5. Docker Documentation. *Resource constraints & cgroups*. https://docs.docker.com/config/containers/resource_constraints/  
6. Nginx Documentation. *Using NGINX as an HTTP load balancer*. https://docs.nginx.com/nginx/admin-guide/load-balancer/http-load-balancer/  
