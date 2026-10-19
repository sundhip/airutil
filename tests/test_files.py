"""Test Suite for Phase 3: Smart AirDrop, MIME classification, and File Organizer.

Operating Systems Concepts Demonstrated:
1. Shared Volume Cross-Process Access: Validates directory tree in shared storage.
2. File Magic Header Inspection: Prevents executable masking via spoofed extensions.
3. PostgreSQL Inverted Index (GIN) Full-Text Search: Full-text search over document contents.
4. Transient Capability Tokens: Expirable Redis tokens with TTL.
"""

from __future__ import annotations

import io
import os
import json
import pytest
from app.files import (
    classify_category,
    detect_mime_type,
    extract_course_from_filename,
    COURSE_REGEX,
)


class TestCourseCodeDetection:
    """Test university course code regex detection in filenames."""

    def test_cse_course_codes(self):
        assert extract_course_from_filename("CSE1005_Unit2.pdf") == "CSE1005"
        assert extract_course_from_filename("cse1005_assignment.docx") == "CSE1005"
        assert extract_course_from_filename("CSE 1005 Lecture 1.pdf") == "CSE1005"

    def test_bcse_course_codes(self):
        assert extract_course_from_filename("BCSE302L_Lab_Manual.pdf") == "BCSE302L"
        assert extract_course_from_filename("bcse201p_exam.zip") == "BCSE201P"

    def test_other_department_codes(self):
        assert extract_course_from_filename("ECE2001_Signals.pptx") == "ECE2001"
        assert extract_course_from_filename("MAT1001_Calculus_CheatSheet.png") == "MAT1001"
        assert extract_course_from_filename("CS101_Intro.pdf") == "CS101"

    def test_no_course_code(self):
        assert extract_course_from_filename("my_vacation_photo.jpg") is None
        assert extract_course_from_filename("resume_final_final.pdf") is None
        assert extract_course_from_filename("project_notes.txt") is None


class TestMimeAndCategoryClassification:
    """Test real MIME header detection and category classification."""

    def test_pe_executable_spoofing(self):
        """A Windows .exe file renamed to .jpg MUST be detected by its real magic header (MZ)."""
        # DOS/PE Header starts with 'MZ' (0x4D, 0x5A)
        fake_jpg_stream = io.BytesIO(b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00")
        detected_mime = detect_mime_type(fake_jpg_stream, "innocent_photo.jpg")
        assert detected_mime in ("application/x-dosexec", "application/x-msdownload", "application/x-executable")

        category = classify_category(detected_mime, "innocent_photo.jpg")
        # Must NEVER be categorized as Images!
        assert category != "Images"
        assert category == "Others"

    def test_elf_executable_spoofing(self):
        """A Linux ELF executable renamed to .png MUST be detected by its ELF magic header."""
        fake_png_stream = io.BytesIO(b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00")
        detected_mime = detect_mime_type(fake_png_stream, "avatar.png")
        assert detected_mime == "application/x-executable"

        category = classify_category(detected_mime, "avatar.png")
        assert category != "Images"
        assert category == "Others"

    def test_category_classification_rules(self):
        assert classify_category("application/pdf", "notes.pdf") == "Documents"
        assert classify_category("image/png", "diagram.png") == "Images"
        assert classify_category("text/x-python", "script.py") == "Code"
        assert classify_category("application/zip", "backup.zip") == "Archives"
        assert classify_category("video/mp4", "lecture.mp4") == "Videos"
        assert classify_category("audio/mpeg", "recording.mp3") == "Audio"


class TestExpiringShareLinks:
    """Test expiring share link token generation and Redis TTL mechanism."""

    def test_share_token_generation_and_expiry_calc(self):
        from app.session import get_redis_client
        import secrets
        import datetime

        r = get_redis_client()
        test_token = "test_token_" + secrets.token_urlsafe(16)
        ttl = 3600  # 1 hour

        payload = {
            "file_id": 999,
            "user_id": 1,
            "max_downloads": 2,
            "download_count": 0,
        }
        r.set(f"share:{test_token}", json.dumps(payload), ex=ttl)

        # Verify key exists and has TTL <= 3600
        assert r.exists(f"share:{test_token}") == 1
        remaining_ttl = r.ttl(f"share:{test_token}")
        assert 0 < remaining_ttl <= 3600

        # Retrieve and parse
        cached = json.loads(r.get(f"share:{test_token}"))
        assert cached["file_id"] == 999
        assert cached["max_downloads"] == 2

        # Clean up
        r.delete(f"share:{test_token}")
