# Airutil Phase 5 Report: Burn-on-Read Clip Beam

## 1. Executive Summary & Objective

In **Phase 5**, Airutil implemented **Clip Beam**, an in-memory, burn-on-read ephemeral clipboard transfer utility. Clip Beam allows college students to quickly beam sensitive credentials, Wi-Fi keys, Zoom links, and code snippets across devices without leaving any persistent footprint in PostgreSQL or storage disk volumes (`/vault_storage`).

---

## 2. Key OS & Systems Architecture Concepts Demonstrated

1. **Atomic Read-and-Destroy (Redis `GETDEL` / Linearizability)**:
   - Redis executes operations in a single-threaded sequential event loop.
   - The command `GETDEL beam:<token>` atomically returns the message payload and removes the key in a single atomic instruction.
   - When two clients issue reveal requests simultaneously (even routed through different replicas `airutil_app1` and `airutil_app2`), Redis guarantees race-free linearizability: exactly one recipient receives the payload, while the second receives `None` ("already burned").

2. **Pure Ephemeral RAM Buffering (Zero-Disk Footprint)**:
   - Clip Beam content is strictly stored in Redis with `SETEX` (60 seconds, 5 minutes, 15 minutes TTL).
   - Content is **never written to PostgreSQL** and **never stored on disk volumes** (`/vault_storage`).
   - Verified via `grep -rn "secret" /vault_storage` returning exit code 1 (zero matches).

3. **Multi-Replica In-Memory Synchronization**:
   - Both Flask replicas share the `airutil_redis` service over the isolated bridge network `vault_net`.
   - A beam launched via `app1` can be instantly claimed, verified, and burned via `app2`.

4. **Access Control & Threshold Burn (3-Strike PIN Enforcement)**:
   - Senders can protect beams with an optional 4-digit PIN.
   - Failed attempts are tracked in Redis (`beam:fails:<token>`). On the 3rd failed attempt, the beam key is immediately destroyed (`r.delete(...)`) to prevent online brute-force guessing.

5. **Live Polling & State Tomstones**:
   - When a beam is burned, a transient tombstone key (`beam:status:<token>`) is recorded with a 5-minute TTL.
   - The sender's live dashboard polls `/beam/<token>/status` every 1.5 seconds, dynamically updating the tracking badge from `"Waiting for recipient..."` to `"🔥 Read & Burned!"` without persisting the original message body.

6. **Defense-in-Depth & Boundary Clamping**:
   - Payload size clamped to a strict 5 KB limit (`5120 bytes`).
   - Rate limited to 10 beams per minute per user/IP using Redis counters.
   - All output is strictly HTML-escaped by Jinja2 to prevent Cross-Site Scripting (XSS).

---

## 3. What Changed in Phase 5

### Application & Routing (`app/beam.py`, `app/main.py`)
- Registered `beam_bp` in `app/main.py` and retired placeholder route.
- `create_beam` (`POST /beam/create`): Rate limiting, 5 KB payload cap, optional 4-digit PIN validation, and Redis `SETEX` with TTL.
- `receive_beam` (`GET` & `POST /beam/<token>`): PIN gate verification, 3-strike policy burn, and atomic `GETDEL` reveal.
- `beam_status` (`GET /beam/<token>/status`): Returns live state (`waiting`, `read`, `expired`) and remaining TTL.
- `generate_qr_data_uri`: Dynamic in-memory QR code generation returning base64 data URIs without disk writes.

### User Interface & Styling (`app/templates/`, `app/static/css/style.css`)
- `beam.html`: Creation form with live byte counter, TTL selection (60s, 5m, 15m), PIN input, shareable link, base64 QR code, and real-time polling status badge (`pulse-green`, `pulse-orange`, `pulse-red`).
- `beam_receive.html`: PIN prompt screen, live countdown timer, revealed content box with Copy button and external link launcher, and clear "Burned on Read" confirmation banners.

---

## 4. Verification & Acceptance Results

### Acceptance Checks Run Live (`python scripts/verify_phase5.py`):
```text
======================================================================
AIRUTIL PHASE 5: LIVE ACCEPTANCE VERIFICATION CHECKS
Target Gateway: http://localhost:8080
======================================================================
[PASS] Sender Student Registration
       -> Registered user beam_sender_1792400118
[PASS] Clip Beam Created
       -> Generated token: 2bV5W8T5pChSE9_2Cl-E_XwotfrenNAK
[PASS] Redis GET Shows Key Before Reveal
       -> Redis stores JSON payload in ephemeral RAM: {"token": "2bV5W8T5pChSE9_2Cl-E_XwotfrenNAK", "content": "Co...
[PASS] Redis TTL Decreases Over Time
       -> Initial TTL: 59s -> After 2s: 57s
[PASS] Mobile Phone Browser Reveal Successful
       -> Mobile client successfully revealed secret clip and triggered burn notice
[PASS] Redis GET Returns nil After Reveal (Atomic Burn Confirmed)
       -> Key 'beam:2bV5W8T5pChSE9_2Cl-E_XwotfrenNAK' evicted from RAM. Output: ''
[PASS] Subsequent Read Sees 'Already Burned'
       -> Second reader blocked: message already consumed and destroyed
[PASS] Simultaneous Atomic Burn (Race-Free Linearizability via GETDEL)
       -> Request 1 success: False, Request 2 success: True (Exactly one won the race)
[PASS] Zero-Disk Footprint (grep -rn on /vault_storage finds NOTHING)
       -> No beam content written to disk volume; 100% ephemeral RAM buffering
[PASS] 4-Digit PIN Gate Rendered
       -> PIN prompt displayed before revealing content
[PASS] PIN 3-Strike Policy Burns Beam
       -> 3 wrong attempts burned the beam immediately
[PASS] PIN-Burned Key Evicted from Redis
       -> Key deleted from Redis following 3rd strike
======================================================================
ALL PHASE 5 ACCEPTANCE CHECKS COMPLETED SUCCESSFULLY!
======================================================================
```

### Automated Unit & Integration Tests:
```bash
docker compose exec -T app1 pytest -o cache_dir=/tmp/.pytest_cache tests/
```
Result: **26 passed in 2.69s** (100% pass rate across `test_auth.py`, `test_beam.py`, `test_files.py`, `test_healthz.py`, and `test_vault.py`).

---

## 5. Manual QA Checklist

- [x] Open `http://localhost:8080/beam` on desktop and mobile browsers.
- [x] Paste a code snippet or URL into the textarea and observe the live byte counter update.
- [x] Select **60 Seconds (Ultra Fast Transfer)** and click **Launch Clip Beam**.
- [x] Verify the active beam tracking card displays a live countdown and blinking green pulse.
- [x] Copy the link or scan the QR code from another device (or mobile browser).
- [x] Open the link on the second device; verify content is displayed with the **Burned on Read** notice.
- [x] Notice the sender's dashboard updates in real-time to **🔥 Read & Burned!** without page refresh.
- [x] Refresh the receiver page; verify it immediately states **Clip Beam Already Burned**.
- [x] Create a beam with a 4-digit PIN; enter an incorrect PIN 3 times and confirm the beam is destroyed.
- [x] Run `docker exec airutil_app1 grep -r "<payload>" /vault_storage` and verify zero matches found.
