"""Test Suite for Phase 6: OS Lab Primitives & Telemetry.

Operating Systems Concepts Demonstrated:
1. Control Groups v2 (cgroups v2): Memory max/current accounting and CFS bandwidth limits.
2. Linux Namespaces: /proc/self/ns symlinks, PID 1 namespace isolation.
3. OverlayFS: Root union mounts vs. host VFS volume mounts.
4. Load Balancing: Redis replica request counters for Nginx round-robin.
5. Concurrency & Rate Guards: Mutex lock on CPU stress worker threads.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from unittest.mock import patch

import pytest
from flask import g

from app.main import app
from app.oslab import (
    get_cgroups_info,
    get_namespaces_info,
    get_overlay_and_mounts_info,
    get_redis_telemetry,
    read_file_safe,
)
from app.session import SessionManager, get_redis_client


@pytest.fixture
def test_client():
    """Create Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def auth_client(test_client):
    """Create test client with authenticated session."""
    r = get_redis_client()
    sm = SessionManager()
    user_id = f"user_{secrets.token_hex(4)}"
    session_id = sm.create_session(
        user_id=user_id,
        username="oslab_student",
        email="student@univ.edu",
    )
    test_client.set_cookie("airutil_session", session_id)
    # Fetch CSRF token from a GET request
    resp = test_client.get("/")
    # Retrieve csrf_token from session
    session_data = sm.get_session(session_id)
    csrf_token = session_data.get("csrf_token") if session_data else "test_csrf"
    return test_client, csrf_token


class TestOSLabParsers:
    """Test kernel procfs/sysfs diagnostic readers and fallback logic."""

    def test_read_file_safe_missing_and_existing(self, tmp_path):
        """Verify safe reading of existent and nonexistent paths without exceptions."""
        assert read_file_safe("/non/existent/kernel/path/654321") is None

        test_file = tmp_path / "test_kernel_file.txt"
        test_file.write_text("50000 100000\n", encoding="utf-8")
        assert read_file_safe(str(test_file)) == "50000 100000"

    def test_cgroups_info_structure(self):
        """Verify get_cgroups_info returns valid structure and calculations."""
        info = get_cgroups_info()
        assert "cgroup_version" in info
        assert "memory_limit_bytes" in info
        assert "memory_current_bytes" in info
        assert "memory_percent" in info
        assert "cpu_cores_limit" in info
        assert "nr_throttled" in info
        assert "nr_periods" in info
        assert "throttled_sec" in info
        assert isinstance(info["memory_percent"], (int, float))
        assert 0.0 <= info["memory_percent"] <= 100.0

    def test_cgroups_custom_parsing(self):
        """Test calculation of cpu_cores_limit from quota and period."""
        mock_cpu_max = "50000 100000"
        mock_cpu_stat = (
            "usage_usec 1234567\n"
            "nr_periods 1000\n"
            "nr_throttled 45\n"
            "throttled_usec 450000\n"
        )

        def mock_read(path: str):
            if path == "/sys/fs/cgroup/cpu.max":
                return mock_cpu_max
            elif path == "/sys/fs/cgroup/cpu.stat":
                return mock_cpu_stat
            elif path == "/sys/fs/cgroup/memory.max":
                return "134217728"  # 128 MiB
            elif path == "/sys/fs/cgroup/memory.current":
                return "33554432"   # 32 MiB
            return None

        with patch("app.oslab.read_file_safe", side_effect=mock_read):
            info = get_cgroups_info()
            assert info["cpu_cores_limit"] == 0.50
            assert info["cpu_quota_usec"] == 50000
            assert info["cpu_period_usec"] == 100000
            assert info["nr_throttled"] == 45
            assert info["nr_periods"] == 1000
            assert info["throttled_percent"] == 4.5
            assert info["throttled_sec"] == 0.45
            assert info["memory_limit_bytes"] == 134217728
            assert info["memory_percent"] == 25.0

    def test_namespaces_info_structure(self):
        """Verify get_namespaces_info returns hostname, PID, and namespaces dict."""
        ns = get_namespaces_info()
        assert "hostname" in ns
        assert "pid" in ns
        assert "ppid" in ns
        assert "namespaces" in ns
        assert "pid1_cmd" in ns
        assert "threads" in ns
        assert isinstance(ns["namespaces"], dict)

    def test_mountinfo_overlay_parsing(self):
        """Verify parsing of /proc/self/mountinfo for OverlayFS and volume mounts."""
        mock_mountinfo = (
            "2054 2053 0:136 / / rw,relatime master:1 - overlay overlay "
            "rw,lowerdir=/var/lib/docker/overlay2/l1:/var/lib/docker/overlay2/l2,"
            "upperdir=/var/lib/docker/overlay2/u,workdir=/var/lib/docker/overlay2/w\n"
            "2055 2054 8:1 /var/lib/docker/volumes/airutil_data/_data /vault_storage rw,relatime - ext4 /dev/sda1 rw\n"
        )

        with patch("app.oslab.read_file_safe", return_value=mock_mountinfo):
            mounts = get_overlay_and_mounts_info()
            assert mounts["overlay"]["found"] is True
            assert mounts["overlay"]["lower_layers_count"] == 2
            assert mounts["overlay"]["upperdir"] == "/var/lib/docker/overlay2/u"
            assert mounts["overlay"]["workdir"] == "/var/lib/docker/overlay2/w"
            assert mounts["volume"]["found"] is True
            assert mounts["volume"]["mount_point"] == "/vault_storage"

    def test_redis_telemetry_keys(self):
        """Verify get_redis_telemetry returns non-sensitive metrics."""
        tel = get_redis_telemetry()
        assert tel["status"] == "connected"
        assert "connected_clients" in tel
        assert "used_memory_human" in tel
        assert "active_unlock_keys" in tel
        assert "active_beam_keys" in tel
        assert "app1_reqs" in tel
        assert "app2_reqs" in tel


class TestOSLabEndpoints:
    """Test HTTP routes, authentication guards, and live telemetry API."""

    def test_unauthenticated_oslab_redirects(self, test_client):
        """Unauthenticated access to /oslab must redirect to /login."""
        resp = test_client.get("/oslab")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_unauthenticated_api_returns_401(self, test_client):
        """Unauthenticated access to /api/oslab must return HTTP 401."""
        resp = test_client.get("/api/oslab")
        assert resp.status_code == 401
        data = json.loads(resp.data)
        assert data["status"] == "error"

    def test_unauthenticated_stress_returns_401(self, test_client):
        """Unauthenticated POST to /api/oslab/stress must return HTTP 401."""
        resp = test_client.post("/api/oslab/stress")
        assert resp.status_code in (401, 403)

    def test_authenticated_oslab_page(self, auth_client):
        """Authenticated user receives complete OS Lab HTML page."""
        client, _ = auth_client
        resp = client.get("/oslab")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Linux Kernel & Container Isolation Primitives" in html
        assert "cgroups v2 Resource Limits" in html
        assert "Linux Namespaces" in html
        assert "OverlayFS & Mounts" in html
        assert "Round-Robin Replicas" in html

    def test_authenticated_api_telemetry(self, auth_client):
        """Authenticated GET /api/oslab returns live JSON telemetry."""
        client, _ = auth_client
        resp = client.get("/api/oslab")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "cgroup" in data
        assert "ns" in data
        assert "mounts" in data
        assert "redis" in data
        assert "replica_name" in data

    def test_stress_test_execution_and_concurrency_lock(self, auth_client):
        """Verify CPU stress test lock prevents concurrent stress runs."""
        client, csrf_token = auth_client
        r = get_redis_client()
        r.delete("oslab_stress_lock")

        # First trigger must succeed
        resp1 = client.post(
            "/api/oslab/stress",
            headers={"X-CSRFToken": csrf_token}
        )
        assert resp1.status_code == 200
        data1 = json.loads(resp1.data)
        assert data1["status"] == "started"

        # Lock must exist in Redis
        assert r.exists("oslab_stress_lock") == 1

        # Second immediate trigger must be rejected with 429
        resp2 = client.post(
            "/api/oslab/stress",
            headers={"X-CSRFToken": csrf_token}
        )
        assert resp2.status_code == 429
        data2 = json.loads(resp2.data)
        assert "already running" in data2["message"]

        # Clean up lock
        r.delete("oslab_stress_lock")
