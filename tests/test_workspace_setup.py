"""Tests for the smart workspace templates scaffold.

Covers:
- Empty workspace → all package templates copied
- Idempotent: user customizations preserved on re-run
- Partial workspace → only missing files copied
- .prompts/ excluded (agent role prompts not workspace content)
- Recursive walk into .skills/ (27 files scaffolded)
- Graceful failure on missing package dir
- Future-proof: nested subdirs handled
"""
from __future__ import annotations

from pathlib import Path

from strategy_research import _TEMPLATES_DIR
from strategy_research.apps.workspace_setup import (
    _EXCLUDED_TOP_DIRS,
    smart_init_workspace_templates,
)

# ── Expected counts (computed against current package) ───────────

# 5 top-level files (README, config.yaml, prepare.py, program.md, strategy.py)
_EXPECTED_TOP_LEVEL_FILES = {
    "README.md", "config.yaml", "prepare.py", "program.md", "strategy.py",
}


def _actual_skills_count() -> int:
    """Derive the .skills/*.md count from the package (grows over time)."""
    skills_dir = _TEMPLATES_DIR / ".skills"
    return len(list(skills_dir.glob("*.md"))) if skills_dir.is_dir() else 0


def _expected_total() -> int:
    """Expected scaffold size: every package template file the walk copies
    (top-level + .skills/ + workflows/, excluding .prompts/ + __pycache__)."""
    total = 0
    for src_path in sorted(_TEMPLATES_DIR.rglob("*")):
        if src_path.is_dir():
            continue
        rel = src_path.relative_to(_TEMPLATES_DIR)
        if rel.parts and rel.parts[0] in _EXCLUDED_TOP_DIRS:
            continue
        if any(part == "__pycache__" for part in rel.parts):
            continue
        total += 1
    return total


# ── 1. Empty workspace → all package templates copied ────────────


class TestSmartScaffoldBasic:
    def test_empty_workspace_copies_top_level(self, tmp_path: Path):
        """Fresh workspace → all top-level files copied."""
        report = smart_init_workspace_templates(tmp_path)
        for f in _EXPECTED_TOP_LEVEL_FILES:
            assert f in report["copied"], f"missing {f} from copied"
            assert (tmp_path / "templates" / f).exists()

    def test_empty_workspace_copies_all_skills(self, tmp_path: Path):
        """All 27 .skills files are copied."""
        report = smart_init_workspace_templates(tmp_path)
        skills_copied = [p for p in report["copied"] if p.startswith(".skills/")]
        assert len(skills_copied) == _actual_skills_count()

    def test_total_file_count(self, tmp_path: Path):
        """Top + skills + workflows = all package template files (no .prompts)."""
        report = smart_init_workspace_templates(tmp_path)
        assert len(report["copied"]) == _expected_total()
        assert report["skipped"] == []
        assert report["errors"] == []


# ── 2. Idempotency — never overwrite existing files ─────────────


class TestSmartScaffoldIdempotent:
    def test_idempotent_no_overwrite(self, tmp_path: Path):
        """Re-run preserves user customizations."""
        smart_init_workspace_templates(tmp_path)
        user_file = tmp_path / "templates" / "strategy.py"
        user_content = "# user custom version\nPARAMS = {'top_n': 42}\n"
        user_file.write_text(user_content)
        report = smart_init_workspace_templates(tmp_path)
        assert user_file.read_text() == user_content
        assert "strategy.py" in report["skipped"]
        # All other files are skipped too
        assert len(report["copied"]) == 0
        assert len(report["skipped"]) == _expected_total()

    def test_partial_workspace_only_copies_missing(self, tmp_path: Path):
        """Partial workspace → only missing files copied."""
        (tmp_path / "templates").mkdir()
        user = tmp_path / "templates" / "strategy.py"
        user.write_text("# user")
        report = smart_init_workspace_templates(tmp_path)
        assert "strategy.py" in report["skipped"]
        assert "config.yaml" in report["copied"]
        # All template files minus the one pre-existing user file
        assert len(report["copied"]) == _expected_total() - 1


# ── 3. Exclusion — .prompts/ never copied ────────────────────────


class TestSmartScaffoldExclusions:
    def test_prompts_not_copied(self, tmp_path: Path):
        """.prompts/ is excluded (agent role prompts)."""
        report = smart_init_workspace_templates(tmp_path)
        assert not any(p.startswith(".prompts") for p in report["copied"])
        assert not (tmp_path / "templates" / ".prompts").exists()

    def test_excluded_top_dirs_constant(self):
        """Sanity check: .prompts is in the exclusion list."""
        assert ".prompts" in _EXCLUDED_TOP_DIRS


# ── 4. Recursive walk — future-proof for nested subdirs ──────────


class TestSmartScaffoldRecursive:
    def test_recursive_into_skills(self, tmp_path: Path):
        """Walk goes into .skills/ (which has 27 files)."""
        report = smart_init_workspace_templates(tmp_path)
        # All .skills files are real .md files in package
        for p in report["copied"]:
            if p.startswith(".skills/"):
                assert p.endswith(".md"), f"unexpected non-md: {p}"

    def test_recursive_into_subdirs(self, tmp_path: Path, monkeypatch):
        """Future-proof: nested subdirs are scaffolded."""
        # Simulate a future package version with a nested subdir
        fake_pkg = tmp_path / "fake_pkg" / "templates"
        fake_pkg.mkdir(parents=True)
        (fake_pkg / "README.md").write_text("# top")
        (fake_pkg / "extra_dir").mkdir()
        (fake_pkg / "extra_dir" / "inner.md").write_text("# inner")
        (fake_pkg / "extra_dir" / "deeper").mkdir()
        (fake_pkg / "extra_dir" / "deeper" / "deep.md").write_text("# deep")
        monkeypatch.setattr(
            "strategy_research.apps.workspace_setup._TEMPLATES_DIR",
            tmp_path / "fake_pkg" / "templates",
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        report = smart_init_workspace_templates(workspace)
        assert "README.md" in report["copied"]
        assert "extra_dir/inner.md" in report["copied"]
        assert "extra_dir/deeper/deep.md" in report["copied"]


# ── 5. Error handling — never crash the server ───────────────────


class TestSmartScaffoldErrors:
    def test_handles_missing_package_dir(self, tmp_path: Path, monkeypatch):
        """If package templates dir missing, returns error but doesn't crash."""
        monkeypatch.setattr(
            "strategy_research.apps.workspace_setup._TEMPLATES_DIR",
            Path("/nonexistent/path/that/does/not/exist"),
        )
        report = smart_init_workspace_templates(tmp_path)
        assert report["copied"] == []
        assert report["skipped"] == []
        assert len(report["errors"]) == 1
        assert "missing" in report["errors"][0]

    def test_creates_templates_dir(self, tmp_path: Path):
        """Workspace without templates/ gets it created."""
        assert not (tmp_path / "templates").exists()
        smart_init_workspace_templates(tmp_path)
        assert (tmp_path / "templates").exists()
        assert (tmp_path / "templates").is_dir()


# ── 6. Returned report shape ─────────────────────────────────────


class TestSmartScaffoldReportShape:
    def test_report_keys(self, tmp_path: Path):
        """Report always has copied/skipped/errors keys."""
        report = smart_init_workspace_templates(tmp_path)
        assert set(report.keys()) == {"copied", "skipped", "errors"}
        assert isinstance(report["copied"], list)
        assert isinstance(report["skipped"], list)
        assert isinstance(report["errors"], list)

    def test_copied_paths_are_relative(self, tmp_path: Path):
        """Copied/skipped paths are relative to templates/."""
        report = smart_init_workspace_templates(tmp_path)
        for p in report["copied"] + report["skipped"]:
            assert not p.startswith("/"), f"absolute path: {p}"
            assert not p.startswith("templates/"), f"includes prefix: {p}"
