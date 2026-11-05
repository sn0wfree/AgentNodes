"""PDF text extraction (optional dependency: PyMuPDF)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _check_fitz() -> bool:
    try:
        import fitz  # noqa: F401
        return True
    except ImportError:
        return False


def read_document(
    path: str,
    max_pages: int = 50,
) -> str:
    """Extract text from a PDF document.

    Args:
        path: Path to the PDF file.
        max_pages: Maximum pages to extract (default 50).

    Returns:
        JSON string with extracted text.
    """
    if not path or not path.strip():
        return json.dumps({
            "status": "error",
            "error": "path is required",
        }, ensure_ascii=False)

    if not _check_fitz():
        return json.dumps({
            "status": "error",
            "error": "PyMuPDF not installed. Install with: pip install PyMuPDF",
        }, ensure_ascii=False)

    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        return json.dumps({
            "status": "error",
            "error": f"file not found: {path}",
        }, ensure_ascii=False)
    if not file_path.is_file():
        return json.dumps({
            "status": "error",
            "error": f"not a regular file: {path}",
        }, ensure_ascii=False)

    try:
        import fitz

        doc = fitz.open(str(file_path))
        total_pages = len(doc)
        pages_to_read = min(total_pages, max_pages)

        texts = []
        for i in range(pages_to_read):
            page = doc[i]
            text = page.get_text()
            if text.strip():
                texts.append(f"--- Page {i + 1} ---\n{text}")

        doc.close()

        full_text = "\n\n".join(texts)
        truncated = False
        # 截断到 50K 字符
        max_total = 50_000
        if len(full_text) > max_total:
            full_text = full_text[:max_total] + "\n... [truncated]"
            truncated = True

        return json.dumps({
            "status": "ok",
            "path": str(file_path),
            "total_pages": total_pages,
            "pages_read": pages_to_read,
            "text": full_text,
            "char_count": len(full_text),
            "truncated": truncated,
        }, ensure_ascii=False)

    except Exception as exc:
        logger.warning("read_document failed for %r: %s", path, exc)
        return json.dumps({
            "status": "error",
            "error": f"PDF extraction failed: {exc}",
            "path": str(file_path),
        }, ensure_ascii=False)
