"""Showcase Blueprint Routes: /terminal, /api/terminal/run (SSE), and /internal/oslab.

Operating Systems & Security Concepts Demonstrated:
1. Role-Based Access Control (RBAC): Terminal access restricted exclusively to authenticated
   administrators (first registered user).
2. Server-Sent Events (SSE) Streaming: Real-time unidirectional streaming from kernel/container
   probes directly to browser terminal without polling overhead.
3. Protected Internal IPC Interconnect: Direct inter-container communication via private token
   authentication without traversing public reverse proxy.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    stream_with_context,
    url_for,
)

from app.db import is_user_admin
from app.oslab import (
    get_cgroups_info,
    get_namespaces_info,
    get_overlay_and_mounts_info,
    read_file_safe,
)
from app.showcase.engine import ShowcaseEngine

logger = logging.getLogger("airutil.showcase.routes")

showcase_bp = Blueprint("showcase", __name__)

SHOWCASE_ENABLED = os.getenv("SHOWCASE_ENABLED", "true").lower() in ("true", "1", "yes")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "airutil_internal_telemetry_token_64bytes_production")
REPLICA_NAME = os.getenv("REPLICA_NAME", "standalone-app")


@showcase_bp.route("/terminal", methods=["GET"])
def view_terminal():
    """Render in-app Docker Terminal Showcase view (Admin Only)."""
    if not SHOWCASE_ENABLED:
        abort(404, description="Docker Terminal Showcase is disabled in this deployment environment.")

    if not getattr(g, "user", None):
        flash("Please log in to access the Docker Terminal Showcase.", "warning")
        return redirect(url_for("auth.login", next=request.url))

    user_id = g.user.get("user_id")
    if not is_user_admin(user_id):
        abort(403, description="Access Denied: Terminal showcase is restricted to system administrators.")

    return render_template(
        "terminal.html",
        replica_name=REPLICA_NAME,
        user=g.user,
        showcase_enabled=SHOWCASE_ENABLED,
    )


@showcase_bp.route("/api/terminal/run", methods=["POST"])
def api_terminal_run():
    """Stream command output via Server-Sent Events (SSE)."""
    if not SHOWCASE_ENABLED:
        return jsonify({"error": "Showcase disabled"}), 404

    if not getattr(g, "user", None):
        return jsonify({"error": "Unauthorized"}), 401

    user_id = g.user.get("user_id")
    if not is_user_admin(user_id):
        return jsonify({"error": "Forbidden: Admin privileges required"}), 403

    payload = request.get_json(silent=True) or request.form or {}
    cmd = (payload.get("cmd") or payload.get("command") or "").strip()
    if not cmd:
        return jsonify({"error": "Command is required"}), 400

    client_ip = request.headers.get("X-Real-IP") or request.remote_addr or "127.0.0.1"
    engine = ShowcaseEngine(user_id=user_id, client_ip=client_ip)

    def generate_sse():
        try:
            for chunk in engine.run_command_stream(cmd):
                # Send SSE data frame
                data = json.dumps({"output": chunk})
                yield f"data: {data}\n\n"
        except Exception as e:
            logger.error("Error streaming terminal command: %s", e)
            err_data = json.dumps({"output": f"\r\n\x1b[31mInternal streaming error: {e}\x1b[0m\r\n"})
            yield f"data: {err_data}\n\n"
        # Terminate stream
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate_sse()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


@showcase_bp.route("/internal/oslab", methods=["GET"])
def internal_oslab():
    """Internal endpoint queried by replica peer to read /proc and /sys safely.

    Protected by shared internal token (not exposed through public reverse proxy).
    """
    token = request.headers.get("X-Internal-Token")
    if not token or token != INTERNAL_API_TOKEN:
        logger.warning("Unauthorized internal oslab probe from %s", request.remote_addr)
        abort(403, description="Invalid internal authentication token")

    metric = request.args.get("metric", "all").lower()

    if metric == "ns":
        ns_info = get_namespaces_info()
        lines = [
            f"=== Namespace Inodes ({REPLICA_NAME}) ===",
            f"UTS Hostname : {ns_info.get('hostname')}",
            f"Container PID: {ns_info.get('pid')} (Parent: {ns_info.get('ppid')})",
            f"PID 1 Cmdline: {ns_info.get('pid1_cmd')}",
            "",
            "Namespace Symlinks (/proc/self/ns/*):"
        ]
        for k, v in ns_info.get("namespaces", {}).items():
            lines.append(f"  {k:18s} -> {v}")
        output = "\r\n".join(lines) + "\r\n"

    elif metric == "cgroup":
        cg = get_cgroups_info()
        lines = [
            f"=== cgroups v2 Limits & Stat ({REPLICA_NAME}) ===",
            f"Memory Max    : {cg.get('memory_limit_human')} ({cg.get('memory_limit_bytes')} bytes)",
            f"Memory Current: {cg.get('memory_current_human')} ({cg.get('memory_percent')}%)",
            f"CPU Limit     : {cg.get('cpu_cores_limit')} Cores (Quota: {cg.get('cpu_quota_usec')}µs / Period: {cg.get('cpu_period_usec')}µs)",
            f"Throttled     : {cg.get('nr_throttled')} times ({cg.get('throttled_sec')}s / {cg.get('throttled_percent')}%)",
            f"Total Periods : {cg.get('nr_periods')}",
        ]
        output = "\r\n".join(lines) + "\r\n"

    elif metric == "mounts":
        mounts = get_overlay_and_mounts_info()
        overlay = mounts.get("overlay", {})
        vol = mounts.get("volume", {})
        lines = [
            f"=== Filesystem & Mountinfo ({REPLICA_NAME}) ===",
            f"OverlayFS Found : {overlay.get('found')}",
            f"Lower Layers    : {overlay.get('lower_layers_count')} read-only Docker image layers",
            f"LowerDir        : {overlay.get('lowerdir')[:100]}...",
            f"UpperDir (COW)  : {overlay.get('upperdir') or '(None: rootfs is read-only)'}",
            f"Volume Mount    : {vol.get('mount_point')} ({vol.get('filesystem')})",
        ]
        output = "\r\n".join(lines) + "\r\n"

    else:
        output = f"Unknown metric '{metric}'. Expected 'ns', 'cgroup', or 'mounts'.\r\n"

    return jsonify({
        "status": "ok",
        "replica": REPLICA_NAME,
        "metric": metric,
        "output": output,
    })
