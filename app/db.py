"""Airutil Database Connection Pool & Operations.

Operating Systems Concepts Demonstrated:
1. Socket & Connection Pooling: Reusing active TCP connections to PostgreSQL avoids
   frequent socket three-way handshakes and worker process fork overhead in PostgreSQL.
2. ACID Transactions & Concurrency: Isolation of concurrent database modifications
   across multiple container replicas.
3. Prepared / Parameterized Queries: Prevents SQL injection and allows PostgreSQL
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
    """Retrieve or initialize the global threaded connection pool."""
    global _pool
    if _pool is None or _pool.closed:
        max_retries = 10
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
                    "Database connection attempt %d/%d failed: %s. Retrying in 2s...",
                    attempt, max_retries, e
                )
                time.sleep(2)
        if _pool is None:
            raise RuntimeError("Could not connect to PostgreSQL database pool.")
    return _pool


@contextmanager
def get_db_cursor(commit: bool = True) -> Generator[psycopg2.extensions.cursor, None, None]:
    """Context manager for acquiring a pooled connection and cursor with transaction management.

    Args:
        commit: If True, commits the transaction on clean exit; rolls back on exception.
    """
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
        connection_pool.putconn(conn)


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
    logger.info("Database schema initialized successfully.")


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
