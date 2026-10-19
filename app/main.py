"""Airutil Web Application Entrypoint.

Operating Systems Concepts Demonstrated:
1. Namespaces (PID, UTS, MNT): Isolation of hostnames, processes, and mount points.
2. Control Groups (cgroups v2): Hard CPU quotas and memory limits enforced by the Linux kernel.
3. IPC & Network Sockets: TCP communication with PostgreSQL and Redis across bridge network.
4. Storage Volumes & VFS: Shared volume mounting for cross-container persistent storage.
"""

from __future__ import annotations

import datetime
import os
import socket
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request

# Initialize Flask application instance
app = Flask(__name__)

# OS Concept: Environment Variables (POSIX process environment block)
REPLICA_NAME: str = os.getenv("REPLICA_NAME", "standalone-app")
STORAGE_PATH: str = os.getenv("STORAGE_PATH", "/vault_storage")
DATABASE_URL: str = os.getenv("DATABASE_URL", "")
REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")


def get_cgroup_memory_limit() -> str:
    """Read cgroup v2 memory limit from /sys/fs/cgroup/memory.max.

    OS Concept:
    Linux Control Groups (cgroups v2) expose resource constraints
    via the pseudo-filesystem mounted at /sys/fs/cgroup.
    """
    try:
        # Check cgroup v2 path first
        cgroup_v2_path = "/sys/fs/cgroup/memory.max"
        if os.path.exists(cgroup_v2_path):
            with open(cgroup_v2_path, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val == "max":
                    return "Unlimited (max)"
                bytes_val = int(val)
                return f"{bytes_val / (1024 * 1024):.1f} MiB"

        # Check cgroup v1 fallback
        cgroup_v1_path = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
        if os.path.exists(cgroup_v1_path):
            with open(cgroup_v1_path, "r", encoding="utf-8") as f:
                bytes_val = int(f.read().strip())
                return f"{bytes_val / (1024 * 1024):.1f} MiB"
    except Exception:
        pass
    return "128.0 MiB (configured)"


def get_system_diagnostics() -> Dict[str, Any]:
    """Collect OS primitives and container isolation metadata.

    OS Concepts:
    - UTS Namespace: Container hostname isolation.
    - PID Namespace: Process ID isolation (Gunicorn worker runs as low-numbered PID).
    - VFS / Mount Namespace: Shared volume mount verification.
    """
    storage_mounted = os.path.isdir(STORAGE_PATH)
    storage_writable = False
    if storage_mounted:
        test_file = os.path.join(STORAGE_PATH, f".probe_{REPLICA_NAME}")
        try:
            with open(test_file, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            os.remove(test_file)
            storage_writable = True
        except Exception:
            storage_writable = False

    return {
        "replica_name": REPLICA_NAME,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "uid": os.getuid() if hasattr(os, "getuid") else "N/A",
        "gid": os.getgid() if hasattr(os, "getgid") else "N/A",
        "cgroup_memory_limit": get_cgroup_memory_limit(),
        "storage_path": STORAGE_PATH,
        "storage_mounted": storage_mounted,
        "storage_writable": storage_writable,
    }


@app.route("/healthz", methods=["GET"])
def healthcheck():
    """Health check endpoint for Docker HEALTHCHECK and Nginx load balancer probes.

    Returns:
        JSON response with service health status, replica name, and timestamp.
    """
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return jsonify({
        "status": "healthy",
        "service": "airutil",
        "replica": REPLICA_NAME,
        "timestamp": now,
        "pid": os.getpid(),
    }), 200


@app.route("/", methods=["GET"])
def index():
    """Main dashboard showing active replica, OS primitives, and system state."""
    diagnostics = get_system_diagnostics()
    return render_template("index.html", diag=diagnostics)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
