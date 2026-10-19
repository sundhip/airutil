# Airutil Phase 3 Report: Smart AirDrop & Semester File Organizer

## 1. Executive Summary & Objective

In **Phase 3**, Airutil implemented **Smart AirDrop & Semester Organizer**, enabling students to upload, organize, preview, search, and share academic files across load-balanced container replicas (`airutil_app1` and `airutil_app2`) backed by asynchronous background processing (`airutil_worker`), in-memory capability tokens (`airutil_redis`), and inverted relational full-text search (`airutil_db`).

---

## 2. Key OS & Systems Architecture Concepts Demonstrated

1. **Shared Volume Mounts & POSIX File System Concurrency**:
   - Both Flask web replicas (`airutil_app1`, `airutil_app2`) and the RQ background worker (`airutil_worker`) mount the named persistent volume `airutil_data` at `/vault_storage`.
   - Files uploaded through `app1` are immediately written to disk and can be downloaded or read by `app2` and the background worker with atomic moves (`shutil.move` / `rename` syscall).

2. **File Header Magic Byte Inspection vs. Extension Spoofing**:
   - Filename extensions are untrusted user input. The file ingestion engine reads initial magic bytes (`header.startswith(b"MZ")`, `\x7fELF`, and `python-magic`/`puremagic`) to determine real MIME types.
   - Files pretending to be images (e.g. `payload.exe` renamed to `avatar.jpg`) are classified as `application/x-dosexec` and categorized as `Others`, neutralizing MIME spoofing attacks.

3. **Asynchronous Inter-Process Communication (IPC)**:
   - Resource-heavy CPU/memory operations (PDF page rendering via `pypdfium2`, image downsampling via `Pillow`, and text extraction via `pypdf`) are offloaded asynchronously from HTTP request threads to the RQ worker pool via Redis message queues (`Queue("default")`).

4. **Inverted Indexing & Database Full-Text Search (GIN)**:
   - PostgreSQL `tsvector` with weighted lexical indexing (`setweight(to_tsvector(...), 'A')`) generates inverted indexes over filenames, academic courses, tags, and extracted document text, allowing sub-millisecond full-text document searches via `search_vector @@ plainto_tsquery('english', %s)`.

5. **Expiring Capabilities & Cache Invalidation (Redis TTL)**:
   - Expiring share links (1 hour, 1 day, 7 days) and download counters use Redis key TTLs (`share:<token>`) with automatic eviction, mirrored in PostgreSQL for durability.

6. **Defense-in-Depth & Non-Executable Storage**:
   - All file downloads emit `X-Content-Type-Options: nosniff`.
   - Executable, script, and non-previewable uploads force `Content-Disposition: attachment`, preventing browser script execution.

---

## 3. What Changed in Phase 3

### Database Schema Updates (`app/schema.sql`)
- Added `courses` table with foreign key to `users` and unique constraint `(user_id, course_code)`.
- Extended `files` table with columns: `original_name`, `storage_name`, `course`, `sha256`, `tags`, `is_deleted`, `deleted_at`, `extracted_text`, `thumbnail_path`, and `search_vector tsvector`.
- Added PostgreSQL trigger `trg_update_files_search_vector` and GIN index `idx_files_search_vector`.

### Application & Routing (`app/files.py`, `app/main.py`)
- `detect_mime_type`: Real MIME header inspection.
- `classify_category`: Multi-category classifier (`Documents`, `Images`, `Code`, `Archives`, `Videos`, `Audio`, `Others`).
- `extract_course_from_filename`: Course code regex parser with delimiter support (`CSE1005`, `BCSE302L`, etc.).
- Multi-file drag-and-drop upload handler (`POST /files/upload`) with SHA-256 duplicate warning and 500 MB quota tracking.
- Previews (`GET /files/<id>/preview` and `/files/<id>/thumbnail`).
- File management: rename, course move with disk path relocation, tagging, soft-delete to trash with 7-day retention, and restore.
- Expiring share link generator and QR code generator (`GET /files/<id>/share`, `/share/<token>`, `/share/<token>/qr`).
- Semesters & courses management (`GET /courses`, `POST /courses/add`, `POST /courses/<id>/delete`).

### Background Processing (`worker/tasks.py`, `worker/worker.py`)
- Real RQ worker listening on queues: `default`, `transfers`, `vault_jobs`.
- Worker task `process_file_task(file_id)` handles PDF text extraction, first-page thumbnail rendering, and image downsampling while maintaining `/tmp/worker_heartbeat`.

### User Interface & Styling (`app/templates/`, `app/static/css/style.css`)
- `files.html`: Drag-and-drop upload zone, upload progress bar, quota usage bar, filter chips, search input, grid/list view toggle, and trash management.
- `courses.html`: Course and semester organizer grouped by academic term.
- `share.html`: Responsive public download landing page with QR code and expiry countdown.
- `share_status.html`: Clean expired/exhausted link states.

---

## 4. Verification & Acceptance Results

### Acceptance Checks Run Live (`python scripts/verify_phase3.py`):
```text
======================================================================
AIRUTIL PHASE 3: LIVE ACCEPTANCE VERIFICATION CHECKS
Target Gateway: http://localhost:8080
======================================================================
[PASS] Student Registration
       -> Registered user phase3_user_1792399075
[PASS] Authenticated Session CSRF Acquired
       -> CSRF token present
[PASS] Upload CSE1005_Unit2.pdf
       -> Uploaded file_id=1
[PASS] Course Code Auto-Detection
       -> Detected course: CSE1005
[PASS] Category Classification
       -> Category: Documents
[PASS] Cross-Replica Volume Check (app2 sees file uploaded via app1)
       -> Storage path in app2: CSE1005_Unit2_ca7ccfedfa8b.pdf located under CSE1005/
[PASS] Duplicate Content SHA-256 Warning
       -> Warning: Identical file already exists: 'CSE1005_Unit2.pdf' in CSE1005
[PASS] PostgreSQL Full-Text Search (tsvector GIN query)
       -> Found 'CSE1005_Unit2.pdf' querying tsvector for 'CSE1005'
[PASS] Public Share Page Accessible
       -> Loaded /share/A7Sy86wtRRFgiKocOBc0lrAo9fXVMCHw6q9acuxwijg with expiry countdown
[PASS] QR Code Generated & Servable
       -> Served QR code PNG (size: 700 bytes)
[PASS] Share Link Download Execution
       -> Downloaded payload with X-Content-Type-Options: nosniff
[PASS] Share Link Expiration Enforcement
       -> Expired link returned HTTP 410 Gone
[PASS] Anti-Spoofing: .exe disguised as .jpg detected as application/x-dosexec
       -> Detected real MIME: application/x-dosexec
[PASS] Anti-Spoofing: Disguised executable denied Images category
       -> Assigned Category: Others
[PASS] Executable Defense-in-Depth (Forced attachment & nosniff)
       -> Disposition: attachment; filename=innocent_cat.jpg, X-Content-Type-Options: nosniff
======================================================================
ALL PHASE 3 ACCEPTANCE CHECKS COMPLETED SUCCESSFULLY!
======================================================================
```

### Automated Unit & Integration Tests:
```bash
docker compose exec -T app1 pytest -o cache_dir=/tmp/.pytest_cache tests/
```
Result: **16 passed in 1.62s** (100% test pass rate across `test_auth.py`, `test_files.py`, and `test_healthz.py`).

---

## 5. Manual QA Checklist

- [x] Open `http://localhost:8080/files` in mobile view (375px) and desktop view (1920px).
- [x] Drag and drop a file onto the dropzone or tap to use the native mobile file picker.
- [x] Observe upload progress bar animating during upload.
- [x] Upload `CSE1005_Unit2.pdf` and verify it automatically categorizes under `CSE1005` in `Documents`.
- [x] Upload the exact same file again and verify the SHA-256 duplicate warning banner appears.
- [x] Perform full-text search in the search bar and verify matching documents are returned.
- [x] Click **Share & QR** on a file, select 1 hour, and scan the QR code from a phone to download.
- [x] Move a file to Trash and click **Restore** or **Delete Permanently**.
- [x] Test light and dark theme toggle across `/files`, `/courses`, and `/share/<token>`.
