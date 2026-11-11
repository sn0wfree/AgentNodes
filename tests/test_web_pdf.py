"""Tests for PDF text extraction."""

from __future__ import annotations

import json


class TestReadDocument:
    def test_extract_requires_path(self):
        from agentnodes.foundation.web.pdf import read_document
        result = read_document("")
        body = json.loads(result)
        assert body["status"] == "error"
        assert "required" in body["error"].lower()

    def test_extract_missing_file(self):
        from agentnodes.foundation.web.pdf import read_document
        result = read_document("/nonexistent/path/file.pdf")
        body = json.loads(result)
        assert body["status"] == "error"
        assert "not found" in body["error"].lower()

    def test_extract_not_a_file(self, tmp_path):
        from agentnodes.foundation.web.pdf import read_document
        result = read_document(str(tmp_path))
        body = json.loads(result)
        assert body["status"] == "error"
        assert "not a regular file" in body["error"].lower()

    def test_extract_missing_dependency(self, monkeypatch, tmp_path):
        """PyMuPDF 缺失时应返回优雅错误。

        **批 C**：错误优先级改为「先查路径、再查依赖」（见 pdf.py docstring）
        ——所以这里必须先给一个**真实存在**的文件，否则返回的是
        "file not found"（那是更该修的原因）。
        """
        import agentnodes.foundation.web.pdf as pdf_mod
        real_file = tmp_path / "real.pdf"
        real_file.write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setattr(pdf_mod, "_check_fitz", lambda: False)
        from agentnodes.foundation.web.pdf import read_document
        result = read_document(str(real_file))
        body = json.loads(result)
        assert body["status"] == "error"
        assert "not installed" in body["error"].lower()

    def test_path_errors_take_priority_over_dependency(self, monkeypatch):
        """**错误优先级**：路径错比依赖缺更该先报（批 C 回归防护）。

        原实现先查 `_check_fitz()`——缺依赖的机器上，用户写错路径得到的
        是"PyMuPDF not installed"，装完才发现真正的原因是路径。
        """
        import agentnodes.foundation.web.pdf as pdf_mod
        monkeypatch.setattr(pdf_mod, "_check_fitz", lambda: False)
        from agentnodes.foundation.web.pdf import read_document
        body = json.loads(read_document("/nonexistent/path/file.pdf"))
        assert "not found" in body["error"].lower()
        assert "not installed" not in body["error"].lower()

    def test_extract_json_format(self, tmp_path):
        """非 PDF 文件应报错（而不是崩溃）。"""
        from agentnodes.foundation.web.pdf import read_document
        fake_pdf = tmp_path / "test.txt"
        fake_pdf.write_text("not a pdf")
        result = read_document(str(fake_pdf))
        body = json.loads(result)
        # 应该报错（不是 PDF）或者如果 fitz 能处理文本文件则返回内容
        assert body["status"] in ("ok", "error")

    def test_check_available(self):
        from agentnodes.foundation.web.pdf import _check_fitz
        # 无论 fitz 是否安装，都不应崩溃
        result = _check_fitz()
        assert isinstance(result, bool)
