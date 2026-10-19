"""Security Utilities: Argon2id Hashing, Rate Limiting & Input Validation.

Operating Systems & Security Concepts Demonstrated:
1. Memory-Hard Password Hashing: Argon2id defends against GPU/ASIC parallel brute-force
   attacks by enforcing memory-hard computational complexity (64MB RAM per hash).
2. Distributed In-Memory Rate Limiting: Sliding/fixed window counters stored in Redis
   prevent credential stuffing and denial of service across distributed web replicas.
3. Cryptographic Token Generation: Uses kernel entropy source (/dev/urandom via os.urandom)
   for cryptographically secure random session and CSRF tokens.
"""

from __future__ import annotations

import re
import secrets
from typing import Optional, Tuple

import redis
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask import request

# Initialize Argon2id password hasher with RFC 9106 recommended parameters
# memory_cost=65536 (64 MiB), time_cost=2, parallelism=1
hasher = PasswordHasher(
    time_cost=2,
    memory_cost=65536,
    parallelism=1,
    hash_len=32
)

# Email regex validation pattern
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_]{3,30}$")


def hash_password(password: str) -> str:
    """Hash password using Argon2id."""
    return hasher.hash(password)


def verify_password(hash_str: str, password: str) -> bool:
    """Verify password against stored Argon2id hash."""
    try:
        return hasher.verify(hash_str, password)
    except (VerifyMismatchError, Exception):
        return False


def validate_registration_input(username: str, email: str, password: str) -> Optional[str]:
    """Validate user registration inputs.

    Returns:
        Error string if invalid, None if valid.
    """
    if not username or not USERNAME_REGEX.match(username):
        return "Username must be 3-30 characters long and contain only letters, numbers, and underscores."

    if not email or not EMAIL_REGEX.match(email) or len(email) > 255:
        return "Please provide a valid email address."

    if not password or len(password) < 8:
        return "Password must be at least 8 characters long."

    if len(password) > 128:
        return "Password is too long (maximum 128 characters)."

    return None


def get_client_ip() -> str:
    """Extract real client IP considering Nginx reverse proxy headers."""
    # Nginx passes real client IP in X-Real-IP or X-Forwarded-For
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.remote_addr or "127.0.0.1"


class LoginRateLimiter:
    """Distributed Redis-backed rate limiter for authentication attempts.

    OS Concept: Distributed synchronization via in-memory key-value store (Redis)
    prevents brute-force attacks across multiple load-balanced web replicas.
    """

    def __init__(self, redis_client: redis.Redis, limit: int = 5, window_seconds: int = 60):
        self.redis = redis_client
        self.limit = limit
        self.window = window_seconds

    def check_rate_limit(self, identifier: str) -> Tuple[bool, int]:
        """Check if an identifier has exceeded the login attempt limit.

        Args:
            identifier: IP address or username.

        Returns:
            Tuple of (is_limited, remaining_lockout_seconds).
        """
        key = f"ratelimit:login:{identifier}"
        try:
            current = self.redis.get(key)
            if current and int(current) >= self.limit:
                ttl = self.redis.ttl(key)
                return True, max(ttl, 1)
        except Exception:
            # Fall back safely if Redis is momentarily unreachable
            pass
        return False, 0

    def record_failed_attempt(self, identifier: str) -> int:
        """Increment failed attempts counter with window expiry.

        Returns:
            Current count of failed attempts.
        """
        key = f"ratelimit:login:{identifier}"
        try:
            pipe = self.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, self.window)
            results = pipe.execute()
            return int(results[0])
        except Exception:
            return 1

    def reset_attempts(self, identifier: str) -> None:
        """Reset counter upon successful login."""
        key = f"ratelimit:login:{identifier}"
        try:
            self.redis.delete(key)
        except Exception:
            pass


def generate_csrf_token() -> str:
    """Generate a high-entropy CSRF token using OS kernel random device."""
    return secrets.token_hex(32)
