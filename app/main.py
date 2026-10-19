"""Airutil Web Application Entrypoint.

Operating Systems Concepts Demonstrated:
1. Distributed Shared State: Server-side sessions in Redis allow load-balanced
   Flask replicas (app1, app2) to authenticate users seamlessly across requests.
2. Kernel Namespaces & cgroups: UTS, PID, and memory quotas inspected live.
3. IPC & Network Sockets: Threaded connection pooling to PostgreSQL and Redis.
4. Security & Access Control: CSRF verification and least-privilege non-root execution.
"""

from __future__ import annotations

import datetime
import logging
import os
import secrets
import socket
from functools import wraps
from typing import Any, Callable, Dict, Optional

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
    Response,
)

from app.auth import auth_bp
from app.files import files_bp
from app.vault import vault_bp
from app.db import get_db_cursor, init_db
from app.session import (
    SESSION_COOKIE_NAME,
    SessionManager,
    get_redis_client,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("airutil.main")

# Initialize Flask application instance
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "airutil_super_secret_session_key_production_grade_32bytes")

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(files_bp)
app.register_blueprint(vault_bp)

# OS Concept: Environment Variables
REPLICA_NAME: str = os.getenv("REPLICA_NAME", "standalone-app")
STORAGE_PATH: str = os.getenv("STORAGE_PATH", "/vault_storage")
DATABASE_URL: str = os.getenv("DATABASE_URL", "")
REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Auto-initialize database tables on container startup
try:
    init_db()
except Exception as err:
    logger.warning("Database init on startup deferred: %s", err)


# ==============================================================================
# Request Lifecycle & Security Middleware
# ==============================================================================

@app.before_request
def load_session_and_csrf() -> None:
    """Load session from Redis and establish CSRF token before processing request.

    OS Concept: Distributed Session State.
    Both replicas app1 and app2 query the shared airutil_redis instance.
    """
    g.replica_name = REPLICA_NAME
    g.user = None
    g.session_id = request.cookies.get(SESSION_COOKIE_NAME)

    sm = SessionManager()
    if g.session_id:
        session_data = sm.get_session(g.session_id, refresh=True)
        if session_data:
            g.user = session_data
            g.csrf_token = session_data.get("csrf_token")
        else:
            g.session_id = None

    if not hasattr(g, "csrf_token") or not g.csrf_token:
        # Generate anonymous CSRF token for unauthenticated visitors
        anon_cookie = request.cookies.get("airutil_anon_csrf")
        if anon_cookie:
            g.csrf_token = anon_cookie
        else:
            g.csrf_token = secrets.token_hex(32)

    # CSRF Verification on state-changing requests
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        # Exclude specific non-browser APIs if needed
        token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
        if not token or token != g.csrf_token:
            logger.warning("CSRF validation failed for %s from %s", request.path, request.remote_addr)
            abort(403, description="CSRF token validation failed. Please refresh and try again.")


@app.after_request
def set_security_headers_and_anon_csrf(response: Response) -> Response:
    """Set standard security headers and anonymous CSRF cookie if applicable."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["X-Served-By-Replica"] = REPLICA_NAME

    if not g.user and hasattr(g, "csrf_token"):
        response.set_cookie(
            "airutil_anon_csrf",
            g.csrf_token,
            max_age=7200,
            httponly=True,
            samesite="Lax"
        )
    return response


@app.context_processor
def inject_template_globals() -> Dict[str, Any]:
    """Inject CSRF token, replica name, and user context into all templates."""
    return {
        "csrf_token": lambda: getattr(g, "csrf_token", ""),
        "current_user": getattr(g, "user", None),
        "replica_name": REPLICA_NAME,
    }


def login_required(f: Callable) -> Callable:
    """Decorator to enforce authenticated session."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not getattr(g, "user", None):
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function


# ==============================================================================
# OS Primitives Diagnostics Helpers
# ==============================================================================

def get_cgroup_memory_limit() -> str:
    """Read cgroup v2 memory limit from /sys/fs/cgroup/memory.max."""
    try:
        cgroup_v2_path = "/sys/fs/cgroup/memory.max"
        if os.path.exists(cgroup_v2_path):
            with open(cgroup_v2_path, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val == "max":
                    return "Unlimited (max)"
                return f"{int(val) / (1024 * 1024):.1f} MiB"

        cgroup_v1_path = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
        if os.path.exists(cgroup_v1_path):
            with open(cgroup_v1_path, "r", encoding="utf-8") as f:
                return f"{int(f.read().strip()) / (1024 * 1024):.1f} MiB"
    except Exception:
        pass
    return "128.0 MiB (configured)"


def get_system_diagnostics() -> Dict[str, Any]:
    """Collect OS primitives and container isolation metadata."""
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


# ==============================================================================
# Application Routes
# ==============================================================================

@app.route("/healthz", methods=["GET"])
def healthcheck():
    """Health check endpoint for Docker HEALTHCHECK and Nginx load balancer probes."""
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
    """Landing page or direct redirect to dashboard for logged-in students."""
    if g.user:
        return redirect(url_for("dashboard"))
    diagnostics = get_system_diagnostics()
    return render_template("index.html", diag=diagnostics)


@app.route("/dashboard", methods=["GET"])
@login_required
def dashboard():
    """Protected student dashboard showing active session details and system state."""
    r = get_redis_client()
    session_ttl = 0
    if g.session_id:
        try:
            session_ttl = r.ttl(f"session:{g.session_id}")
        except Exception:
            session_ttl = 7200

    # Fetch recent activity logs for current user
    activity_entries = []
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT action, details, ip_address, created_at
                FROM activity_log
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 5
                """,
                (g.user["user_id"],)
            )
            activity_entries = cur.fetchall()
    except Exception as e:
        logger.error("Failed to load activity logs: %s", e)

    return render_template(
        "dashboard.html",
        user=g.user,
        session_id=g.session_id,
        session_ttl=session_ttl,
        activity=activity_entries,
        replica_name=REPLICA_NAME
    )


@app.route("/oslab", methods=["GET"])
def oslab():
    """OS Lab primitives live inspection view."""
    diagnostics = get_system_diagnostics()
    return render_template("oslab.html", diag=diagnostics)


@app.route("/share/<share_token>", methods=["GET"])
def share_download_public(share_token: str):
    """Public expiring share link handler.

    OS Concept: Expiring capabilities stored in fast in-memory key-value cache (Redis TTL)
    backed by relational PostgreSQL persistence.
    """
    import json
    from flask import send_file
    from app.files import format_bytes

    r = get_redis_client()
    redis_key = f"share:{share_token}"
    cached_info = None

    try:
        raw_val = r.get(redis_key)
        if raw_val:
            cached_info = json.loads(raw_val)
    except Exception as e:
        logger.warning("Redis share link cache lookup error: %s", e)

    # Query database to get latest state and file details
    share_row = None
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT s.id, s.file_id, s.user_id, s.expires_at, s.max_downloads, s.download_count,
                       f.original_name, f.storage_name, f.file_path, f.file_size, f.mime_type, f.category
                FROM share_links s
                JOIN files f ON s.file_id = f.id
                WHERE s.share_token = %s AND f.is_deleted = FALSE
                """,
                (share_token,)
            )
            share_row = cur.fetchone()
    except Exception as e:
        logger.error("DB share link lookup error: %s", e)
        abort(500)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if not share_row:
        return render_template("share_status.html", status="not_found", message="This share link does not exist or has been deleted."), 404

    if share_row["expires_at"] < now_utc:
        try:
            r.delete(redis_key)
        except Exception:
            pass
        return render_template("share_status.html", status="expired", message="This share link has expired."), 410

    if share_row["max_downloads"] is not None and share_row["download_count"] >= share_row["max_downloads"]:
        try:
            r.delete(redis_key)
        except Exception:
            pass
        return render_template("share_status.html", status="exhausted", message="This share link has reached its maximum download limit."), 410

    # If ?download=1, initiate file download and increment count
    if request.args.get("download", "0") == "1":
        file_path = share_row["file_path"]
        if not os.path.exists(file_path):
            abort(404, description="Target file not found on disk")

        try:
            with get_db_cursor(commit=True) as cur:
                cur.execute(
                    "UPDATE share_links SET download_count = download_count + 1 WHERE id = %s",
                    (share_row["id"],)
                )
                cur.execute(
                    "INSERT INTO activity_log (user_id, action, details, ip_address) VALUES (%s, %s, %s, %s)",
                    (share_row["user_id"], "share_download", f"Downloaded '{share_row['original_name']}' via share link", request.remote_addr)
                )
        except Exception as e:
            logger.error("Failed to update download count: %s", e)

        # Update redis cache download count
        if cached_info:
            cached_info["download_count"] = cached_info.get("download_count", 0) + 1
            if share_row["max_downloads"] and cached_info["download_count"] >= share_row["max_downloads"]:
                try:
                    r.delete(redis_key)
                except Exception:
                    pass
            else:
                ttl = r.ttl(redis_key)
                if ttl > 0:
                    r.set(redis_key, json.dumps(cached_info), ex=ttl)

        resp = send_file(
            file_path,
            as_attachment=True,
            download_name=share_row["original_name"] or share_row["storage_name"],
            mimetype=share_row["mime_type"] or "application/octet-stream"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    # Render landing page
    time_left = share_row["expires_at"] - now_utc
    hours_left = max(0, int(time_left.total_seconds() // 3600))
    minutes_left = max(0, int((time_left.total_seconds() % 3600) // 60))

    return render_template(
        "share.html",
        share=share_row,
        share_token=share_token,
        formatted_size=format_bytes(share_row["file_size"]),
        hours_left=hours_left,
        minutes_left=minutes_left,
    )





@app.route("/beam", methods=["GET"])
@login_required
def beam():
    """Clip Beam placeholder for Phase 5."""
    return render_template("placeholder.html", module_name="Burn-on-Read Clip Beam", phase_num=5)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
