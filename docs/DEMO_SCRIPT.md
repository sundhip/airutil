# Airutil: 7-Minute Video Demonstration Script & Presentation Cue Sheet

**Project**: Airutil (*"Air-drop your files. Lock your logins. Beam anything."*)  
**Format**: Split Screen / Alternating Screen (Web Browser on Left, Terminal on Right)  
**Total Target Duration**: 7 minutes 30 seconds  
**Audience**: Course Evaluators, Technical Recruiters, Systems Engineers  

---

## ⏱️ Video Cue Sheet Overview

| Timecode | Segment Name | Primary Screen Focus | Key OS / Docker Primitive |
| :---: | :--- | :--- | :--- |
| **00:00 – 00:45** | Intro & Multi-Container Topology | Terminal (`docker compose ps`) + Browser | Bridge networks (`vault_net`), Port binding isolation |
| **00:45 – 01:45** | Smart AirDrop & Semester Organizer | Browser (Files UI) + Terminal (Storage Mount) | Union filesystems, POSIX volume mounts, MIME verification |
| **01:45 – 02:45** | 30s AES Vault & Ephemeral Redis Key | Browser (Vault UI) + Terminal (`redis-cli TTL`) | In-memory key expiration, Scrypt KDF, Fernet wrapping |
| **02:45 – 03:30** | Burn-on-Read Clip Beam | Browser (Beam UI) + DevTools | Atomic `GETDEL`, volatile memory state, single-consumer |
| **03:30 – 04:30** | OS Lab & CFS Scheduler Throttling | Browser (`/oslab`) + Terminal (`cgroups v2`) | CFS bandwidth quota, `cpu.max`, `nr_throttled` provoker |
| **04:30 – 05:45** | L7 Load Balancing & 0-Downtime Failover | Terminal (`load_test.sh`) + Browser | Token bucket rate limiting, Nginx upstream failover |
| **05:45 – 06:45** | Docker Terminal Showcase & In-App Shell | Browser (`/terminal`) + In-browser `xterm.js` | Read-only socket proxy, strict whitelist validation |
| **06:45 – 07:30** | Automated Test Suite & Architecture Wrap | Terminal (`make test`, CI summary) | 52/52 tests green, least-privilege non-root containerization |

---

## 🎬 Minute-by-Minute Script

---

### [00:00 – 00:45] Segment 1: Introduction & Multi-Container Architecture

#### Screen Action:
- Open Terminal on full screen.
- Run `docker compose ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"`.
- Switch to browser at `http://localhost:8080`.

#### Speaker Narration:
> *"Hello! Welcome to Airutil — a student-friendly, multi-container web platform built to demonstrate operating systems and Linux kernel primitives in action.*
> 
> *Our tagline is: 'Air-drop your files. Lock your logins. Beam anything.'*
> 
> *Before we look at the UI, notice the terminal. Airutil runs across 7 containerized microservices: Nginx, two stateless Flask replicas `app1` and `app2`, an RQ worker, Redis 7, PostgreSQL 16, and an isolated read-only Docker socket proxy.*
> 
> *From an OS networking perspective, only a single port — 8080 on Nginx — is published to the host gateway. All database and cache services communicate across an isolated internal Linux bridge network called `vault_net` with zero external exposure."*

---

### [00:45 – 01:45] Segment 2: Smart AirDrop, Regex Course Tagger & MIME Protection

#### Screen Action:
- Navigate in browser to `http://localhost:8080/files`.
- Drag and drop a file named `CSE1005_Unit2_Notes.pdf` into the animated radar target.
- Point out the instant green badge: *"Detected Course: CSE1005"*.
- Switch to terminal and run:
  ```bash
  docker exec airutil_app1 ls -la /vault_storage/files/1/Documents/CSE1005/
  ```

#### Speaker Narration:
> *"Let's look at Smart AirDrop. When a college student drags coursework onto our radar dropzone, two critical operations occur:*
> 
> *First, we perform magic byte MIME verification. If an attacker attempts to disguise an executable binary — like a Windows PE file or a Linux ELF binary — as a PDF, our server inspects the first 512 bytes and rejects the upload with an HTTP 400.*
> 
> *Second, our server runs regex matching on the filename. The pattern recognizes university course codes like CSE1005 or BCSE201P and automatically categorizes the document.*
> 
> *In the terminal, you can see the file was saved to our shared Docker volume mounted at `/vault_storage`. Because both `app1` and `app2` mount this volume, files uploaded through replica 1 are instantly accessible by replica 2 with raw host NVMe throughput."*

---

### [01:45 – 02:45] Segment 3: AES Password Vault with 30-Second Ephemeral Redis Unlock

#### Screen Action:
- In browser, press `Ctrl+K` to open the Command Palette.
- Type `vault` and hit Enter to jump directly to `http://localhost:8080/vault`.
- Enter master password and click **Unlock Vault (30s)**.
- Highlight the animated SVG radial countdown ring ticking down: `30s`, `29s`, `28s...`.
- Switch to terminal and run:
  ```bash
  docker exec airutil_redis redis-cli TTL "vault_session:1"
  ```

#### Speaker Narration:
> *"Here in the Password Vault, student logins are encrypted using Fernet AES-128 and an Scrypt key derivation function.*
> 
> *Notice our 30-second unlock ring. When unlocked, the unwrapped master key is never persisted to disk. Instead, it is stored in volatile Redis memory using a strictly bounded 30-second TTL.*
> 
> *Look at the terminal output: Redis is decrementing the key's TTL in real time. Once the countdown reaches zero, Redis automatically purges the key from RAM. Even if an attacker dumps the server memory later, the plaintext master key no longer exists. The vault auto-locks itself with zero manual effort."*

---

### [02:45 – 03:30] Segment 4: Burn-on-Read Clip Beam (Atomic GETDEL)

#### Screen Action:
- In browser, open `http://localhost:8080/beam`.
- Type confidential text: `"Exam Room 402 Door Code: 8941"`, set TTL to `1 Hour`, and click **Create Clip Beam**.
- Copy the generated beam link and open it in a new Incognito browser window.
- View the secret note and observe the visual thermal burn animation.
- Refresh the incognito window — observe the immediate 404 / Burned notification.

#### Speaker Narration:
> *"Clip Beam is designed for quick, sensitive transfers between students — like sharing WiFi credentials or door codes.*
> 
> *Under the hood, we demonstrate atomic inter-process communication. When the recipient opens the link, the server issues a Redis `GETDEL` command.*
> 
> *In Operating Systems theory, `GETDEL` is atomic: reading the value and deleting the key happens in a single un-interruptible memory cycle. Two concurrent HTTP requests can never read the same note. The moment it is displayed, the snippet is physically destroyed."*

---

### [03:30 – 04:30] Segment 5: OS Lab & Linux Completely Fair Scheduler (CFS) Throttling

#### Screen Action:
- Open `http://localhost:8080/oslab`.
- Point out the real-time gauges:
  - Cgroup memory limit: `128 MiB`
  - Current memory consumption & peak usage
  - Namespaces table showing kernel inode numbers (`pid`, `mnt`, `net`, `uts`, `ipc`)
  - OverlayFS union layer visualizer
- Click the interactive button: **"Run 10s CPU Stress Test"**.
- Switch to terminal and execute:
  ```bash
  docker exec airutil_app1 cat /sys/fs/cgroup/cpu.stat
  ```

#### Speaker Narration:
> *"This is the OS Lab — the heart of Airutil's pedagogical value. It reads directly from `/sys/fs/cgroup` and `/proc/self/ns` within the unprivileged container.*
> 
> *Students can inspect their exact cgroup memory ceiling — 134 megabytes — and verify that each replica runs under its own isolated PID and network namespaces.*
> 
> *Watch what happens when I trigger our 10-second CPU Stress Test. In the terminal, look at `/sys/fs/cgroup/cpu.stat`. The `nr_throttled` counter starts climbing rapidly.*
> 
> *This is the Linux Completely Fair Scheduler in action: our container is assigned a 50% CPU quota (50,000 microseconds per 100,000 microsecond period). Even though our Python loop tries to consume 100% of the host CPU, the Linux kernel scheduler actively puts our threads to sleep to enforce our quota!"*

---

### [04:30 – 05:45] Segment 6: Layer-7 Round-Robin Balancing & Zero-Downtime Failover

#### Screen Action:
- In terminal, execute our automated load testing script:
  ```bash
  bash scripts/load_test.sh 50 http://127.0.0.1:8080/healthz
  ```
- Highlight the 50/50 round-robin split output: 25 requests to `app1`, 25 requests to `app2`.
- Next, simulate a hardware failure by pausing `app1`:
  ```bash
  docker pause airutil_app1
  ```
- In the browser, immediately refresh the dashboard — page loads instantly with HTTP 200.
- Check headers: `X-Served-By` now shows `app2:5000` (100% transparent failover).
- Unpause `app1`:
  ```bash
  docker unpause airutil_app1
  ```

#### Speaker Narration:
> *"Now let's examine high availability and Layer-7 reverse proxying.*
> 
> *Our load testing script sends 50 requests through port 8080. Nginx distributes them in round-robin fashion: exactly 25 requests served by replica 1, and 25 served by replica 2.*
> 
> *Now, let's break something. I'll freeze `app1` using `docker pause`.*
> 
> *I immediately refresh my browser. Notice: zero 502 Bad Gateway errors, zero dropped connections. Nginx detected the unresponsive socket via upstream timeout rules and transparently routed 100% of user traffic to `app2`.*
> 
> *This is zero-downtime fault tolerance demonstrated live."*

---

### [05:45 – 06:45] Segment 7: Docker Terminal Showcase & Read-Only Socket Proxy

#### Screen Action:
- In browser, navigate to `http://localhost:8080/terminal`.
- Show the web terminal powered by `xterm.js`.
- Type `docker ps` and show the streaming SSE response.
- Attempt a malicious command injection: `docker ps; rm -rf /`.
- Show the red security warning: *"Command rejected: Shell chaining operators (;, |, &, etc.) are prohibited."*
- Click the quick-command button: **"Proof 3: Kernel Cgroup Limits"**.

#### Speaker Narration:
> *"Next is the Docker Terminal Showcase — a terminal emulator right inside the browser built with vendored `xterm.js` and Server-Sent Events.*
> 
> *To do this safely without exposing host vulnerabilities, we route telemetry through `airutil_dockerproxy` on a private bridge network. The proxy rejects all `POST` and `DELETE` calls, allowing only read-only `GET` inspection requests.*
> 
> *Furthermore, our Python backend runs a strict `shlex` whitelist parser. If an attacker tries to chain commands with semicolons, pipes, or subshells, the engine immediately rejects the request with a security alert.*
> 
> *Students can execute 13 built-in architectural proofs right from their browser or using our host script `scripts/airutil-showcase.sh`."*

---

### [06:45 – 07:30] Segment 8: Automated Verification, CI Pipeline & Wrap-Up

#### Screen Action:
- Switch to terminal and run the test suite:
  ```bash
  docker compose exec -T app1 pytest -o cache_dir=/tmp/.pytest_cache -v tests/
  ```
- Show all 52 tests passing in ~18 seconds.
- Run `bash scripts/verify_os.sh` showing 10/10 PASS.
- Display the GitHub Actions CI badge on the repository README.

#### Speaker Narration:
> *"Finally, everything in Airutil is verified through automation.*
> 
> *Our Pytest suite runs 52 unit and integration tests across authentication, Argon2id hashing, cryptographic key wrapping, atomic Redis operations, and cgroup telemetry — all passing in under 18 seconds.*
> 
> *Our `verify_os.sh` script programmatically verifies all 10 OS constraints: 128MB RAM cgroups, 0.5 CPU quotas, read-only root filesystems, unprivileged non-root user 10001, and bridge isolation.*
> 
> *In summary, Airutil is a complete, portfolio-grade demonstration of how Linux kernel primitives, containerization, and modern web architecture come together in a production-ready student utility.*
> 
> *Thank you for watching!"*

---

## 📋 Speaker Setup & Pre-Flight Checklist
- [x] Docker Desktop is running and healthy.
- [x] Run `make up` to ensure all 7 containers are active (`docker compose ps`).
- [x] Create at least one test student user account (`testuser` / `TestPass123!`).
- [x] Ensure `scripts/airutil-showcase.sh` and `scripts/verify_os.sh` have executable permissions.
- [x] Test screen resolution set to 1920x1080 for crisp typography.
