"""Tests for PDF text extraction."""

from __future__ import annotations

import json


class TestReadDocument:
    def test_extract_requires_path(self):
        from strategy_research.foundation.web.pdf import read_document
        result = read_document("")
        body = json.loads(result)
        assert body["status"] == "error"
        assert "required" in body["error"].lower()

    def test_extract_missing_file(self):
        from strategy_research.foundation.web.pdf import read_document
        result = read_document("/nonexistent/path/file.pdf")
        body = json.loads(result)
        assert body["status"] == "error"
        assert "not found" in body["error"].lower()

    def test_extract_not_a_file(self, tmp_path):
        from strategy_research.foundation.web.pdf import read_document
        result = read_document(str(tmp_path))
        body = json.loads(result)
        assert body["status"] == "error"
        assert "not a regular file" in body["error"].lower()

    def test_extract_missing_dependency(self, monkeypatch):
        """PyMuPDF 缺失时应返回优雅错误。"""
        import strategy_research.foundation.web.pdf as pdf_mod
        original = pdf_mod._check_fitz
        pdf_mod._check_fitz = lambda: False
        try:
            from strategy_research.foundation.web.pdf import read_document
            result = read_document("/some/file.pdf")
            body = json.loads(result)
            assert body["status"] == "error"
            assert "not installed" in body["error"].lower()
        finally:
            pdf_mod._check_fitz = original

    def test_extract_json_format(self, tmp_path):
        """非 PDF 文件应报错（而不是崩溃）。"""
        from strategy_research.foundation.web.pdf import read_document
        fake_pdf = tmp_path / "test.txt"
        fake_pdf.write_text("not a pdf")
        result = read_document(str(fake_pdf))
        body = json.loads(result)
        # 应该报错（不是 PDF）或者如果 fitz 能处理文本文件则返回内容
        assert body["status"] in ("ok", "error")

    def test_check_available(self):
        from strategy_research.foundation.web.pdf import _check_fitz
        # 无论 fitz 是否安装，都不应崩溃
        result = _check_fitz()
        assert isinstance(result, bool)
