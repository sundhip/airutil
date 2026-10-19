"""Airutil Background Worker.

Operating Systems Concepts Demonstrated:
1. Asynchronous Process Queuing: Worker process decoupled from HTTP request-response cycle.
2. Inter-Process Communication (IPC): Redis used as an in-memory message broker.
3. Linux Signal Handling: Traps SIGTERM and SIGINT for graceful shutdown.
4. Heartbeat via tmpfs: Writes health pulse to ephemeral in-memory filesystem (/tmp).
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from typing import NoReturn

import redis
from rq import Queue, Worker

REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
STORAGE_PATH: str = os.getenv("STORAGE_PATH", "/vault_storage")
HEARTBEAT_FILE: str = "/tmp/worker_heartbeat"

running = True


def signal_handler(signum: int, frame) -> None:
    """Trap termination signals for graceful shutdown."""
    global running
    print(f"[Worker] Received signal {signum}, stopping gracefully...", flush=True)
    running = False
    sys.exit(0)


def update_heartbeat() -> None:
    """Touch heartbeat file in tmpfs for container healthchecks."""
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception as e:
        print(f"[Worker] Error writing heartbeat: {e}", file=sys.stderr, flush=True)


def heartbeat_loop() -> None:
    """Background thread to continuously update heartbeat file."""
    while running:
        update_heartbeat()
        time.sleep(5)


def main() -> NoReturn:
    """Worker entry point."""
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print(f"[Worker] Starting Airutil Worker (PID={os.getpid()})...", flush=True)
    print(f"[Worker] Connecting to Redis at {REDIS_URL}...", flush=True)

    # Initial heartbeat touch
    update_heartbeat()

    # Retry loop connecting to Redis
    r = None
    for attempt in range(1, 11):
        try:
            r = redis.from_url(REDIS_URL)
            r.ping()
            print("[Worker] Connected to Redis successfully.", flush=True)
            break
        except Exception as e:
            print(f"[Worker] Redis connection attempt {attempt}/10 failed: {e}. Retrying in 2s...", flush=True)
            time.sleep(2)

    if not r:
        print("[Worker] FATAL: Could not connect to Redis broker.", file=sys.stderr, flush=True)
        sys.exit(1)

    # Start background heartbeat thread
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()

    # Worker queue loop (RQ 2.x API)
    queues = [Queue("default", connection=r), Queue("transfers", connection=r), Queue("vault_jobs", connection=r)]
    print("[Worker] Listening on queues: default, transfers, vault_jobs", flush=True)

    worker = Worker(queues, connection=r)
    worker.work(with_scheduler=False, burst=False)


if __name__ == "__main__":
    main()
