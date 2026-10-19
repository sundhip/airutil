# Professional Portfolio & Resume Highlights: Airutil

**Project**: Airutil (*"Air-drop your files. Lock your logins. Beam anything."*)  
**GitHub Repository**: [https://github.com/sundhip/airutil](https://github.com/sundhip/airutil)  

---

## 1. LinkedIn Post Draft

🚀 **Most tutorials treat Docker as "just running an image". For my Operating Systems capstone, I wanted to build a production-grade system that proves Linux kernel primitives live.**

Introducing **Airutil** ⚡ (*"Air-drop your files. Lock your logins. Beam anything."*) — an all-in-one productivity vault built specifically for university students, backed by an observable 7-container microservice architecture.

Here is what makes this project special from a Systems Engineering perspective:

🔹 **Linux Kernel Cgroups v2 & Bandwidth Enforcement**:
Instead of relying on unbounded processes, each web replica is capped at exactly **0.50 CPU cores** and **128 MiB RAM** via kernel cgroups v2. In our live OS Lab, we can provoke the Linux Completely Fair Scheduler (CFS) into active throttling (`nr_throttled`), watching the kernel put runaway threads to sleep in real time!

🔹 **Layer-7 High Availability & Upstream Balancing**:
Traffic routes through Nginx to identical Flask replicas (`app1` and `app2`). In our 50-request load test benchmark, traffic split **50% / 50%** across both replicas. When we deliberately freeze `app1` using `docker pause`, Nginx detects upstream latency and transparently shifts 100% of traffic to `app2` with **zero 502 Bad Gateway errors**.

🔹 **Defense-in-Depth Container Hardening**:
- Root filesystems are mounted **strictly read-only (`read_only: true`)**, triggering immediate `EROFS` kernel halts if an attacker attempts file modification.
- Execution dropped to an unprivileged non-root user (`UID 10001`) with all Linux kernel capabilities dropped (`cap_drop: [ALL]`).
- Internal Docker socket introspection runs across a private bridge network through a **read-only proxy (`tecnativa/docker-socket-proxy`)** with a strict Python `shlex` command whitelist.

🔹 **Built for Student Workflows**:
- 📡 **Smart AirDrop**: Drag-and-drop file organizer with regex course-code recognition (`CSE1005`, `BCSE201P`) and magic-byte MIME protection against disguised executables.
- 🔐 **Ephemeral Password Vault**: AES-128 (Fernet) vault with an automatic 30-second Redis unlock timer that purges master keys from RAM upon expiration.
- 💥 **Burn-on-Read Clip Beam**: Single-use confidential snippets using atomic Redis `GETDEL` and a 3-strike PIN vaporize rule.
- 💻 **Docker Terminal Showcase**: In-browser terminal emulator powered by `xterm.js` running 13 automated architectural proofs.

📊 **By the Numbers**:
- **7 Microservices** across segregated bridge networks (`vault_net` and `showcase_net`).
- **52 / 52 Automated Tests Passing** in 17.77s across unit, integration, and security suites.
- **10 / 10 Automated OS Primitive Audits Passing** with 0 errors.
- **Zero Heavy JS Frameworks**: 100% vanilla HTML5, modern CSS design tokens, and ES6+.

Check out the full architecture breakdown, 13 automated proofs, and disaster recovery scripts on GitHub:
👉 https://github.com/sundhip/airutil

#OperatingSystems #Linux #Docker #DevOps #SystemDesign #Python #WebDevelopment #SoftwareEngineering #CloudComputing #ComputerScience

---

## 2. Featured Projects Section Description (LinkedIn / GitHub Profile)

**Airutil — Multi-Container Student Productivity Platform & OS Kernel Lab**  
*Technologies: Python 3.12, Flask, Gunicorn, PostgreSQL 16, Redis 7, Nginx 1.27, Docker Compose v2, Linux Kernel cgroups v2, xterm.js*

Architected and deployed a production-grade, 7-container student productivity web application and live operating systems laboratory. Designed to operate within a strictly constrained hardware footprint (1.75 CPU and 768MB RAM total), Airutil combines an automatic course-code file organizer, an ephemeral AES-128 password vault with a 30-second in-memory auto-relock mechanism, single-use burn-on-read text clips (atomic Redis `GETDEL`), and an in-browser Docker terminal showcase.

Enforced defense-in-depth security including `read_only` root filesystems, unprivileged user execution (UID 10001), dropped kernel capabilities, and read-only Docker daemon socket proxying. Validated through 52 automated Pytest suites (100% passing in 17.8s), 10 OS primitive kernel checks, and zero-downtime failover benchmarks under Layer-7 load balancing.

---

## 3. Resume Bullet Points (Google XYZ Format, Measurable Results Only)

> **Formula**: *Accomplished [X] as measured by [Y], by doing [Z]*

1. **Multi-Container Architecture & Resource Optimization**:
   > *Engineered a 7-service containerized web platform (Flask, Nginx, PostgreSQL, Redis, RQ) constrained to 1.75 CPU cores and 768MB total RAM, enforcing Linux cgroups v2 memory caps (128MB) and CFS bandwidth quotas (0.5 CPU) to maintain 100% host stability during simulated load spikes.*

2. **High Availability & Reverse Proxy Resilience**:
   > *Implemented Layer-7 round-robin load balancing and active-active failover across dual Flask replicas via Nginx, achieving an exact 50%/50% traffic distribution across 50 benchmark requests and zero dropped connections during container pause recovery.*

3. **Automated Testing, Hardening & Disaster Recovery**:
   > *Constructed an automated test and verification pipeline achieving 100% pass rates across 52 unit/integration tests in 17.8 seconds, while hardening containers via read-only rootfilesystems, unprivileged UID 10001 execution, and automated transactional backup/restore verification.*
