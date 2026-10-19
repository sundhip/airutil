"""Database initialization script for Airutil.

Executes schema.sql and verifies table readiness.
"""

import sys
from app.db import get_db_cursor, init_db


def main():
    print("[DB Init] Initializing Airutil PostgreSQL schema...", flush=True)
    try:
        init_db()
        with get_db_cursor(commit=False) as cur:
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """)
            tables = [row["table_name"] for row in cur.fetchall()]
        print(f"[DB Init] Schema created successfully! Tables: {', '.join(tables)}", flush=True)
    except Exception as e:
        print(f"[DB Init] FATAL: Failed to initialize schema: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
