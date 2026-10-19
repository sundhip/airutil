"""Airutil Burn-on-Read Clip Beam Module (Phase 5).

Operating Systems Concepts Demonstrated:
1. Atomic Read-and-Destroy (Redis GETDEL):
   Guarantees race-free linearizability across distributed clients and load-balanced replicas.
   The first reader consumes and destroys the message in a single atomic instruction.
2. Pure Ephemeral Memory Buffering (Zero-Disk Footprint):
   Sensitive clipboard clips exist only in volatile RAM buffers.
   Never persisted in PostgreSQL, never written to disk or the /vault_storage volume.
3. Linearizable Distributed State:
   Both app1 and app2 communicate with airutil_redis, ensuring cross-replica synchronization.
4. Input Boundary Clamping & Rate Limiting:
   Enforces a strict 5 KB payload cap and Redis-backed sliding window rate limits.
"""

from __future__ import annotations

import base64
import html
import io
import json
import logging
import os
import re
import secrets
import time
from typing import Any, Dict, Optional, Tuple

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

try:
    import qrcode
except ImportError:
    qrcode = None

logger = logging.getLogger("airutil.beam")

beam_bp = Blueprint("beam", __name__)

MAX_BEAM_BYTES: int = 5 * 1024  # 5 KB max text payload
TTL_MAP = {
    "60s": 60,
    "5m": 300,
    "15m": 900,
}


def generate_qr_data_uri(url: str) -> str:
    """Generate in-memory QR code PNG as base64 data URI (never written to disk)."""
    if not qrcode:
        return ""
    try:
        qr = qrcode.QRCode(version=1, box_size=6, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{b64_str}"
    except Exception as e:
        logger.warning("In-memory QR generation failed: %s", e)
        return ""


@beam_bp.route("/beam", methods=["GET"])
def index():
    """Sender Clip Beam creation page & active beam tracking."""
    active_token = request.args.get("active_token", "").strip()
    return render_template("beam.html", active_token=active_token)


@beam_bp.route("/beam/create", methods=["POST"])
def create_beam():
    """Create a new burn-on-read beam stored exclusively in volatile Redis memory."""
    r = get_redis_client()
    user_id = g.user["user_id"] if g.user else request.remote_addr

    # Rate limiting: Max 10 beams per minute per user/IP
    rate_key = f"beam_rate:{user_id}"
    req_count = r.incr(rate_key)
    if req_count == 1:
        r.expire(rate_key, 60)
    if req_count > 10:
        ttl = r.ttl(rate_key)
        flash(f"Rate limit exceeded. Please wait {ttl} seconds before creating more beams.", "danger")
        return redirect(url_for("beam.index"))

    content = request.form.get("content", "").strip()
    ttl_choice = request.form.get("ttl", "5m").strip()
    claim_code = request.form.get("claim_code", "").strip()

    if not content:
        flash("Message or link content cannot be empty.", "danger")
        return redirect(url_for("beam.index"))

    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_BEAM_BYTES:
        flash(f"Content exceeds 5 KB limit ({len(content_bytes)} bytes).", "danger")
        return redirect(url_for("beam.index"))

    # Validate optional 4-digit PIN
    if claim_code and not re.match(r"^\d{4}$", claim_code):
        flash("Claim code must be exactly 4 digits (e.g. 1234).", "danger")
        return redirect(url_for("beam.index"))

    ttl_seconds = TTL_MAP.get(ttl_choice, 300)
    token = secrets.token_urlsafe(24)

    payload = {
        "token": token,
        "content": content,
        "is_link": content.startswith(("http://", "https://")),
        "claim_code": claim_code,
        "created_at": time.time(),
        "ttl_seconds": ttl_seconds,
    }

    # OS Concept: Pure RAM Storage with SETEX
    r.setex(f"beam:{token}", ttl_seconds, json.dumps(payload))

    # Store sender token in cookie or session to track live status
    receive_url = url_for("beam.receive_beam", token=token, _external=True)
    qr_data_uri = generate_qr_data_uri(receive_url)

    # Cache beam tracking metadata for the sender in Redis
    sender_tracking = {
        "token": token,
        "receive_url": receive_url,
        "qr_data_uri": qr_data_uri,
        "ttl_seconds": ttl_seconds,
        "has_pin": bool(claim_code),
    }
    r.setex(f"beam_sender:{token}", ttl_seconds + 300, json.dumps(sender_tracking))

    flash("Clip Beam created! Send the link or scan the QR code to transfer.", "success")
    return redirect(url_for("beam.index", active_token=token))


@beam_bp.route("/beam/<token>/status", methods=["GET"])
def beam_status(token: str):
    """Poll live status of beam for sender dashboard (waiting, read, expired)."""
    r = get_redis_client()
    beam_key = f"beam:{token}"
    tombstone_key = f"beam:status:{token}"

    if r.exists(beam_key):
        ttl = r.ttl(beam_key)
        return jsonify({"status": "waiting", "ttl": max(0, ttl)})

    # Key does not exist in beam: check if tombstone was recorded on read
    raw_tombstone = r.get(tombstone_key)
    if raw_tombstone:
        try:
            data = json.loads(raw_tombstone)
            return jsonify({"status": "read", "burned_at": data.get("burned_at")})
        except Exception:
            return jsonify({"status": "read"})

    # Neither exists -> expired without read
    return jsonify({"status": "expired"})


@beam_bp.route("/beam/<token>", methods=["GET", "POST"])
def receive_beam(token: str):
    """Receive and burn beam using atomic Redis GETDEL instruction."""
    r = get_redis_client()
    beam_key = f"beam:{token}"
    tombstone_key = f"beam:status:{token}"
    fail_key = f"beam:fails:{token}"

    # 1. Check if beam has already been burned or expired
    if not r.exists(beam_key):
        # Determine if it was read or expired
        was_read = r.exists(tombstone_key)
        return render_template(
            "beam_receive.html",
            state="burned" if was_read else "expired",
            message="This Clip Beam was already burned upon reading." if was_read else "This Clip Beam has expired."
        )

    # 2. Inspect beam metadata to check if claim code is required
    raw_meta = r.get(beam_key)
    if not raw_meta:
        return render_template("beam_receive.html", state="expired", message="Clip Beam not found or expired.")

    try:
        meta = json.loads(raw_meta)
    except Exception:
        abort(500)

    has_pin = bool(meta.get("claim_code"))
    ttl_remaining = max(0, r.ttl(beam_key))

    # 3. If claim code is required and request is GET -> Show PIN prompt
    if has_pin and request.method == "GET":
        failed_attempts = int(r.get(fail_key) or 0)
        return render_template(
            "beam_receive.html",
            state="pin_required",
            token=token,
            ttl=ttl_remaining,
            attempts_left=max(0, 3 - failed_attempts)
        )

    # 4. Handle PIN verification if PIN was set
    if has_pin:
        user_pin = request.form.get("pin", "").strip()
        expected_pin = meta.get("claim_code")

        if user_pin != expected_pin:
            fails = r.incr(fail_key)
            if fails == 1:
                r.expire(fail_key, ttl_remaining + 60)

            if fails >= 3:
                # 3 strikes: Burn beam immediately!
                r.delete(beam_key)
                r.setex(tombstone_key, 300, json.dumps({"status": "burned_bad_pin", "burned_at": time.time()}))
                return render_template(
                    "beam_receive.html",
                    state="burned",
                    message="Too many incorrect PIN attempts. This beam has been burned to protect confidentiality."
                )

            attempts_left = max(0, 3 - fails)
            return render_template(
                "beam_receive.html",
                state="pin_required",
                token=token,
                ttl=ttl_remaining,
                attempts_left=attempts_left,
                error=f"Incorrect 4-digit code. {attempts_left} attempt(s) remaining."
            )

    # 5. ATOMIC REVEAL AND BURN via GETDEL
    # OS Concept: Linearizable Atomic Operation
    raw_data = r.getdel(beam_key)
    if not raw_data:
        # Another concurrent request executed GETDEL first!
        return render_template(
            "beam_receive.html",
            state="burned",
            message="This Clip Beam was already revealed and burned by another recipient."
        )

    # Record tombstone so sender sees 'read'
    r.setex(tombstone_key, 300, json.dumps({"status": "read", "burned_at": time.time()}))
    r.delete(fail_key)

    try:
        beam_data = json.loads(raw_data)
        content = beam_data["content"]
        is_link = beam_data.get("is_link", False)
    except Exception:
        abort(500)

    return render_template(
        "beam_receive.html",
        state="revealed",
        content=content,
        is_link=is_link,
    )
