"""Distributed Server-Side Session Management in Redis.

Operating Systems Concepts Demonstrated:
1. Shared In-Memory State: Distributed memory store (Redis) allows stateless application
   replicas (app1, app2) to share authentication state with zero sticky session dependency.
2. Sliding Expiration: Active requests refresh the in-memory key TTL (2 hours) via
   atomic EXPIRE commands.
3. HttpOnly & SameSite Cookie Hardening: Prevents client-side JavaScript access (XSS mitigation)
   and cross-site request forgery at the browser boundary.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any, Dict, Optional

import redis
from flask import g, request, Response

logger = logging.getLogger("airutil.session")

REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
SESSION_COOKIE_NAME = "airutil_session"
SESSION_LIFETIME_SECONDS = 7200  # 2 hours sliding window

# Global redis client for session operations
_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> redis.Redis:
    """Retrieve or initialize the Redis client instance with resilient timeout settings."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
            retry_on_timeout=True,
        )
    return _redis_client


def check_redis_health() -> bool:
    """Non-throwing health probe for Redis used in healthchecks and 503 error handling."""
    try:
        r = get_redis_client()
        return bool(r.ping())
    except Exception as e:
        logger.debug("Redis health check failed: %s", e)
        return False


class SessionManager:
    """Manages server-side sessions stored in Redis."""

    def __init__(self, client: Optional[redis.Redis] = None):
        self.r = client or get_redis_client()

    def create_session(self, user_id: int, username: str, email: str) -> str:
        """Create a new session in Redis with 2-hour TTL and return session_id."""
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_hex(32)
        session_data = {
            "user_id": user_id,
            "username": username,
            "email": email,
            "csrf_token": csrf_token,
        }
        key = f"session:{session_id}"
        self.r.setex(key, SESSION_LIFETIME_SECONDS, json.dumps(session_data))
        logger.info("Created session %s for user %s", session_id[:8], username)
        return session_id

    def get_session(self, session_id: str, refresh: bool = True) -> Optional[Dict[str, Any]]:
        """Retrieve session data from Redis. Optionally slides expiry window."""
        if not session_id:
            return None
        key = f"session:{session_id}"
        try:
            data = self.r.get(key)
            if not data:
                return None
            if refresh:
                # Slide session expiration window by 2 hours
                self.r.expire(key, SESSION_LIFETIME_SECONDS)
            return json.loads(data)
        except Exception as e:
            logger.error("Failed to read session %s: %s", session_id[:8], e)
            return None

    def destroy_session(self, session_id: str) -> None:
        """Delete session from Redis."""
        if session_id:
            key = f"session:{session_id}"
            try:
                self.r.delete(key)
                logger.info("Destroyed session %s", session_id[:8])
            except Exception as e:
                logger.error("Error destroying session: %s", e)

    def get_csrf_token(self, session_id: str) -> Optional[str]:
        """Fetch CSRF token for the session."""
        session = self.get_session(session_id, refresh=False)
        return session.get("csrf_token") if session else None


def set_session_cookie(response: Response, session_id: str) -> Response:
    """Attach the secure HttpOnly session cookie to an HTTP response."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=SESSION_LIFETIME_SECONDS,
        httponly=True,
        samesite="Lax",
        secure=False,  # Set to True when TLS is enabled
        path="/"
    )
    return response


def clear_session_cookie(response: Response) -> Response:
    """Instruct browser to delete session cookie."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/"
    )
    return response
