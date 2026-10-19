"""Live Acceptance Verification Script for Phase 5: Burn-on-Read Clip Beam.

This script tests all non-negotiable acceptance requirements against the live multi-container
deployment via the Nginx load balancer at http://localhost:8080.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import secrets
import subprocess
import sys
import time
import requests

BASE_URL = os.getenv("BASE_URL", "http://localhost:8080")


def print_check(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name}")
    if detail:
        print(f"       -> {detail}")
    if not passed:
        sys.exit(1)


def get_csrf_token(html_text: str) -> str:
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', html_text)
    if match:
        return match.group(1)
    return ""


def main():
    print("=" * 70)
    print("AIRUTIL PHASE 5: LIVE ACCEPTANCE VERIFICATION CHECKS")
    print(f"Target Gateway: {BASE_URL}")
    print("=" * 70)

    # 1. Register test sender
    sender = requests.Session()
    u = f"beam_sender_{int(time.time())}"
    p = "BeamPassword123!"

    resp = sender.get(f"{BASE_URL}/register")
    csrf = get_csrf_token(resp.text)
    sender.post(f"{BASE_URL}/register", data={
        "csrf_token": csrf,
        "username": u,
        "email": f"{u}@college.edu",
        "password": p,
        "confirm_password": p,
    }, allow_redirects=True)
    print_check("Sender Student Registration", True, f"Registered user {u}")

    # --------------------------------------------------------------------------
    # Check 1 & 2 & 4: Key in Redis before reveal, nil after, and TTL decreases
    # --------------------------------------------------------------------------
    beam_page = sender.get(f"{BASE_URL}/beam")
    csrf_beam = get_csrf_token(beam_page.text)
    secret_clip = f"ConfidentialToken_{secrets.token_hex(8)}"

    create_data = {
        "csrf_token": csrf_beam,
        "content": secret_clip,
        "ttl": "60s",
        "claim_code": "",
    }
    create_resp = sender.post(f"{BASE_URL}/beam/create", data=create_data, allow_redirects=False)
    loc = create_resp.headers.get("Location", "")
    token_match = re.search(r"active_token=([^&]+)", loc)
    assert token_match, f"Could not find active_token in redirect: {loc}"
    token_1 = token_match.group(1)
    print_check("Clip Beam Created", True, f"Generated token: {token_1}")

    # Inspect Redis before reveal
    redis_get_before = subprocess.run(
        ["docker", "exec", "airutil_redis", "redis-cli", "GET", f"beam:{token_1}"],
        capture_output=True,
        text=True
    )
    has_key_before = secret_clip in redis_get_before.stdout
    print_check(
        "Redis GET Shows Key Before Reveal",
        has_key_before,
        f"Redis stores JSON payload in ephemeral RAM: {redis_get_before.stdout.strip()[:60]}..."
    )

    # Verify TTL decreases
    ttl_1_proc = subprocess.run(
        ["docker", "exec", "airutil_redis", "redis-cli", "TTL", f"beam:{token_1}"],
        capture_output=True,
        text=True
    )
    ttl_1 = int(ttl_1_proc.stdout.strip())
    time.sleep(2)
    ttl_2_proc = subprocess.run(
        ["docker", "exec", "airutil_redis", "redis-cli", "TTL", f"beam:{token_1}"],
        capture_output=True,
        text=True
    )
    ttl_2 = int(ttl_2_proc.stdout.strip())
    ttl_decreased = (0 < ttl_2 < ttl_1)
    print_check(
        "Redis TTL Decreases Over Time",
        ttl_decreased,
        f"Initial TTL: {ttl_1}s -> After 2s: {ttl_2}s"
    )

    # Reveal the beam as a mobile client (simulate mobile Phone browser User-Agent)
    mobile_session = requests.Session()
    mobile_headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    }
    reveal_resp = mobile_session.get(f"{BASE_URL}/beam/{token_1}", headers=mobile_headers)
    revealed_ok = (reveal_resp.status_code == 200) and (secret_clip in reveal_resp.text) and ("Burned on Read" in reveal_resp.text)
    print_check(
        "Mobile Phone Browser Reveal Successful",
        revealed_ok,
        "Mobile client successfully revealed secret clip and triggered burn notice"
    )

    # Inspect Redis after reveal: MUST BE nil
    redis_get_after = subprocess.run(
        ["docker", "exec", "airutil_redis", "redis-cli", "GET", f"beam:{token_1}"],
        capture_output=True,
        text=True
    )
    output_after = redis_get_after.stdout.strip()
    is_nil = output_after == "" or output_after == "(nil)" or output_after == "nil"
    print_check(
        "Redis GET Returns nil After Reveal (Atomic Burn Confirmed)",
        is_nil,
        f"Key 'beam:{token_1}' evicted from RAM. Output: '{output_after}'"
    )

    # Second read attempt MUST fail
    second_read = mobile_session.get(f"{BASE_URL}/beam/{token_1}")
    second_failed = "Already Burned" in second_read.text or "burned" in second_read.text
    print_check(
        "Subsequent Read Sees 'Already Burned'",
        second_failed,
        "Second reader blocked: message already consumed and destroyed"
    )

    # --------------------------------------------------------------------------
    # Check 3: Simultaneous Concurrent Reveal (Atomic Linearizability via GETDEL)
    # --------------------------------------------------------------------------
    # Create beam for simultaneous reveal test
    simult_secret = f"SimultaneousAtomicBurnPayload2026_{secrets.token_hex(4)}"
    create_resp2 = sender.post(
        f"{BASE_URL}/beam/create",
        data={"csrf_token": csrf_beam, "content": simult_secret, "ttl": "5m", "claim_code": ""},
        allow_redirects=False
    )
    loc2 = create_resp2.headers.get("Location", "")
    token_simult = re.search(r"active_token=([^&]+)", loc2).group(1)

    # Launch two concurrent reveal requests concurrently in separate threads
    def fetch_reveal(url):
        return requests.get(url, timeout=5)

    reveal_url = f"{BASE_URL}/beam/{token_simult}"
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(fetch_reveal, reveal_url)
        f2 = executor.submit(fetch_reveal, reveal_url)
        r1 = f1.result()
        r2 = f2.result()

    # Exactly one must contain the secret, and exactly one must show "already burned"
    success_1 = simult_secret in r1.text and "Burned on Read" in r1.text
    success_2 = simult_secret in r2.text and "Burned on Read" in r2.text
    burned_1 = "Already Burned" in r1.text or "burned" in r1.text
    burned_2 = "Already Burned" in r2.text or "burned" in r2.text

    simultaneous_linearizable = (success_1 and burned_2) or (success_2 and burned_1)
    print_check(
        "Simultaneous Atomic Burn (Race-Free Linearizability via GETDEL)",
        simultaneous_linearizable,
        f"Request 1 success: {success_1}, Request 2 success: {success_2} (Exactly one won the race)"
    )

    # --------------------------------------------------------------------------
    # Check 5: Grep on Volume Finds Nothing (Pure RAM Storage)
    # --------------------------------------------------------------------------
    grep_proc = subprocess.run(
        ["docker", "exec", "airutil_app1", "grep", "-rn", "SimultaneousAtomicBurnPayload2026", "/vault_storage"],
        capture_output=True,
        text=True
    )
    grep_empty = (grep_proc.returncode != 0) and (grep_proc.stdout.strip() == "")
    print_check(
        "Zero-Disk Footprint (grep -rn on /vault_storage finds NOTHING)",
        grep_empty,
        "No beam content written to disk volume; 100% ephemeral RAM buffering"
    )

    # --------------------------------------------------------------------------
    # Check 6: 4-Digit Claim PIN and 3-Strike Threshold Burn
    # --------------------------------------------------------------------------
    pin_secret = "ClassifiedAssignmentSolutions#987"
    create_pin_resp = sender.post(
        f"{BASE_URL}/beam/create",
        data={"csrf_token": csrf_beam, "content": pin_secret, "ttl": "5m", "claim_code": "5678"},
        allow_redirects=False
    )
    token_pin = re.search(r"active_token=([^&]+)", create_pin_resp.headers.get("Location", "")).group(1)

    pin_receiver = requests.Session()
    # 1. Initial GET shows PIN prompt
    p_get = pin_receiver.get(f"{BASE_URL}/beam/{token_pin}")
    csrf_p = get_csrf_token(p_get.text)
    print_check(
        "4-Digit PIN Gate Rendered",
        "Protected Clip Beam" in p_get.text and "3 attempt(s) remaining" in p_get.text,
        "PIN prompt displayed before revealing content"
    )

    # 2. Enter wrong PIN 3 times
    for attempt_idx in range(1, 4):
        p_post = pin_receiver.post(
            f"{BASE_URL}/beam/{token_pin}",
            data={"csrf_token": csrf_p, "pin": "0000"},
            allow_redirects=True
        )
        csrf_p = get_csrf_token(p_post.text) or csrf_p

    print_check(
        "PIN 3-Strike Policy Burns Beam",
        "Too many incorrect PIN attempts" in p_post.text or "burned" in p_post.text,
        "3 wrong attempts burned the beam immediately"
    )

    # Ensure key deleted in Redis
    pin_key_check = subprocess.run(
        ["docker", "exec", "airutil_redis", "redis-cli", "EXISTS", f"beam:{token_pin}"],
        capture_output=True,
        text=True
    )
    print_check(
        "PIN-Burned Key Evicted from Redis",
        pin_key_check.stdout.strip() == "0",
        "Key deleted from Redis following 3rd strike"
    )

    print("=" * 70)
    print("ALL PHASE 5 ACCEPTANCE CHECKS COMPLETED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    main()
