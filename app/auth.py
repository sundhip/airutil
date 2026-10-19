"""Authentication Blueprint: Registration, Login, Logout, and Rate Limiting.

Operating Systems Concepts Demonstrated:
1. Distributed Rate Limiting: Coordinated enforcement across independent application
   processes via Redis memory cache.
2. Kernel Cryptographic Hashing: Argon2id password derivation utilizing high-memory
   pages to defeat hardware acceleration.
3. Security Auditing & Logging: Append-only persistent activity log tracking login/logout
   events with real client IP resolution.
"""

from __future__ import annotations

import logging
from typing import Optional

from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
    Response,
)

from app.db import get_db_cursor, log_activity
from app.security import (
    LoginRateLimiter,
    get_client_ip,
    hash_password,
    validate_registration_input,
    verify_password,
)
from app.session import (
    SessionManager,
    clear_session_cookie,
    get_redis_client,
    set_session_cookie,
)

logger = logging.getLogger("airutil.auth")
auth_bp = Blueprint("auth", __name__)


def get_limiter() -> LoginRateLimiter:
    """Instantiate login rate limiter with Redis client."""
    return LoginRateLimiter(get_redis_client(), limit=5, window_seconds=60)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """User registration handler with Argon2id password hashing."""
    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        validation_error = validate_registration_input(username, email, password)
        if validation_error:
            flash(validation_error, "danger")
            return render_template("register.html", username=username, email=email)

        # Hash password with Argon2id
        pwd_hash = hash_password(password)
        client_ip = get_client_ip()

        try:
            with get_db_cursor(commit=True) as cur:
                # Check for existing user
                cur.execute(
                    "SELECT id, username, email FROM users WHERE LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s)",
                    (username, email)
                )
                existing = cur.fetchone()
                if existing:
                    if existing["username"].lower() == username.lower():
                        flash("Username is already taken. Please choose another.", "warning")
                    else:
                        flash("Email is already registered. Please log in.", "warning")
                    return render_template("register.html", username=username, email=email)

                # Insert new user with Argon2id hash
                cur.execute(
                    """
                    INSERT INTO users (username, email, password_hash)
                    VALUES (%s, %s, %s)
                    RETURNING id, username, email
                    """,
                    (username, email, pwd_hash)
                )
                user = cur.fetchone()

            user_id = user["id"]
            log_activity(user_id, "user_register", f"Registered username: {username}", client_ip)

            # Automatically log in new user via Redis session
            sm = SessionManager()
            session_id = sm.create_session(user_id, username, email)

            flash("Account registered successfully! Welcome to Airutil.", "success")
            resp = redirect(url_for("dashboard"))
            return set_session_cookie(resp, session_id)

        except Exception as e:
            logger.error("Registration error: %s", e)
            flash("An unexpected error occurred during registration. Please try again.", "danger")
            return render_template("register.html", username=username, email=email)

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """User login handler with Redis rate limiting and Argon2id verification."""
    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        login_id = request.form.get("login_id", "").strip()
        password = request.form.get("password", "")
        client_ip = get_client_ip()
        limiter = get_limiter()

        # Check rate limits for IP and username
        ip_limited, ip_ttl = limiter.check_rate_limit(f"ip:{client_ip}")
        user_limited, user_ttl = limiter.check_rate_limit(f"user:{login_id.lower()}")

        if ip_limited or user_limited:
            remaining = max(ip_ttl, user_ttl)
            flash(
                f"Too many failed login attempts. Rate limit exceeded. Please try again in {remaining} seconds.",
                "danger"
            )
            return render_template("login.html", login_id=login_id), 429

        if not login_id or not password:
            flash("Please enter both username/email and password.", "warning")
            return render_template("login.html", login_id=login_id)

        # Lookup user in database
        try:
            with get_db_cursor(commit=False) as cur:
                cur.execute(
                    """
                    SELECT id, username, email, password_hash
                    FROM users
                    WHERE LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s)
                    """,
                    (login_id, login_id)
                )
                user = cur.fetchone()

            if not user or not verify_password(user["password_hash"], password):
                # Record failed attempt against IP and username
                limiter.record_failed_attempt(f"ip:{client_ip}")
                if user:
                    limiter.record_failed_attempt(f"user:{user['username'].lower()}")
                else:
                    limiter.record_failed_attempt(f"user:{login_id.lower()}")

                log_activity(
                    user["id"] if user else None,
                    "login_failure",
                    f"Failed login attempt for '{login_id}'",
                    client_ip
                )
                flash("Invalid credentials. Please verify your login details.", "danger")
                return render_template("login.html", login_id=login_id), 401

            # Successful login: reset rate limiter
            limiter.reset_attempts(f"ip:{client_ip}")
            limiter.reset_attempts(f"user:{user['username'].lower()}")

            # Create 2-hour sliding Redis session
            sm = SessionManager()
            session_id = sm.create_session(user["id"], user["username"], user["email"])

            log_activity(user["id"], "login_success", f"User logged in from {client_ip}", client_ip)
            flash(f"Welcome back, {user['username']}!", "success")

            resp = redirect(url_for("dashboard"))
            return set_session_cookie(resp, session_id)

        except Exception as e:
            logger.error("Login database exception: %s", e)
            flash("Service temporarily unavailable. Please try again.", "danger")
            return render_template("login.html", login_id=login_id)

    return render_template("login.html")


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    """Destroy Redis session and clear client session cookie."""
    client_ip = get_client_ip()
    session_id = getattr(g, "session_id", None)
    user = getattr(g, "user", None)

    if session_id:
        sm = SessionManager()
        sm.destroy_session(session_id)

    if user:
        log_activity(user["user_id"], "logout", f"User logged out from {client_ip}", client_ip)

    flash("You have been signed out successfully.", "info")
    resp = redirect(url_for("auth.login"))
    return clear_session_cookie(resp)
