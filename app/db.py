"""Airutil Database Connection Pool & Operations.

Operating Systems Concepts Demonstrated:
1. Socket & Connection Pooling: Reusing active TCP connections to PostgreSQL avoids
   frequent socket three-way handshakes and worker process fork overhead in PostgreSQL.
2. ACID Transactions & Concurrency: Isolation of concurrent database modifications
   across multiple container replicas.
3. Resilience & Self-Healing Sockets: Reconnection with exponential backoff handles
   temporary container restarts and network partitions.
4. Prepared / Parameterized Queries: Prevents SQL injection and allows PostgreSQL
   to cache query execution plans.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Generator, List, Optional, Tuple

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("airutil.db")

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://airutil:airutil_secure_vault_2026@db:5432/airutil"
)

# Global connection pool instance
_pool: Optional[pool.ThreadedConnectionPool] = None


def get_pool() -> pool.ThreadedConnectionPool:
    """Retrieve or initialize the global threaded connection pool with retry resilience."""
    global _pool
    if _pool is None or _pool.closed:
        max_retries = 5
        backoff = 1.0
        for attempt in range(1, max_retries + 1):
            try:
                _pool = pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=20,
                    dsn=DATABASE_URL
                )
                logger.info("Initialized PostgreSQL threaded connection pool.")
                break
            except Exception as e:
                logger.warning(
                    "Database connection attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt, max_retries, e, backoff
                )
                time.sleep(backoff)
                backoff = min(backoff * 1.5, 5.0)

        if _pool is None:
            raise RuntimeError("Could not connect to PostgreSQL database pool.")
    return _pool


def check_db_health() -> bool:
    """Non-throwing health probe used by readiness checks and 503 error handling."""
    try:
        connection_pool = get_pool()
        conn = connection_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                return True
        finally:
            connection_pool.putconn(conn)
    except Exception as e:
        logger.debug("Database health check failed: %s", e)
        return False


@contextmanager
def get_db_cursor(commit: bool = True) -> Generator[psycopg2.extensions.cursor, None, None]:
    """Context manager for acquiring a pooled connection and cursor with transaction management.

    Handles broken socket reconnection transparently.
    """
    global _pool
    connection_pool = get_pool()
    try:
        conn = connection_pool.getconn()
    except Exception as e:
        logger.warning("Connection checkout failed: %s. Re-initializing pool...", e)
        if _pool and not _pool.closed:
            try:
                _pool.closeall()
            except Exception:
                pass
        _pool = None
        connection_pool = get_pool()
        conn = connection_pool.getconn()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            connection_pool.putconn(conn)
        except Exception:
            pass


def is_user_admin(user_id: int) -> bool:
    """Determine if a user has admin privileges.

    OS Principle: Principle of Least Privilege.
    By default, the first registered student (lowest user_id) is the administrator.
    """
    if not user_id:
        return False
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute("SELECT id, is_admin FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                return False
            if row.get("is_admin") is True:
                return True
            # Check if this user is the lowest ID
            cur.execute("SELECT MIN(id) AS first_id FROM users")
            min_row = cur.fetchone()
            if min_row and min_row.get("first_id") == user_id:
                return True
    except Exception as e:
        logger.warning("Failed to evaluate admin status for user %s: %s", user_id, e)
        # Fallback: ID 1 is always admin
        return user_id == 1
    return False


def init_db() -> None:
    """Initialize relational tables and indexes from schema.sql."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    if not os.path.exists(schema_path):
        logger.error("schema.sql not found at %s", schema_path)
        return

    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    with get_db_cursor(commit=True) as cur:
        cur.execute(schema_sql)
        # Migration: ensure is_admin column exists
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;")
        # Set first registered user to admin if not already set
        cur.execute("""
            UPDATE users SET is_admin = TRUE 
            WHERE id = (SELECT MIN(id) FROM users) AND (is_admin IS NULL OR is_admin = FALSE);
        """)
    logger.info("Database schema initialized and admin migration applied successfully.")


def log_activity(user_id: Optional[int], action: str, details: str, ip_address: str) -> None:
    """Record an audit trail entry into activity_log."""
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO activity_log (user_id, action, details, ip_address)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, action, details, ip_address)
            )
    except Exception as e:
        logger.error("Failed to log activity: %s", e)
