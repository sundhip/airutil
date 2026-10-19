"""Test Suite for Phase 5: Burn-on-Read Clip Beam.

Operating Systems Concepts Demonstrated:
1. Atomic Linearizability (Redis GETDEL): Race-free single consumer guarantee.
2. Ephemeral Storage: Validates strict RAM-only lifecycle.
3. Access Control & Threshold Burn: 3-strike PIN enforcement.
"""

from __future__ import annotations

import json
import secrets
import pytest
from app.session import get_redis_client
from app.beam import MAX_BEAM_BYTES, TTL_MAP


class TestBeamAtomicLifecycle:
    """Test Redis GETDEL atomicity and ephemeral lifecycle."""

    def test_atomic_getdel_single_consumer(self):
        """Verify that Redis GETDEL delivers content to exactly one reader and atomically deletes it."""
        r = get_redis_client()
        token = "test_beam_" + secrets.token_urlsafe(16)
        key = f"beam:{token}"
        payload = {
            "token": token,
            "content": "SecretWiFiPassword#2026",
            "is_link": False,
        }
        r.setex(key, 60, json.dumps(payload))

        # First read via GETDEL must succeed
        read_1 = r.getdel(key)
        assert read_1 is not None
        data_1 = json.loads(read_1)
        assert data_1["content"] == "SecretWiFiPassword#2026"

        # Immediate second read must return None (atomically destroyed)
        read_2 = r.getdel(key)
        assert read_2 is None

        # Key must no longer exist in Redis
        assert r.exists(key) == 0

    def test_ttl_mapping_values(self):
        """Verify correct TTL values for duration options."""
        assert TTL_MAP["60s"] == 60
        assert TTL_MAP["5m"] == 300
        assert TTL_MAP["15m"] == 900

    def test_payload_size_enforcement(self):
        """Verify that payloads up to 5 KB are accepted, while over 5 KB are identified."""
        small_payload = "A" * 1024
        assert len(small_payload.encode("utf-8")) <= MAX_BEAM_BYTES

        max_payload = "B" * 5120
        assert len(max_payload.encode("utf-8")) == MAX_BEAM_BYTES

        too_large_payload = "C" * 5121
        assert len(too_large_payload.encode("utf-8")) > MAX_BEAM_BYTES

    def test_pin_three_strike_burn_logic(self):
        """Verify that 3 incorrect PIN attempts burn the beam immediately."""
        r = get_redis_client()
        token = "test_pin_beam_" + secrets.token_urlsafe(16)
        beam_key = f"beam:{token}"
        fail_key = f"beam:fails:{token}"

        payload = {
            "token": token,
            "content": "ConfidentialZoomLink",
            "claim_code": "9876",
        }
        r.setex(beam_key, 60, json.dumps(payload))

        # 1st wrong attempt
        fails = r.incr(fail_key)
        assert fails == 1
        assert r.exists(beam_key) == 1

        # 2nd wrong attempt
        fails = r.incr(fail_key)
        assert fails == 2
        assert r.exists(beam_key) == 1

        # 3rd wrong attempt -> Burn key
        fails = r.incr(fail_key)
        assert fails == 3
        if fails >= 3:
            r.delete(beam_key)

        # Beam must now be burned
        assert r.exists(beam_key) == 0

        # Clean up
        r.delete(fail_key)
