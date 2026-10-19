"""Airutil OS Lab Blueprint (Phase 6).

Operating Systems Concepts Demonstrated:
1. Control Groups v2 (cgroups v2):
   Direct inspection of kernel accounting files (/sys/fs/cgroup/) for memory limits,
   memory usage, CPU CFS bandwidth quotas, and CFS throttling statistics.
2. Linux Namespaces:
   Process isolation inspected through /proc/self/ns/ symlink inode targets,
   verifying containerized PID 1, network namespaces, and mount points.
3. OverlayFS Layering & VFS Mounts:
   Parsing /proc/self/mountinfo to differentiate ephemeral container OverlayFS
   layers (lowerdir/upperdir/workdir) from host-mounted persistent VFS volumes.
4. Load Balancing & Round-Robin Visualization:
   Redis-backed atomic counters recording request distribution across app replicas.
5. CPU CFS Throttling Demonstration:
   A controlled busy-loop thread provoking kernel CFS bandwidth throttling.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import threading
import time
from typing import Any, Dict, List, Optional

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from app.session import get_redis_client

logger = logging.getLogger("airutil.oslab")

oslab_bp = Blueprint("oslab", __name__)

REPLICA_NAME: str = os.getenv("REPLICA_NAME", "app-unknown")
STORAGE_PATH: str = os.getenv("STORAGE_PATH", "/vault_storage")


# ==============================================================================
# Kernel Diagnostics Readers
# ==============================================================================

def read_file_safe(path: str) -> Optional[str]:
    """Safely read text file from sysfs/procfs."""
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read().strip()
    except Exception as e:
        logger.debug("Failed to read %s: %s", path, e)
    return None


def get_cgroups_info() -> Dict[str, Any]:
    """Inspect cgroups v2 / v1 memory and CPU limits and live statistics."""
    info: Dict[str, Any] = {
        "cgroup_version": "v2",
        "memory_limit_bytes": None,
        "memory_current_bytes": None,
        "memory_peak_bytes": None,
        "cpu_quota_usec": None,
        "cpu_period_usec": None,
        "cpu_cores_limit": 0.50,
        "cpu_usage_usec": 0,
        "nr_periods": 0,
        "nr_throttled": 0,
        "throttled_usec": 0,
        "raw": {},
    }

    # 1. cgroup v2 memory files
    mem_max_raw = read_file_safe("/sys/fs/cgroup/memory.max")
    mem_current_raw = read_file_safe("/sys/fs/cgroup/memory.current")
    mem_peak_raw = read_file_safe("/sys/fs/cgroup/memory.peak")

    if mem_max_raw is not None:
        info["raw"]["memory.max"] = mem_max_raw
        if mem_max_raw.isdigit():
            info["memory_limit_bytes"] = int(mem_max_raw)

    if mem_current_raw is not None and mem_current_raw.isdigit():
        info["raw"]["memory.current"] = mem_current_raw
        info["memory_current_bytes"] = int(mem_current_raw)

    if mem_peak_raw is not None and mem_peak_raw.isdigit():
        info["raw"]["memory.peak"] = mem_peak_raw
        info["memory_peak_bytes"] = int(mem_peak_raw)

    # Fallback to cgroup v1 if v2 missing
    if info["memory_limit_bytes"] is None:
        v1_limit = read_file_safe("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        v1_usage = read_file_safe("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        if v1_limit and v1_limit.isdigit():
            info["cgroup_version"] = "v1"
            info["memory_limit_bytes"] = int(v1_limit)
            info["raw"]["memory.limit_in_bytes"] = v1_limit
        if v1_usage and v1_usage.isdigit():
            info["memory_current_bytes"] = int(v1_usage)
            info["raw"]["memory.usage_in_bytes"] = v1_usage

    # If still not readable (e.g. mock or local environment)
    if info["memory_limit_bytes"] is None:
        info["memory_limit_bytes"] = 128 * 1024 * 1024
    if info["memory_current_bytes"] is None:
        info["memory_current_bytes"] = 35 * 1024 * 1024

    # 2. cgroup v2 CPU files
    cpu_max_raw = read_file_safe("/sys/fs/cgroup/cpu.max")
    if cpu_max_raw:
        info["raw"]["cpu.max"] = cpu_max_raw
        parts = cpu_max_raw.split()
        if len(parts) >= 2:
            quota, period = parts[0], parts[1]
            if quota.isdigit() and period.isdigit():
                info["cpu_quota_usec"] = int(quota)
                info["cpu_period_usec"] = int(period)
                info["cpu_cores_limit"] = round(int(quota) / int(period), 2)
            elif quota == "max":
                info["cpu_quota_usec"] = "max"
                info["cpu_period_usec"] = int(period) if period.isdigit() else 100000
                info["cpu_cores_limit"] = "Unlimited"

    # 3. cgroup v2 CPU stat
    cpu_stat_raw = read_file_safe("/sys/fs/cgroup/cpu.stat")
    if cpu_stat_raw:
        info["raw"]["cpu.stat"] = cpu_stat_raw
        for line in cpu_stat_raw.splitlines():
            k_v = line.split()
            if len(k_v) == 2:
                key, val = k_v[0], k_v[1]
                if val.isdigit():
                    if key == "usage_usec":
                        info["cpu_usage_usec"] = int(val)
                    elif key == "nr_periods":
                        info["nr_periods"] = int(val)
                    elif key == "nr_throttled":
                        info["nr_throttled"] = int(val)
                    elif key == "throttled_usec":
                        info["throttled_usec"] = int(val)

    # Format human-readable values & ratios
    info["memory_limit_human"] = f"{info['memory_limit_bytes'] / (1024 * 1024):.1f} MiB"
    info["memory_current_human"] = f"{info['memory_current_bytes'] / (1024 * 1024):.1f} MiB"
    info["memory_percent"] = min(100.0, round((info["memory_current_bytes"] / info["memory_limit_bytes"]) * 100.0, 1))

    info["cpu_usage_sec"] = round(info["cpu_usage_usec"] / 1_000_000, 2)
    info["throttled_sec"] = round(info["throttled_usec"] / 1_000_000, 3)
    info["throttled_percent"] = (
        round((info["nr_throttled"] / info["nr_periods"] * 100), 2)
        if info["nr_periods"] > 0
        else 0.0
    )

    return info


def get_namespaces_info() -> Dict[str, Any]:
    """Inspect /proc/self/ns/* symlinks and PID 1 evidence."""
    ns_dict: Dict[str, str] = {}
    ns_dir = "/proc/self/ns"
    if os.path.isdir(ns_dir):
        for entry in sorted(os.listdir(ns_dir)):
            full_path = os.path.join(ns_dir, entry)
            try:
                target = os.readlink(full_path)
                ns_dict[entry] = target
            except Exception:
                ns_dict[entry] = "accessible"

    # PID 1 inspection
    pid1_cmdline = read_file_safe("/proc/1/cmdline")
    if pid1_cmdline:
        pid1_cmd = pid1_cmdline.replace("\x00", " ").strip()
    else:
        pid1_cmd = "gunicorn (master)"

    # Thread count and capabilities from /proc/self/status
    status_raw = read_file_safe("/proc/self/status") or ""
    threads = 1
    cap_eff = "0000000000000000"
    for line in status_raw.splitlines():
        if line.startswith("Threads:"):
            threads = int(line.split(":")[1].strip())
        elif line.startswith("CapEff:"):
            cap_eff = line.split(":")[1].strip()

    return {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "uid": os.getuid() if hasattr(os, "getuid") else "N/A",
        "gid": os.getgid() if hasattr(os, "getgid") else "N/A",
        "namespaces": ns_dict,
        "pid1_cmd": pid1_cmd,
        "threads": threads,
        "cap_effective": cap_eff,
    }


def get_overlay_and_mounts_info() -> Dict[str, Any]:
    """Parse /proc/self/mountinfo to inspect OverlayFS layers and VFS volume mounts."""
    overlay_info: Dict[str, Any] = {
        "found": False,
        "lowerdir": "",
        "upperdir": "",
        "workdir": "",
        "mount_point": "/",
        "lower_layers": [],
        "lower_layers_count": 0,
    }
    volume_mount: Dict[str, Any] = {
        "found": False,
        "mount_point": STORAGE_PATH,
        "filesystem": "ext4 / volume",
        "options": "rw",
        "exists": os.path.exists(STORAGE_PATH),
    }

    mountinfo_raw = read_file_safe("/proc/self/mountinfo")
    if mountinfo_raw:
        for line in mountinfo_raw.splitlines():
            # Lines have format: ID Parent Major:Minor Root MountPoint MountOpts OptFields - FSType Source SuperOpts
            if " - overlay " in line:
                overlay_info["found"] = True
                match = re.search(r"lowerdir=([^,]+)", line)
                if match:
                    overlay_info["lowerdir"] = match.group(1)
                    overlay_info["lower_layers"] = match.group(1).split(":")
                    overlay_info["lower_layers_count"] = len(overlay_info["lower_layers"])
                match = re.search(r"upperdir=([^,]+)", line)
                if match:
                    overlay_info["upperdir"] = match.group(1)
                match = re.search(r"workdir=([^,]+)", line)
                if match:
                    overlay_info["workdir"] = match.group(1)

            if f" {STORAGE_PATH} " in line:
                volume_mount["found"] = True
                volume_mount["raw_line"] = line

    return {
        "overlay": overlay_info,
        "volume": volume_mount,
    }


def get_redis_telemetry() -> Dict[str, Any]:
    """Retrieve operational telemetry from Redis without leaking sensitive keys."""
    r = get_redis_client()
    try:
        info = r.info()
        connected_clients = info.get("connected_clients", 1)
        used_memory_human = info.get("used_memory_human", "N/A")
        uptime_seconds = info.get("uptime_in_seconds", 0)

        # Count active ephemeral tokens safely (without fetching contents)
        active_unlock_keys = len(r.keys("unlock:*"))
        active_beam_keys = len(r.keys("beam:*"))

        # Request counter per replica
        app1_reqs = int(r.get("replica_reqs:app1") or 0)
        app2_reqs = int(r.get("replica_reqs:app2") or 0)

        return {
            "status": "connected",
            "connected_clients": connected_clients,
            "used_memory_human": used_memory_human,
            "uptime_seconds": uptime_seconds,
            "active_unlock_keys": active_unlock_keys,
            "active_beam_keys": active_beam_keys,
            "app1_reqs": app1_reqs,
            "app2_reqs": app2_reqs,
        }
    except Exception as e:
        logger.error("Failed to query Redis telemetry: %s", e)
        return {"status": "error", "error": str(e)}


# ==============================================================================
# OS Lab Routes & Endpoints
# ==============================================================================

@oslab_bp.route("/oslab", methods=["GET"])
def view_oslab():
    """Main interactive OS Lab page for students (requires authentication)."""
    if not g.user:
        flash("Please log in to access the OS Lab.", "warning")
        return redirect(url_for("auth.login", next=request.url))

    r = get_redis_client()
    # Increment round-robin counter for serving replica
    try:
        r.incr(f"replica_reqs:{REPLICA_NAME}")
    except Exception as e:
        logger.warning("Could not increment replica counter: %s", e)

    cgroup = get_cgroups_info()
    ns = get_namespaces_info()
    mounts = get_overlay_and_mounts_info()
    redis_tel = get_redis_telemetry()

    return render_template(
        "oslab.html",
        replica_name=REPLICA_NAME,
        cgroup=cgroup,
        ns=ns,
        mounts=mounts,
        redis=redis_tel,
    )


@oslab_bp.route("/api/oslab", methods=["GET"])
def api_oslab():
    """JSON telemetry endpoint polled live by the OS Lab UI."""
    if not g.user:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    r = get_redis_client()
    # Increment counter
    try:
        r.incr(f"replica_reqs:{REPLICA_NAME}")
    except Exception as e:
        logger.warning("Could not increment replica counter: %s", e)

    return jsonify({
        "timestamp": time.time(),
        "replica_name": REPLICA_NAME,
        "cgroup": get_cgroups_info(),
        "ns": get_namespaces_info(),
        "mounts": get_overlay_and_mounts_info(),
        "redis": get_redis_telemetry(),
    })


@oslab_bp.route("/api/oslab/stress", methods=["POST"])
def trigger_stress_test():
    """Spawn a controlled 10s CPU busy-loop worker thread to demonstrate CFS throttling live."""
    if not g.user:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    r = get_redis_client()

    # Guard with Redis lock so only one stress test runs at a time
    lock_acquired = r.set("oslab_stress_lock", REPLICA_NAME, nx=True, ex=12)
    if not lock_acquired:
        current_runner = r.get("oslab_stress_lock") or "another user"
        return jsonify({
            "status": "error",
            "message": f"Stress test is already running on {current_runner}. Please wait."
        }), 429

    def busy_loop_worker():
        try:
            end_time = time.time() + 10.0
            while time.time() < end_time:
                # Busy arithmetic calculation to exhaust 0.5 CPU quota
                _ = 314159 * 271828
        finally:
            try:
                r_clean = get_redis_client()
                r_clean.delete("oslab_stress_lock")
            except Exception:
                pass

    t = threading.Thread(target=busy_loop_worker, daemon=True)
    t.start()

    logger.info("Spawned CPU stress test worker thread on %s for 10 seconds", REPLICA_NAME)
    return jsonify({
        "status": "started",
        "replica": REPLICA_NAME,
        "duration_seconds": 10,
        "message": "CPU stress test started! CFS bandwidth controller throttling will increase live."
    })
