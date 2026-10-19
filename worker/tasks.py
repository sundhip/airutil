"""Asynchronous Background Worker Tasks for Airutil.

Operating Systems Concepts Demonstrated:
1. Asynchronous Task Offloading: CPU and memory-intensive operations (PDF rendering,
   image downsampling, full-text extraction) run in isolated worker processes.
2. Kernel I/O and File Parsing: Reads multi-format binary streams from shared volume
   storage (/vault_storage) without stalling web request threads.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from PIL import Image

from app.db import get_db_cursor

logger = logging.getLogger("airutil.worker.tasks")
STORAGE_PATH = os.getenv("STORAGE_PATH", "/vault_storage")
THUMBNAIL_DIR = os.path.join(STORAGE_PATH, "thumbnails")


def process_file_task(file_id: int) -> bool:
    """Asynchronously process an uploaded file: generate thumbnail and extract searchable text.

    Args:
        file_id: Primary key of file in PostgreSQL.

    Returns:
        True if processed successfully, False otherwise.
    """
    logger.info("Worker starting processing for file_id=%d", file_id)
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)

    file_row = None
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT id, user_id, original_name, file_path, mime_type, category FROM files WHERE id = %s",
                (file_id,)
            )
            file_row = cur.fetchone()
    except Exception as e:
        logger.error("Failed to fetch file metadata for file_id=%d: %s", file_id, e)
        return False

    if not file_row:
        logger.warning("File record not found for file_id=%d", file_id)
        return False

    file_path = file_row["file_path"]
    mime_type = file_row["mime_type"] or ""
    category = file_row["category"] or ""
    thumb_path = ""
    extracted_text = ""

    if not os.path.exists(file_path):
        logger.error("Physical file does not exist at %s", file_path)
        return False

    # 1. Image Thumbnail Generation (Pillow)
    if mime_type.startswith("image/"):
        try:
            target_thumb = os.path.join(THUMBNAIL_DIR, f"{file_id}.png")
            with Image.open(file_path) as img:
                img.thumbnail((256, 256))
                img.save(target_thumb, "PNG")
            thumb_path = target_thumb
            logger.info("Generated image thumbnail at %s", target_thumb)
        except Exception as e:
            logger.warning("Failed to generate image thumbnail for file_id=%d: %s", file_id, e)

    # 2. PDF Processing: Text Extraction & First-Page Thumbnail
    elif mime_type == "application/pdf" or file_path.lower().endswith(".pdf"):
        # Text extraction via pypdf
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            text_parts = []
            for page_idx, page in enumerate(reader.pages[:30]):  # Extract up to first 30 pages
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            extracted_text = "\n".join(text_parts)[:60000]
            logger.info("Extracted %d characters of text from PDF file_id=%d", len(extracted_text), file_id)
        except Exception as e:
            logger.warning("Failed to extract PDF text for file_id=%d: %s", file_id, e)

        # First-page thumbnail rendering via pypdfium2
        try:
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(file_path)
            if len(pdf) > 0:
                page = pdf[0]
                image = page.render(scale=1.5).to_pil()
                image.thumbnail((256, 256))
                target_thumb = os.path.join(THUMBNAIL_DIR, f"{file_id}.png")
                image.save(target_thumb, "PNG")
                thumb_path = target_thumb
                logger.info("Generated PDF thumbnail at %s", target_thumb)
        except Exception as e:
            logger.warning("Failed to render PDF thumbnail for file_id=%d: %s", file_id, e)

    # 3. Plain Text / Source Code Preview Extraction
    elif category == "Code" or mime_type.startswith("text/"):
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                extracted_text = f.read(50000)
            logger.info("Extracted code/text preview for file_id=%d (%d chars)", file_id, len(extracted_text))
        except Exception as e:
            logger.warning("Failed to read text preview for file_id=%d: %s", file_id, e)

    # 4. Update Database Record & Full-Text Search Vector
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE files
                SET extracted_text = %s,
                    thumbnail_path = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (extracted_text, thumb_path, file_id)
            )
        logger.info("Successfully updated file_id=%d with worker results", file_id)
        return True
    except Exception as e:
        logger.error("Failed to commit worker updates for file_id=%d: %s", file_id, e)
        return False
