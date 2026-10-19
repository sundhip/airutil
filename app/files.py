"""Airutil Smart AirDrop & Semester Organizer (Phase 3).

Operating Systems Concepts Demonstrated:
1. Shared Volume File System Persistence: Containers app1, app2, and worker all mount
   /vault_storage (airutil_data volume) concurrently, allowing cross-replica reads/writes.
2. Kernel I/O and File Attributes: Safe file naming, atomic moves, MIME magic inspection,
   and block storage quota enforcement.
3. Asynchronous Worker IPC: CPU-heavy thumbnailing and PDF text extraction are offloaded
   to the RQ worker pool via Redis message broker.
4. Full-Text Inverted Indexing: Document content and tags indexed via PostgreSQL GIN indexes
   for sub-millisecond retrieval.
5. Least-Privilege & Defense-in-Depth: Safe MIME classification and Content-Disposition
   headers prevent inline execution of untrusted binary uploads.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
from typing import Any, Dict, List, Optional, Tuple

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
    send_file,
    url_for,
)
from rq import Queue
from werkzeug.utils import secure_filename

from app.db import get_db_cursor
from app.session import get_redis_client

try:
    import magic
except ImportError:
    magic = None

try:
    import puremagic
except ImportError:
    puremagic = None

try:
    import qrcode
except ImportError:
    qrcode = None

logger = logging.getLogger("airutil.files")

files_bp = Blueprint("files", __name__)

STORAGE_PATH: str = os.getenv("STORAGE_PATH", "/vault_storage")
MAX_FILE_SIZE: int = 100 * 1024 * 1024  # 100 MB max per upload
USER_QUOTA_BYTES: int = 500 * 1024 * 1024  # 500 MB quota per user

# Course regex: e.g. CSE1005, BCSE302L, MAT1001, CS101, ECE2001P
# Allows word boundaries as well as delimiters like underscores (_), hyphens (-), and dots (.)
COURSE_REGEX = re.compile(r"(?:^|[^A-Za-z0-9])([A-Za-z]{2,5}\s?\d{3,4}[A-Za-z]?)(?:$|[^A-Za-z0-9])")

CATEGORIES = ["Documents", "Images", "Code", "Archives", "Videos", "Audio", "Others"]


# ==============================================================================
# Helper Utilities
# ==============================================================================

def detect_mime_type(file_stream_or_path: Any, filename: str) -> str:
    """Detect actual MIME type using magic header bytes with multiple fallbacks.

    OS Concept: File magic signatures vs user-supplied filename extensions.
    Prevents extension spoofing (e.g. .exe renamed to .jpg).
    """
    header = b""
    if hasattr(file_stream_or_path, "read"):
        pos = file_stream_or_path.tell()
        header = file_stream_or_path.read(4096)
        file_stream_or_path.seek(pos)
    elif isinstance(file_stream_or_path, (str, bytes)) and os.path.isfile(file_stream_or_path):
        with open(file_stream_or_path, "rb") as f:
            header = f.read(4096)

    # 1. Inspect direct binary headers for known security-critical types
    if header.startswith(b"MZ"):  # DOS/Windows PE Executable
        return "application/x-dosexec"
    if header.startswith(b"\x7fELF"):  # Linux ELF Executable
        return "application/x-executable"

    # 2. Try python-magic (libmagic C library binding)
    if magic and header:
        try:
            detected = magic.from_buffer(header, mime=True)
            if detected:
                return detected
        except Exception as e:
            logger.debug("python-magic buffer check failed: %s", e)

    # 3. Try puremagic pure-python library
    if puremagic and header:
        try:
            matches = puremagic.magic_string(header)
            if matches:
                return matches[0].mime_type
        except Exception:
            pass

    # 4. Fallback based on extension
    ext = os.path.splitext(filename)[1].lower()
    fallback_map = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".py": "text/x-python",
        ".c": "text/x-c",
        ".cpp": "text/x-c++",
        ".java": "text/x-java-source",
        ".json": "application/json",
        ".html": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".zip": "application/zip",
        ".tar": "application/x-tar",
        ".gz": "application/gzip",
        ".mp4": "video/mp4",
        ".mp3": "audio/mpeg",
        ".exe": "application/x-dosexec",
    }
    return fallback_map.get(ext, "application/octet-stream")


def classify_category(mime_type: str, filename: str) -> str:
    """Classify file into standard categories based on true MIME type and extension."""
    mime = (mime_type or "").lower()
    ext = os.path.splitext(filename)[1].lower()

    # Executables must never be classified as Images or Documents
    if mime in (
        "application/x-dosexec",
        "application/x-msdownload",
        "application/x-executable",
        "application/x-mach-binary",
        "application/x-bat",
        "application/x-sh",
    ):
        return "Others"

    if mime.startswith("image/"):
        return "Images"
    if (
        mime == "application/pdf"
        or mime.startswith("application/msword")
        or "officedocument" in mime
        or mime == "application/rtf"
        or ext in (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx")
    ):
        return "Documents"
    if (
        mime.startswith("text/")
        or ext in (
            ".py", ".c", ".cpp", ".h", ".hpp", ".java", ".rs", ".go",
            ".html", ".css", ".js", ".ts", ".json", ".sql", ".sh", ".yaml", ".yml", ".md"
        )
    ):
        return "Code"
    if (
        mime in ("application/zip", "application/x-tar", "application/gzip", "application/x-7z-compressed", "application/x-rar-compressed")
        or ext in (".zip", ".tar", ".gz", ".tgz", ".7z", ".rar")
    ):
        return "Archives"
    if mime.startswith("video/") or ext in (".mp4", ".mkv", ".webm", ".avi", ".mov"):
        return "Videos"
    if mime.startswith("audio/") or ext in (".mp3", ".wav", ".ogg", ".m4a", ".flac"):
        return "Audio"
    return "Others"


def extract_course_from_filename(filename: str) -> Optional[str]:
    """Detect university course code from filename using regex."""
    match = COURSE_REGEX.search(filename)
    if match:
        code = match.group(1)
        return re.sub(r"\s+", "", code).upper()
    return None


def get_user_storage_usage(user_id: int) -> int:
    """Calculate total non-deleted file bytes for user."""
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT COALESCE(SUM(file_size), 0) AS total FROM files WHERE user_id = %s AND is_deleted = FALSE",
                (user_id,)
            )
            row = cur.fetchone()
            return int(row["total"]) if row else 0
    except Exception as e:
        logger.error("Failed to query user storage usage: %s", e)
        return 0


def format_bytes(num_bytes: int) -> str:
    """Format bytes into human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


# ==============================================================================
# File Management Routes
# ==============================================================================

@files_bp.route("/files", methods=["GET"])
def list_files():
    """Main Files page: grid/list views, search, filters, course selector, and quota."""
    if not g.user:
        flash("Please log in to access your files.", "warning")
        return redirect(url_for("auth.login", next=request.url))

    user_id = g.user["user_id"]
    category_filter = request.args.get("category", "").strip()
    course_filter = request.args.get("course", "").strip()
    tag_filter = request.args.get("tag", "").strip()
    search_query = request.args.get("q", "").strip()
    view_mode = request.args.get("view", "grid")
    show_trash = request.args.get("trash", "0") == "1"

    # Fetch user courses for dropdown
    courses: List[Dict[str, Any]] = []
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT id, course_code, course_name, semester FROM courses WHERE user_id = %s ORDER BY semester, course_code",
                (user_id,)
            )
            courses = cur.fetchall() or []
    except Exception as e:
        logger.error("Failed to fetch courses: %s", e)

    # Compute storage usage and quota
    used_bytes = get_user_storage_usage(user_id)
    quota_percent = min(100.0, (used_bytes / USER_QUOTA_BYTES) * 100.0)

    # Query user files with filters and full-text search
    files_list: List[Dict[str, Any]] = []
    try:
        with get_db_cursor(commit=False) as cur:
            conditions = ["user_id = %s"]
            params: List[Any] = [user_id]

            if show_trash:
                conditions.append("is_deleted = TRUE")
            else:
                conditions.append("is_deleted = FALSE")

            if category_filter and category_filter in CATEGORIES:
                conditions.append("category = %s")
                params.append(category_filter)

            if course_filter:
                conditions.append("course = %s")
                params.append(course_filter)

            if tag_filter:
                conditions.append("%s = ANY(tags)")
                params.append(tag_filter)

            if search_query:
                # PostgreSQL Full-Text Search over search_vector
                conditions.append("search_vector @@ plainto_tsquery('english', %s)")
                params.append(search_query)
                order_by = "ts_rank(search_vector, plainto_tsquery('english', %s)) DESC, created_at DESC"
                params.append(search_query)
            else:
                order_by = "created_at DESC"

            sql = f"""
                SELECT id, original_name, storage_name, file_size, mime_type, category,
                       course, sha256, tags, is_deleted, deleted_at, thumbnail_path,
                       created_at, updated_at
                FROM files
                WHERE {' AND '.join(conditions)}
                ORDER BY {order_by}
            """
            cur.execute(sql, tuple(params))
            files_list = cur.fetchall() or []
    except Exception as e:
        logger.error("Failed to fetch files: %s", e)
        flash("Could not load files due to database error.", "danger")

    # Format human-readable file sizes
    for f in files_list:
        f["formatted_size"] = format_bytes(f["file_size"])
        f["has_thumb"] = bool(f.get("thumbnail_path") and os.path.exists(f["thumbnail_path"]))

    return render_template(
        "files.html",
        files=files_list,
        courses=courses,
        categories=CATEGORIES,
        current_category=category_filter,
        current_course=course_filter,
        current_tag=tag_filter,
        search_query=search_query,
        view_mode=view_mode,
        show_trash=show_trash,
        used_bytes=used_bytes,
        quota_bytes=USER_QUOTA_BYTES,
        quota_percent=round(quota_percent, 1),
        formatted_used=format_bytes(used_bytes),
        formatted_quota=format_bytes(USER_QUOTA_BYTES),
    )


@files_bp.route("/files/upload", methods=["POST"])
def upload_file():
    """Handle drag-and-drop or picker multi-file upload with MIME inspection and background processing."""
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = g.user["user_id"]
    if "files" not in request.files and "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    uploaded_files = request.files.getlist("files") or request.files.getlist("file")
    if not uploaded_files or not uploaded_files[0].filename:
        return jsonify({"error": "No file selected"}), 400

    r = get_redis_client()
    rq_queue = Queue("default", connection=r)

    # Last course fallback from form or session
    form_course = request.form.get("course", "").strip()
    if form_course:
        r.set(f"user:{user_id}:last_course", form_course, ex=86400 * 30)

    results = []
    current_used = get_user_storage_usage(user_id)

    for file_obj in uploaded_files:
        original_name = file_obj.filename or "uploaded_file"
        # Sanitize filename
        safe_name = secure_filename(original_name)
        if not safe_name:
            safe_name = "file_" + secrets.token_hex(4)

        # Inspect stream length & enforce single file upload limit
        file_obj.seek(0, os.SEEK_END)
        file_size = file_obj.tell()
        file_obj.seek(0)

        if file_size > MAX_FILE_SIZE:
            results.append({
                "filename": original_name,
                "status": "error",
                "message": f"File exceeds 100 MB limit ({format_bytes(file_size)})"
            })
            continue

        if current_used + file_size > USER_QUOTA_BYTES:
            results.append({
                "filename": original_name,
                "status": "error",
                "message": "Storage quota of 500 MB exceeded"
            })
            continue

        # Real MIME detection (independent of file extension)
        mime_type = detect_mime_type(file_obj, safe_name)
        category = classify_category(mime_type, safe_name)

        # Course detection: regex priority, then form input, then last remembered course, fallback to General
        auto_course = extract_course_from_filename(original_name)
        if auto_course:
            assigned_course = auto_course
        elif form_course:
            assigned_course = form_course
        else:
            cached_course = r.get(f"user:{user_id}:last_course")
            assigned_course = cached_course if cached_course else "General"

        # Read content, compute SHA-256 for duplicate detection
        hasher = hashlib.sha256()
        file_bytes = file_obj.read()
        hasher.update(file_bytes)
        file_sha256 = hasher.hexdigest()

        # Check for existing duplicate content
        duplicate_warning = None
        try:
            with get_db_cursor(commit=False) as cur:
                cur.execute(
                    "SELECT id, original_name, course, category FROM files WHERE user_id = %s AND sha256 = %s AND is_deleted = FALSE",
                    (user_id, file_sha256)
                )
                dup = cur.fetchone()
                if dup:
                    duplicate_warning = f"Identical file already exists: '{dup['original_name']}' in {dup['course']}"
        except Exception as e:
            logger.warning("Duplicate check failed: %s", e)

        # Build directory path: /vault_storage/files/<user_id>/<Category>/<Course>/
        dest_dir = os.path.join(STORAGE_PATH, "files", str(user_id), category, assigned_course)
        os.makedirs(dest_dir, exist_ok=True)

        name_part, ext_part = os.path.splitext(safe_name)
        unique_suffix = secrets.token_hex(6)
        storage_name = f"{name_part}_{unique_suffix}{ext_part}"
        dest_path = os.path.join(dest_dir, storage_name)

        # Save physical file to shared volume
        with open(dest_path, "wb") as out_f:
            out_f.write(file_bytes)

        # Insert record into PostgreSQL
        file_id = None
        try:
            with get_db_cursor(commit=True) as cur:
                cur.execute(
                    """
                    INSERT INTO files (
                        user_id, filename, original_name, storage_name, file_path,
                        file_size, mime_type, category, course, sha256, is_deleted
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE)
                    RETURNING id
                    """,
                    (
                        user_id, safe_name, original_name, storage_name, dest_path,
                        file_size, mime_type, category, assigned_course, file_sha256
                    )
                )
                row = cur.fetchone()
                if row:
                    file_id = row["id"]

                # Log activity
                cur.execute(
                    "INSERT INTO activity_log (user_id, action, details, ip_address) VALUES (%s, %s, %s, %s)",
                    (user_id, "upload_file", f"Uploaded '{original_name}' ({format_bytes(file_size)}) to {assigned_course}", request.remote_addr)
                )
        except Exception as e:
            logger.error("Failed to insert file record: %s", e)
            if os.path.exists(dest_path):
                os.remove(dest_path)
            results.append({
                "filename": original_name,
                "status": "error",
                "message": "Database insertion error"
            })
            continue

        # Enqueue background processing task to RQ worker
        if file_id:
            try:
                from worker.tasks import process_file_task
                rq_queue.enqueue(process_file_task, file_id)
            except Exception as e:
                logger.error("Failed to enqueue worker task for file_id=%d: %s", file_id, e)

        current_used += file_size
        results.append({
            "id": file_id,
            "filename": original_name,
            "storage_name": storage_name,
            "category": category,
            "course": assigned_course,
            "mime_type": mime_type,
            "size": format_bytes(file_size),
            "status": "success",
            "duplicate_warning": duplicate_warning,
        })

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({"results": results}), 200

    # Normal form submission redirect
    for res in results:
        if res.get("duplicate_warning"):
            flash(f"Uploaded {res['filename']} (Warning: {res['duplicate_warning']})", "warning")
        elif res["status"] == "success":
            flash(f"Successfully uploaded {res['filename']} to {res['course']}", "success")
        else:
            flash(f"Upload failed for {res['filename']}: {res.get('message')}", "danger")

    return redirect(url_for("files.list_files"))


@files_bp.route("/files/<int:file_id>/download", methods=["GET"])
def download_file(file_id: int):
    """Serve file download with defense-in-depth headers.

    OS Concept: Blocking executable execution in browser via Content-Disposition & nosniff.
    """
    if not g.user:
        abort(401)

    user_id = g.user["user_id"]
    file_record = None
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT id, original_name, storage_name, file_path, mime_type, category FROM files WHERE id = %s AND user_id = %s",
                (file_id, user_id)
            )
            file_record = cur.fetchone()
    except Exception as e:
        logger.error("Download query failed: %s", e)
        abort(500)

    if not file_record or not os.path.exists(file_record["file_path"]):
        abort(404, description="File not found")

    mime = file_record["mime_type"] or "application/octet-stream"
    filename = file_record["original_name"] or file_record["storage_name"]

    # Security rule: Executables, scripts, HTML, and binary formats are forced attachment
    force_attachment = True
    if request.args.get("inline", "0") == "1" and mime.startswith(("image/", "text/plain")):
        force_attachment = False

    resp = send_file(
        file_record["file_path"],
        as_attachment=force_attachment,
        download_name=filename,
        mimetype=mime,
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@files_bp.route("/files/<int:file_id>/thumbnail", methods=["GET"])
def get_thumbnail(file_id: int):
    """Serve generated thumbnail from shared volume."""
    if not g.user:
        abort(401)

    user_id = g.user["user_id"]
    file_record = None
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT id, thumbnail_path, category, mime_type FROM files WHERE id = %s AND user_id = %s",
                (file_id, user_id)
            )
            file_record = cur.fetchone()
    except Exception as e:
        abort(500)

    if not file_record:
        abort(404)

    thumb_path = file_record.get("thumbnail_path")
    if thumb_path and os.path.exists(thumb_path):
        resp = send_file(thumb_path, mimetype="image/png")
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    # Fallback 1x1 transparent PNG or placeholder
    abort(404)


@files_bp.route("/files/<int:file_id>/preview", methods=["GET"])
def preview_file(file_id: int):
    """Return text or code preview for file modal."""
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = g.user["user_id"]
    file_record = None
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT id, original_name, category, mime_type, file_path, extracted_text FROM files WHERE id = %s AND user_id = %s",
                (file_id, user_id)
            )
            file_record = cur.fetchone()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not file_record:
        return jsonify({"error": "File not found"}), 404

    content = file_record.get("extracted_text") or ""
    if not content and os.path.exists(file_record["file_path"]):
        # Attempt to read direct text snippet if small
        try:
            with open(file_record["file_path"], "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(20000)
        except Exception:
            content = "[Binary preview unavailable]"

    return jsonify({
        "id": file_record["id"],
        "name": file_record["original_name"],
        "category": file_record["category"],
        "mime_type": file_record["mime_type"],
        "content": content,
    })


@files_bp.route("/files/<int:file_id>/rename", methods=["POST"])
def rename_file(file_id: int):
    """Rename user's display filename."""
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = g.user["user_id"]
    new_name = request.form.get("name", "").strip()
    if not new_name:
        return jsonify({"error": "Filename cannot be empty"}), 400

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE files SET original_name = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s AND user_id = %s",
                (new_name, file_id, user_id)
            )
        flash(f"Renamed file to '{new_name}'", "success")
    except Exception as e:
        logger.error("Rename failed: %s", e)
        flash("Could not rename file", "danger")

    return redirect(url_for("files.list_files"))


@files_bp.route("/files/<int:file_id>/move", methods=["POST"])
def move_file(file_id: int):
    """Move file to another course and atomically rename disk path.

    OS Concept: Atomic rename across directory structures within the same filesystem mount.
    """
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = g.user["user_id"]
    new_course = request.form.get("course", "").strip() or "General"

    file_record = None
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT id, original_name, storage_name, file_path, category, course FROM files WHERE id = %s AND user_id = %s",
                (file_id, user_id)
            )
            file_record = cur.fetchone()
    except Exception as e:
        logger.error("Move lookup failed: %s", e)
        flash("Database error", "danger")
        return redirect(url_for("files.list_files"))

    if not file_record:
        flash("File not found", "warning")
        return redirect(url_for("files.list_files"))

    old_path = file_record["file_path"]
    category = file_record["category"]
    storage_name = file_record["storage_name"]

    new_dir = os.path.join(STORAGE_PATH, "files", str(user_id), category, new_course)
    os.makedirs(new_dir, exist_ok=True)
    new_path = os.path.join(new_dir, storage_name)

    # Perform atomic filesystem move if old file exists
    if os.path.exists(old_path) and old_path != new_path:
        try:
            shutil.move(old_path, new_path)
        except Exception as e:
            logger.error("Failed to move physical file from %s to %s: %s", old_path, new_path, e)
            flash("Failed to move physical file on disk", "danger")
            return redirect(url_for("files.list_files"))

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE files SET course = %s, file_path = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s AND user_id = %s",
                (new_course, new_path, file_id, user_id)
            )
        flash(f"Moved '{file_record['original_name']}' to {new_course}", "success")
    except Exception as e:
        logger.error("Database update failed for move: %s", e)
        flash("Could not update database record", "danger")

    return redirect(url_for("files.list_files"))


@files_bp.route("/files/<int:file_id>/tags", methods=["POST"])
def update_tags(file_id: int):
    """Update file tags."""
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = g.user["user_id"]
    raw_tags = request.form.get("tags", "").strip()
    # Split comma separated tags
    tag_list = [t.strip().lower() for t in raw_tags.split(",") if t.strip()]

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE files SET tags = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s AND user_id = %s",
                (tag_list, file_id, user_id)
            )
        flash("Tags updated successfully", "success")
    except Exception as e:
        logger.error("Tags update failed: %s", e)
        flash("Could not update tags", "danger")

    return redirect(url_for("files.list_files"))


@files_bp.route("/files/<int:file_id>/delete", methods=["POST"])
def delete_file(file_id: int):
    """Soft delete file into trash (with 7-day retention)."""
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = g.user["user_id"]
    permanent = request.form.get("permanent", "0") == "1"

    if permanent:
        # Permanent deletion from disk and DB
        try:
            with get_db_cursor(commit=True) as cur:
                cur.execute(
                    "SELECT file_path, thumbnail_path FROM files WHERE id = %s AND user_id = %s",
                    (file_id, user_id)
                )
                rec = cur.fetchone()
                if rec:
                    if rec["file_path"] and os.path.exists(rec["file_path"]):
                        os.remove(rec["file_path"])
                    if rec["thumbnail_path"] and os.path.exists(rec["thumbnail_path"]):
                        os.remove(rec["thumbnail_path"])
                cur.execute("DELETE FROM files WHERE id = %s AND user_id = %s", (file_id, user_id))
            flash("File permanently deleted", "info")
        except Exception as e:
            logger.error("Permanent delete error: %s", e)
            flash("Failed to permanently delete file", "danger")
    else:
        # Soft delete
        try:
            with get_db_cursor(commit=True) as cur:
                cur.execute(
                    "UPDATE files SET is_deleted = TRUE, deleted_at = CURRENT_TIMESTAMP WHERE id = %s AND user_id = %s",
                    (file_id, user_id)
                )
            flash("File moved to Trash (retained for 7 days)", "info")
        except Exception as e:
            logger.error("Soft delete error: %s", e)
            flash("Failed to delete file", "danger")

    return redirect(url_for("files.list_files", trash="1" if permanent else "0"))


@files_bp.route("/files/<int:file_id>/restore", methods=["POST"])
def restore_file(file_id: int):
    """Restore file from trash back to active files."""
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = g.user["user_id"]
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE files SET is_deleted = FALSE, deleted_at = NULL WHERE id = %s AND user_id = %s",
                (file_id, user_id)
            )
        flash("File restored from Trash", "success")
    except Exception as e:
        logger.error("Restore failed: %s", e)
        flash("Failed to restore file", "danger")

    return redirect(url_for("files.list_files"))


# ==============================================================================
# Expiring Share Links & QR Codes
# ==============================================================================

@files_bp.route("/files/<int:file_id>/share", methods=["POST"])
def create_share_link(file_id: int):
    """Generate expiring share link with optional max download limit and QR code.

    OS Concept: Expiring capabilities stored in fast in-memory key-value cache (Redis TTL)
    backed by relational PostgreSQL persistence.
    """
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = g.user["user_id"]
    duration_str = request.form.get("duration", "1d")
    max_downloads_str = request.form.get("max_downloads", "")

    # Map duration to seconds
    durations = {
        "1h": 3600,
        "1d": 86400,
        "7d": 604800,
    }
    ttl_seconds = durations.get(duration_str, 86400)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=ttl_seconds)

    max_downloads = None
    if max_downloads_str and max_downloads_str.isdigit():
        max_downloads = int(max_downloads_str)

    # Verify file ownership
    file_record = None
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT id, original_name FROM files WHERE id = %s AND user_id = %s AND is_deleted = FALSE",
                (file_id, user_id)
            )
            file_record = cur.fetchone()
    except Exception as e:
        abort(500)

    if not file_record:
        abort(404, description="File not found")

    share_token = secrets.token_urlsafe(32)

    # 1. Store in PostgreSQL
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO share_links (user_id, file_id, share_token, expires_at, max_downloads, download_count)
                VALUES (%s, %s, %s, %s, %s, 0)
                """,
                (user_id, file_id, share_token, expires_at, max_downloads)
            )
    except Exception as e:
        logger.error("Failed to insert share link: %s", e)
        flash("Could not generate share link", "danger")
        return redirect(url_for("files.list_files"))

    # 2. Cache in Redis with auto-expiring TTL
    r = get_redis_client()
    redis_payload = {
        "file_id": file_id,
        "user_id": user_id,
        "max_downloads": max_downloads,
        "download_count": 0,
        "expires_at": expires_at.isoformat(),
    }
    r.set(f"share:{share_token}", json.dumps(redis_payload), ex=ttl_seconds)

    # 3. Generate QR code pointing to public share link
    share_url = url_for("share_download_public", share_token=share_token, _external=True)
    qr_dir = os.path.join(STORAGE_PATH, "qr")
    os.makedirs(qr_dir, exist_ok=True)
    qr_path = os.path.join(qr_dir, f"{share_token}.png")

    if qrcode:
        try:
            qr = qrcode.QRCode(version=1, box_size=8, border=3)
            qr.add_data(share_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            img.save(qr_path)
        except Exception as e:
            logger.warning("Failed to generate QR code: %s", e)

    flash(f"Share link generated! Valid for {duration_str}.", "success")
    return redirect(url_for("files.list_files", shared_token=share_token))


@files_bp.route("/share/<share_token>/qr", methods=["GET"])
def get_share_qr(share_token: str):
    """Serve generated QR code image for a share link."""
    qr_path = os.path.join(STORAGE_PATH, "qr", f"{share_token}.png")
    if os.path.exists(qr_path):
        return send_file(qr_path, mimetype="image/png")
    abort(404)


# ==============================================================================
# Semester & Course Management Routes
# ==============================================================================

@files_bp.route("/courses", methods=["GET"])
def list_courses():
    """Courses & Semesters organization page."""
    if not g.user:
        flash("Please log in to manage courses.", "warning")
        return redirect(url_for("auth.login", next=request.url))

    user_id = g.user["user_id"]
    courses_by_semester: Dict[str, List[Dict[str, Any]]] = {}

    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT c.id, c.course_code, c.course_name, c.semester, c.created_at,
                       COUNT(f.id) AS file_count
                FROM courses c
                LEFT JOIN files f ON f.course = c.course_code AND f.user_id = c.user_id AND f.is_deleted = FALSE
                WHERE c.user_id = %s
                GROUP BY c.id
                ORDER BY c.semester, c.course_code
                """,
                (user_id,)
            )
            rows = cur.fetchall() or []
            for row in rows:
                sem = row["semester"]
                if sem not in courses_by_semester:
                    courses_by_semester[sem] = []
                courses_by_semester[sem].append(row)
    except Exception as e:
        logger.error("Failed to load courses: %s", e)
        flash("Could not load courses", "danger")

    return render_template("courses.html", semesters=courses_by_semester)


@files_bp.route("/courses/add", methods=["POST"])
def add_course():
    """Add a new course under a semester."""
    if not g.user:
        abort(401)

    user_id = g.user["user_id"]
    course_code = request.form.get("course_code", "").strip().upper()
    course_name = request.form.get("course_name", "").strip()
    semester = request.form.get("semester", "").strip()

    if not course_code or not course_name or not semester:
        flash("Course code, name, and semester are required.", "danger")
        return redirect(url_for("files.list_courses"))

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO courses (user_id, course_code, course_name, semester)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, course_code) DO UPDATE
                SET course_name = EXCLUDED.course_name, semester = EXCLUDED.semester
                """,
                (user_id, course_code, course_name, semester)
            )
        flash(f"Course {course_code} added successfully!", "success")
    except Exception as e:
        logger.error("Failed to add course: %s", e)
        flash("Failed to add course", "danger")

    return redirect(url_for("files.list_courses"))


@files_bp.route("/courses/<int:course_id>/delete", methods=["POST"])
def delete_course(course_id: int):
    """Delete course definition."""
    if not g.user:
        abort(401)

    user_id = g.user["user_id"]
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("DELETE FROM courses WHERE id = %s AND user_id = %s", (course_id, user_id))
        flash("Course removed", "info")
    except Exception as e:
        logger.error("Delete course error: %s", e)
        flash("Failed to delete course", "danger")

    return redirect(url_for("files.list_courses"))
