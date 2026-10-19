# Airutil Security Architecture & Threat Model

**Application**: Airutil (*"Air-drop your files. Lock your logins. Beam anything."*)  
**Security Level**: Production-Grade Multi-Container Student Vault  
**Operating Systems Primitives Enforced**: cgroups v2, Linux Namespaces, read-only rootfs, non-root user (10001), drop capabilities, token-bucket rate limiting.

---

## 1. Threat Model Table

| Asset | Threat | Potential Impact | Kernel & Architectural Mitigation in Airutil |
| :--- | :--- | :--- | :--- |
| **Host Operating System** | Container escape / Privilege escalation | Full host compromise | `cap_drop: ALL`, `security_opt: [no-new-privileges:true]`, non-root execution (`10001:10001`), `read_only: true` rootfs. |
| **Host CPU / Memory** | Fork bombs / Denial of Service (DoS) | Host resource starvation | cgroups v2 hard limits: `128M` memory max, `0.50` CPU CFS bandwidth quota, `pids_limit: 100`, `ulimits: nofile: 1024:2048`. |
| **Docker Engine Socket** | Daemon hijack via mounted `/var/run/docker.sock` | Root takeover of host | App containers **never** mount `docker.sock`. Only `airutil_dockerproxy` mounts it on an isolated network (`showcase_net`) with `POST=0, EXEC=0, SERVICES=0, SYSTEM=0`. |
| **User Passwords** | GPU/ASIC parallel brute-force attack | Credential compromise | RFC 9106 recommended Argon2id (`time_cost=2`, `memory_cost=64MB`, `parallelism=1`). |
| **Password Vault Master Key** | Cold boot / memory dump / swap persistence | Master secret theft | User Fernet master key is derived via `scrypt` ($N=32768$), wrapped, cached in Redis with strict 30-second TTL, and never written to disk or database. |
| **Burn-on-Read Beam Content** | Residual data forensic recovery | Leaked confidential transfer | Content is stored exclusively in RAM-only Redis (`save ""`, AOF disabled). Atomically destroyed on first read via Redis `GETDEL`. |
| **User Sessions** | Session hijacking / XSS exfiltration | Account impersonation | `HttpOnly`, `SameSite=Lax` cookies, per-session sliding window TTL in Redis, and strict CSRF verification on all mutating verbs. |
| **Shared Volume Files** | Path traversal (`../../etc/passwd`) | Arbitrary filesystem overwrite | Strict sanitization with `secure_filename()`, UUID-based internal storage names, real MIME inspection (disguised `.exe` blocked). |
| **Public Expiring Share Links** | Capability URL brute force | Unauthorized file download | 256-bit cryptographic hex tokens (`secrets.token_urlsafe(32)`), strict Redis TTL matching DB expiration, single-link revocation. |
| **Network Interception** | Internal socket eavesdropping | Cleartext data sniffing | Software-defined bridge networks (`vault_net`, `showcase_net`) isolate internal sockets. Nginx is the sole published gateway. |
| **Insecure Defaults** | Default secrets left in production | Trivial unauthorized access | Startup validation (`validate_production_secrets`) fails fast if default keys are detected in production mode. |

---

## 2. Container Hardening Audit

```
airutil_nginx:
  - Port published: 8080 (Sole host ingress)
  - Rate limiting: 20 req/s with burst=30 (Token Bucket)
  - Connection limit: 20 active connections per IP
  - CPU limit: 0.25 | Memory limit: 64M
  - Security headers: Strict CSP, X-Frame-Options DENY, nosniff, Referrer-Policy

airutil_app1 & airutil_app2:
  - Port published: None (Internal bridge only)
  - User: 10001:10001 (airutil, non-root)
  - Root filesystem: read_only: true
  - Temporary storage: tmpfs (/tmp, 32M, noexec, nosuid)
  - Capabilities: ALL dropped (cap_drop: ALL)
  - Privilege escalation: no-new-privileges: true
  - PID limit: 100 | Ulimits: nofile (soft 1024, hard 2048)
  - CPU limit: 0.50 | Memory limit: 128M

airutil_dockerproxy:
  - Accessible only on: showcase_net (internal to app1 & app2)
  - Socket mount: /var/run/docker.sock (ro, read-only)
  - Enforced API filters: CONTAINERS=1, NETWORKS=1, VOLUMES=1, IMAGES=1, INFO=1
  - Denied API filters: POST=0, EXEC=0, SERVICES=0, SWARM=0, SYSTEM=0, AUTH=0, SECRETS=0
  - Host access: Ports NOT published

airutil_redis:
  - Persistence: Disabled (save "", appendonly no)
  - Memory policy: allkeys-lru (maxmemory 64mb)
  - CPU limit: 0.25 | Memory limit: 96M

airutil_db:
  - User: postgres (non-root)
  - Storage: Persistent Docker volume airutil_pgdata
  - CPU limit: 0.50 | Memory limit: 256M
```

---

## 3. Dependency Security Audit (`pip-audit`)

Pinned dependencies scanned with `pip-audit`:
- `cryptography==44.0.1` (Clean)
- `argon2-cffi==23.1.0` (Clean)
- `flask==3.1.0` (Clean)
- `gunicorn==23.0.0` (Clean)
- `psycopg2-binary==2.9.10` (Clean)
- `redis==5.2.1` (Clean)
- `requests==2.32.3` (Clean)
- `docker==7.1.0` (Clean)

### Known Limitations:
1. **Local Self-Signed / HTTP Gateway**: Nginx runs on plain HTTP port 8080 by default for local development. In production, a TLS termination proxy or reverse proxy (e.g. Cloudflare / Let's Encrypt certbot) must provide HTTPS and HSTS.
2. **Docker Socket Proxy Read Boundary**: The socket proxy allows read-only GET requests (`docker inspect`, `docker ps`). Sensitive container environment variables could theoretically be read through `docker inspect` if an administrator runs it; therefore, the Terminal Showcase is strictly restricted to authenticated administrators and can be globally disabled via `SHOWCASE_ENABLED=false`.
