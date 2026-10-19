"""Live Acceptance Verification Script for Phase 3: Smart AirDrop & File Organizer.

This script tests all non-negotiable acceptance requirements against the live multi-container
deployment via the Nginx load balancer at http://localhost:8080.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import time
import requests

BASE_URL = "http://localhost:8080"
SESSION = requests.Session()


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
    print("AIRUTIL PHASE 3: LIVE ACCEPTANCE VERIFICATION CHECKS")
    print(f"Target Gateway: {BASE_URL}")
    print("=" * 70)

    # 1. Register test student
    username = f"phase3_user_{int(time.time())}"
    email = f"{username}@univ.edu"
    password = "SecurePassword123!"

    resp = SESSION.get(f"{BASE_URL}/register")
    csrf_token = get_csrf_token(resp.text)
    assert csrf_token, "Could not extract CSRF token from /register"

    reg_data = {
        "csrf_token": csrf_token,
        "username": username,
        "email": email,
        "password": password,
        "confirm_password": password,
    }
    reg_resp = SESSION.post(f"{BASE_URL}/register", data=reg_data, allow_redirects=True)
    print_check("Student Registration", reg_resp.status_code == 200, f"Registered user {username}")

    # Fetch dashboard / files to get authenticated session CSRF token
    files_page = SESSION.get(f"{BASE_URL}/files")
    csrf_token = get_csrf_token(files_page.text)
    print_check("Authenticated Session CSRF Acquired", bool(csrf_token), f"CSRF token present")

    # --------------------------------------------------------------------------
    # Check 1 & 3: Course Regex & Shared Storage Volume across Replicas
    # Upload CSE1005_Unit2.pdf with valid PDF structure and text
    # --------------------------------------------------------------------------
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        b"4 0 obj << /Length 73 >> stream\n"
        b"BT /F1 12 Tf 100 700 Td (CSE1005 Operating Systems Deadlocks Mutual Exclusion) Tj ET\n"
        b"endstream\nendobj\n"
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000244 00000 n \n0000000368 00000 n \n"
        b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n443\n%%EOF\n"
    )

    upload_files = {
        "files": ("CSE1005_Unit2.pdf", pdf_bytes, "application/pdf")
    }
    upload_data = {
        "csrf_token": csrf_token,
        "course": ""  # Leave blank for auto-detection
    }
    headers = {"X-Requested-With": "XMLHttpRequest"}

    up_resp = SESSION.post(f"{BASE_URL}/files/upload", files=upload_files, data=upload_data, headers=headers)
    assert up_resp.status_code == 200, f"Upload failed: {up_resp.text}"
    up_json = up_resp.json()
    result = up_json["results"][0]
    file_id = result["id"]

    print_check("Upload CSE1005_Unit2.pdf", result["status"] == "success", f"Uploaded file_id={file_id}")
    print_check("Course Code Auto-Detection", result["course"] == "CSE1005", f"Detected course: {result['course']}")
    print_check("Category Classification", result["category"] == "Documents", f"Category: {result['category']}")

    # Verify physical file existence in app2 shared volume mount
    # docker exec airutil_app2 ls -R /vault_storage/files
    app2_check = subprocess.run(
        ["docker", "exec", "airutil_app2", "ls", "-R", "/vault_storage/files"],
        capture_output=True,
        text=True
    )
    shared_vol_passed = "CSE1005" in app2_check.stdout and result["storage_name"] in app2_check.stdout
    print_check(
        "Cross-Replica Volume Check (app2 sees file uploaded via app1)",
        shared_vol_passed,
        f"Storage path in app2: {result['storage_name']} located under CSE1005/"
    )

    # --------------------------------------------------------------------------
    # Check 2: Duplicate Upload Warning
    # --------------------------------------------------------------------------
    up_resp_dup = SESSION.post(f"{BASE_URL}/files/upload", files=upload_files, data=upload_data, headers=headers)
    up_json_dup = up_resp_dup.json()
    result_dup = up_json_dup["results"][0]
    dup_warning = result_dup.get("duplicate_warning")
    print_check(
        "Duplicate Content SHA-256 Warning",
        bool(dup_warning and "already exists" in dup_warning),
        f"Warning: {dup_warning}"
    )

    # --------------------------------------------------------------------------
    # Check 4: Full-Text Search via PostgreSQL tsvector
    # --------------------------------------------------------------------------
    # Allow background worker 2 seconds to process thumbnail and text
    time.sleep(2)
    # Search for "deadlocks" or "CSE1005"
    search_resp = SESSION.get(f"{BASE_URL}/files?q=CSE1005")
    search_passed = "CSE1005_Unit2.pdf" in search_resp.text
    print_check(
        "PostgreSQL Full-Text Search (tsvector GIN query)",
        search_passed,
        "Found 'CSE1005_Unit2.pdf' querying tsvector for 'CSE1005'"
    )

    # --------------------------------------------------------------------------
    # Check 5: Expiring Share Links & QR Code
    # --------------------------------------------------------------------------
    share_data = {
        "csrf_token": csrf_token,
        "duration": "1h",
        "max_downloads": "2"
    }
    share_resp = SESSION.post(f"{BASE_URL}/files/{file_id}/share", data=share_data, allow_redirects=False)
    # Extract shared_token from redirect URL
    loc = share_resp.headers.get("Location", "")
    token_match = re.search(r"shared_token=([^&]+)", loc)
    assert token_match, f"Could not find shared_token in redirect: {loc}"
    share_token = token_match.group(1)

    # Test accessing public share landing page
    share_page = requests.get(f"{BASE_URL}/share/{share_token}")
    print_check(
        "Public Share Page Accessible",
        share_page.status_code == 200 and "CSE1005_Unit2.pdf" in share_page.text,
        f"Loaded /share/{share_token} with expiry countdown"
    )

    # Test QR Code endpoint
    qr_resp = requests.get(f"{BASE_URL}/share/{share_token}/qr")
    print_check(
        "QR Code Generated & Servable",
        qr_resp.status_code == 200 and qr_resp.headers.get("Content-Type") == "image/png",
        f"Served QR code PNG (size: {len(qr_resp.content)} bytes)"
    )

    # Test Download via share link (?download=1)
    dl_resp = requests.get(f"{BASE_URL}/share/{share_token}?download=1")
    print_check(
        "Share Link Download Execution",
        dl_resp.status_code == 200 and dl_resp.headers.get("X-Content-Type-Options") == "nosniff",
        f"Downloaded payload with X-Content-Type-Options: nosniff"
    )

    # Test Expiration by invalidating token in Redis & DB
    subprocess.run(
        ["docker", "exec", "airutil_redis", "redis-cli", "del", f"share:{share_token}"],
        capture_output=True
    )
    subprocess.run(
        ["docker", "exec", "airutil_db", "psql", "-U", "airutil", "-d", "airutil", "-c",
         f"UPDATE share_links SET expires_at = NOW() - INTERVAL '1 hour' WHERE share_token = '{share_token}';"],
        capture_output=True
    )
    expired_resp = requests.get(f"{BASE_URL}/share/{share_token}")
    print_check(
        "Share Link Expiration Enforcement",
        expired_resp.status_code == 410,
        f"Expired link returned HTTP 410 Gone"
    )

    # --------------------------------------------------------------------------
    # Check 6: Renamed .exe pretending to be .jpg classified by real type
    # --------------------------------------------------------------------------
    # DOS/PE Header starts with MZ
    fake_exe_bytes = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00" + b"\x00" * 200
    spoofed_upload = {
        "files": ("innocent_cat.jpg", fake_exe_bytes, "image/jpeg")
    }
    spoof_resp = SESSION.post(f"{BASE_URL}/files/upload", files=spoofed_upload, data=upload_data, headers=headers)
    assert spoof_resp.status_code == 200, f"Upload failed: {spoof_resp.text}"
    spoof_json = spoof_resp.json()
    spoof_result = spoof_json["results"][0]

    mime_is_dosexec = spoof_result["mime_type"] in ("application/x-dosexec", "application/x-msdownload", "application/x-executable")
    cat_is_not_images = spoof_result["category"] != "Images"

    print_check(
        "Anti-Spoofing: .exe disguised as .jpg detected as application/x-dosexec",
        mime_is_dosexec,
        f"Detected real MIME: {spoof_result['mime_type']}"
    )
    print_check(
        "Anti-Spoofing: Disguised executable denied Images category",
        cat_is_not_images,
        f"Assigned Category: {spoof_result['category']}"
    )

    # Verify download of executable forces attachment and nosniff
    spoof_dl = SESSION.get(f"{BASE_URL}/files/{spoof_result['id']}/download")
    disposition = spoof_dl.headers.get("Content-Disposition", "")
    nosniff = spoof_dl.headers.get("X-Content-Type-Options", "")
    print_check(
        "Executable Defense-in-Depth (Forced attachment & nosniff)",
        "attachment" in disposition and nosniff == "nosniff",
        f"Disposition: {disposition}, X-Content-Type-Options: {nosniff}"
    )

    print("=" * 70)
    print("ALL PHASE 3 ACCEPTANCE CHECKS COMPLETED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    main()
