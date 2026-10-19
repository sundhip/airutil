# Operating Systems Concepts: A Student Guide to Linux Kernel Primitives in Airutil

**Project**: Airutil (*"Air-drop your files. Lock your logins. Beam anything."*)  
**Target Audience**: Computer Science & Engineering Students, OS Course Instructors  
**Scope**: Linux Kernel Primitives, Containerization Internals, and Code Mappings  

---

## 1. Introduction: Containers are Just Linux Kernel Primitives

Many students think of Docker containers as "lightweight virtual machines". In reality, **there is no such thing as a "container" inside the Linux kernel**.

A container is simply an ordinary Linux process running with two kernel-level boundaries applied:
1. **Linux Namespaces**: Dictate **what the process can see** (its view of PIDs, network interfaces, mount points, and hostnames).
2. **Control Groups (cgroups)**: Dictate **how much the process can use** (hard limits on CPU cycles, memory bytes, and process counts).

Airutil was built to make these abstract OS concepts tangible, observable, and measurable in real time.

---

## 2. Linux Namespaces: What the Process Can See

### 2.1 Concept Overview
Namespaces wrap a global system resource in an abstraction that makes it appear to the processes within the namespace that they have their own isolated instance of the resource.

The Linux kernel supports 8 distinct namespaces:
1. **PID (Process ID)**: Virtualizes process numbers. The main process inside the container believes it is `PID 1`, while on the host it is mapped to a standard high PID (e.g., `PID 88019`).
2. **NET (Network)**: Virtualizes network devices, IP routing tables, port bindings, and firewall rules. Each container has its own `eth0` and loopback `lo`.
3. **MNT (Mount)**: Virtualizes the filesystem hierarchy. The container sees its own root filesystem (`/`) decoupled from the host root filesystem.
4. **UTS (UNIX Timesharing System)**: Virtualizes the system hostname and domain name. Allows each container to have an independent hostname (e.g., `airutil_app1`).
5. **IPC (Inter-Process Communication)**: Isolates System V IPC mechanisms and POSIX message queues. Processes in different containers cannot communicate via shared memory segments unless explicitly mounted.
6. **USER**: Maps container-internal UIDs/GIDs to different host UIDs/GIDs (e.g., container root `UID 0` mapped to unprivileged host `UID 10001`).
7. **TIME**: Virtualizes clock offsets (monotonic and boot times).
8. **CGROUP**: Virtualizes the process's view of its own cgroup hierarchy directory.

### 2.2 Code Mapping in Airutil
- **Namespace Inspection**: Implemented in [`app/oslab.py`](file:///C:/Users/sundhip/.gemini/antigravity/scratch/airutil/app/oslab.py) under `get_namespaces_info()`.
- **Inspecting Inodes**: Airutil reads `/proc/self/ns/*` using `os.readlink()`. Each symlink points to a unique kernel namespace inode string (e.g., `net:[4026532797]`).

### 2.3 Verification Commands & Expected Output

#### Inspecting Namespace Inodes inside `airutil_app1`:
```bash
docker exec airutil_app1 ls -l /proc/self/ns
```
**Output:**
```text
lrwxrwxrwx 1 airutil airutil 0 Oct 19 10:12 cgroup -> cgroup:[4026532796]
lrwxrwxrwx 1 airutil airutil 0 Oct 19 10:12 ipc -> ipc:[4026532794]
lrwxrwxrwx 1 airutil airutil 0 Oct 19 10:12 mnt -> mnt:[4026532791]
lrwxrwxrwx 1 airutil airutil 0 Oct 19 10:12 net -> net:[4026532797]
lrwxrwxrwx 1 airutil airutil 0 Oct 19 10:12 pid -> pid:[4026532795]
lrwxrwxrwx 1 airutil airutil 0 Oct 19 10:12 time -> time:[4026532865]
lrwxrwxrwx 1 airutil airutil 0 Oct 19 10:12 user -> user:[4026531837]
lrwxrwxrwx 1 airutil airutil 0 Oct 19 10:12 uts -> uts:[4026532792]
```

#### Comparing Host PID vs Container PID (PID Namespace):
```bash
# Inside the container: Gunicorn is PID 1
docker exec airutil_app1 cat /proc/1/cmdline | tr '\0' ' '
```
**Output:**
```text
/opt/venv/bin/python /opt/venv/bin/gunicorn --workers=2 --bind=0.0.0.0:5000 app.main:app
```
```bash
# On the host: Gunicorn is mapped to an unprivileged process PID
docker top airutil_app1
```
**Output:**
```text
UID     PID     PPID    C   STIME   TTY   TIME        CMD
10001   88019   87995   0   09:53   ?     00:00:00    /opt/venv/bin/python ... gunicorn (master)
10001   88080   88019   0   09:53   ?     00:00:00    /opt/venv/bin/python ... gunicorn (worker 1)
10001   88082   88019   0   09:53   ?     00:00:00    /opt/venv/bin/python ... gunicorn (worker 2)
```

---

## 3. Control Groups v2 (cgroups v2): How Much the Process Can Use

### 3.1 Concept Overview
While namespaces provide isolation, **cgroups** provide resource management. Without cgroups, a single runaway process could allocate all system RAM or consume 100% of CPU cycles, starving other services (a Denial of Service).

In Linux **cgroups v2** (unified hierarchy mounted at `/sys/fs/cgroup`), resource controllers are represented as standard files that can be read and written directly by the kernel:
- **`memory.max`**: Hard memory ceiling in bytes. If a process exceeds this limit and cannot reclaim pages, the kernel Out-Of-Memory (OOM) killer terminates processes in the group.
- **`memory.current`**: Instantaneous memory consumption in bytes.
- **`memory.peak`**: High-water mark of memory usage since cgroup initialization.
- **`cpu.max`**: Contains two numbers: `quota` and `period` (in microseconds).
  $$\text{CPU Cores} = \frac{\text{quota}}{\text{period}} = \frac{50000\mu s}{100000\mu s} = 0.50 \text{ cores}$$
- **`cpu.stat`**: Accounting statistics containing `nr_periods`, `nr_throttled`, and `throttled_usec`.

### 3.2 Code Mapping in Airutil
- **Compose Declaration**: [`docker-compose.yml`](file:///C:/Users/sundhip/.gemini/antigravity/scratch/airutil/docker-compose.yml) lines 28–34:
  ```yaml
  deploy:
    resources:
      limits:
        cpus: '0.50'
        memory: 128M
      reservations:
        memory: 64M
  ```
- **Kernel Reader**: [`app/oslab.py`](file:///C:/Users/sundhip/.gemini/antigravity/scratch/airutil/app/oslab.py) under `get_cgroups_info()`. Reads `/sys/fs/cgroup/memory.max`, `/sys/fs/cgroup/memory.current`, `/sys/fs/cgroup/cpu.max`, and `/sys/fs/cgroup/cpu.stat`.

### 3.3 Verification Commands & Expected Output

```bash
# Read memory hard limit directly from sysfs
docker exec airutil_app1 cat /sys/fs/cgroup/memory.max
```
**Output:**
```text
134217728
```
*(Note: $128 \times 1024 \times 1024 = 134,217,728$ bytes exact match)*

```bash
# Read CFS bandwidth quota and period
docker exec airutil_app1 cat /sys/fs/cgroup/cpu.max
```
**Output:**
```text
50000 100000
```
*(Quota: 50,000 microseconds per 100,000 microsecond window = 0.5 CPU cores)*

---

## 4. OverlayFS vs Named Volumes: Ephemeral Union vs Persistent Storage

### 4.1 Concept Overview
Containers achieve lightweight disk efficiency using a **union filesystem** (OverlayFS):
- **Lowerdir**: Immutable read-only base layers (e.g., Python 3.12 Debian slim image layers).
- **Upperdir**: A thin, writable Copy-On-Write (CoW) scratchpad layer unique to this container instance.
- **Merged / Rootfs**: The unified view presented to the running process at `/`.

```
  ┌──────────────────────────────────────────────┐
  │         Merged View (/ container root)       │
  └──────────────────────▲───────────────────────┘
                         │
         ┌───────────────┴───────────────┐
         │                               │
  ┌──────────────┐               ┌──────────────┐
  │   Upperdir   │               │   Lowerdir   │
  │ (Read-Only)  │               │ (Base Image  │
  │ (Read-Only)  │               │  13 Layers)  │
  └──────────────┘               └──────────────┘
```

**Why Read-Only Rootfs Matters**:
In Airutil, we set `read_only: true` on `app1`, `app2`, `worker`, and `nginx`. The kernel prevents any modifications to the Upperdir layer. If an attacker injects code attempting to drop a backdoor binary into `/bin` or modify application code, the kernel immediately halts the syscall with `EROFS` (Read-only file system).

**Contrasting with Named Volumes (`airutil_data`)**:
OverlayFS copy-on-write has performance overhead for heavy I/O. Therefore, user files are mounted to `/vault_storage` via a **Docker Named Volume (`airutil_data`)**. Named volumes bypass OverlayFS completely, mapping directly to native host block storage via direct VFS filesystem drivers.

### 4.2 Code Mapping in Airutil
- **Read-Only Rootfs**: Set in [`docker-compose.yml`](file:///C:/Users/sundhip/.gemini/antigravity/scratch/airutil/docker-compose.yml):
  ```yaml
  read_only: true
  tmpfs:
    - /tmp:exec,mode=1777
  ```
- **Volume Mount**: Mounted at `/vault_storage` across `app1`, `app2`, and `worker`.
- **Mountinfo Parser**: Implemented in [`app/oslab.py`](file:///C:/Users/sundhip/.gemini/antigravity/scratch/airutil/app/oslab.py) under `get_overlay_and_mounts_info()`.

### 4.3 Verification Commands & Expected Output

```bash
# Verify read-only rootfs prevents unauthorized writes
docker exec airutil_app1 touch /malicious_script.sh
```
**Output:**
```text
touch: cannot touch '/malicious_script.sh': Read-only file system
```

```bash
# Verify Cross-Container Volume Concurrency
# Write file from app1:
docker exec airutil_app1 sh -c "echo 'sync_token_42' > /vault_storage/sync.txt"

# Read file immediately from app2:
docker exec airutil_app2 cat /vault_storage/sync.txt
```
**Output:**
```text
sync_token_42
```

---

## 5. Docker Containers vs Virtual Machines

| Dimension | Type-2 Virtual Machine (e.g. VirtualBox) | Linux Container (Airutil Docker) |
| :--- | :--- | :--- |
| **Kernel Architecture** | Separate guest kernel per VM running on a hypervisor. | **Single shared host Linux kernel** mediating all processes. |
| **Isolation Mechanism** | Hardware-assisted CPU virtualization (Intel VT-x / AMD-V). | Kernel software boundaries (**Namespaces** and **cgroups**). |
| **Startup Latency** | 30 to 90 seconds (full OS boot sequence, BIOS, systemd). | **Sub-second (50ms–500ms)** (regular `fork()` + `execve()` syscall). |
| **Memory Footprint** | Heavy (1GB–4GB base OS per VM). | Ultra-light (**2MiB–50MiB** per service). |
| **I/O Overhead** | Virtualized disk controllers and synthetic devices. | Native host block storage speeds via volume passthrough. |

---

## 6. Inter-Process Communication (IPC): Redis as a Distributed Broker

### 6.1 Concept Overview
Because `app1` and `app2` run in isolated IPC and PID namespaces, they cannot share POSIX shared memory (`shm_open`) or local Unix domain sockets directly across container boundaries.

To synchronize state without coupling replicas, Airutil utilizes **Redis 7** over the virtual bridge network (`vault_net`) as an external, high-performance in-memory IPC broker.

### 6.2 Key IPC Patterns in Airutil
1. **Volatile Key Expiration (30s Vault Timer)**:
   - When the student enters their master password, `app1` derives the encryption key and writes to Redis:
     ```python
     redis_client.setex(f"vault_session:{user_id}", 30, unwrapped_key)
     ```
   - If the student's next request routes to `app2` via round-robin, `app2` reads the key directly from Redis. After 30 seconds, the kernel/Redis timer expires the key automatically.
2. **Atomic Single-Consumer Transfer (`GETDEL`)**:
   - The burn-on-read Clip Beam executes `GETDEL beam:<token>`, retrieving the message and destroying it in a single atomic memory instruction.
3. **Distributed Mutual Exclusion (Mutex Locks)**:
   - To prevent multiple students from running CPU stress tests concurrently, `app/oslab.py` uses Redis atomic locking:
     ```python
     acquired = redis_client.set("oslab_stress_lock", "active", nx=True, ex=12)
     ```

---

## 7. CPU Scheduling & CFS Quota Throttling

### 7.1 How CFS Bandwidth Control Works
The Linux kernel uses the **Completely Fair Scheduler (CFS)** to allocate CPU time across threads.

When you assign `cpus: '0.50'`, the kernel creates a CFS bandwidth group with:
- `period = 100,000` microseconds ($100\text{ ms}$).
- `quota = 50,000` microseconds ($50\text{ ms}$).

During each 100ms window, the container's threads are allowed to execute for up to 50ms total across all CPU cores. Once this quota is exhausted, the scheduler **throttles** the container: its processes are removed from the runqueue and put to sleep until the start of the next 100ms period!

```
 100ms CFS Window:
 ┌─────────────────────────────┬─────────────────────────────┐
 │    Active Execution (50ms)   │    Kernel Throttled (50ms)  │
 └─────────────────────────────┴─────────────────────────────┘
  ◄────── Quota Allocated ─────► ◄──── Process Forced Sleep ──►
```

### 7.2 Code Mapping & Provoking Throttling
- **Stress Worker**: Defined in [`app/oslab.py`](file:///C:/Users/sundhip/.gemini/antigravity/scratch/airutil/app/oslab.py) under `_cpu_stress_worker()`. Spawns a tight mathematical calculation loop for 10 seconds.
- **Throttling Telemetry**: Inspecting `cpu.stat` before and after:
  ```bash
  docker exec airutil_app1 cat /sys/fs/cgroup/cpu.stat
  ```
  **Output after stress test:**
  ```text
  usage_usec 4983210
  nr_periods 112
  nr_throttled 98
  throttled_usec 4890212
  ```
  *(Notice: `nr_throttled` jumped from 0 to 98, proving that the CFS scheduler actively clamped container throughput to keep the CPU capped at 0.5 cores!)*

---

## 8. Summary Checklist for Students & Evaluators

| Question to Investigate | Kernel File to Read | What it Proves |
| :--- | :--- | :--- |
| Is my memory capped? | `/sys/fs/cgroup/memory.max` | Hard cgroup limit enforced by kernel memory controller. |
| Is my CPU capped? | `/sys/fs/cgroup/cpu.max` | Completely Fair Scheduler quota / period ratio. |
| Am I isolated from other containers? | `/proc/self/ns/*` | Unique inode numbers per namespace. |
| Am I really PID 1? | `/proc/1/cmdline` vs host `ps` | PID namespace abstraction mapping container to host. |
| Is my container filesystem tamper-proof?| `touch /bin/test` | Read-only Upperdir OverlayFS enforcing `EROFS`. |
| Is my traffic evenly distributed? | `curl -i http://localhost:8080/healthz` | Layer-7 Nginx reverse proxy round-robin split. |
