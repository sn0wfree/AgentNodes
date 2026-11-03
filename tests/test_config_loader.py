"""Tests for foundation/config_loader.py (Phase 2.1)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.config_loader import (
    ConfigBuilder,
    known_keys_filter,
    load_layered_config,
    scalar_key_filter,
)


class TestLoadLayeredConfig(unittest.TestCase):

    def test_defaults_only(self):
        result = load_layered_config(defaults={"a": 1, "b": 2})
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_cli_overrides_wins(self):
        result = load_layered_config(
            defaults={"a": 1},
            cli_overrides={"a": 2},
        )
        self.assertEqual(result["a"], 2)

    def test_priority_cli_over_workspace_over_user(self):
        with TemporaryDirectory() as tmpdir:
            user_path = Path(tmpdir) / "user.yaml"
            ws_path = Path(tmpdir) / "ws.yaml"
            user_path.write_text("a: 1\nb: 1\n", encoding="utf-8")
            ws_path.write_text("a: 2\nc: 1\n", encoding="utf-8")
            result = load_layered_config(
                defaults={"a": 0, "b": 0, "c": 0, "d": 0},
                user_path=user_path,
                workspace_path=ws_path,
                cli_overrides={"a": 3},
            )
            # a: cli wins
            self.assertEqual(result["a"], 3)
            # b: user only (1)
            self.assertEqual(result["b"], 1)
            # c: workspace only (1)
            self.assertEqual(result["c"], 1)
            # d: defaults only (0)
            self.assertEqual(result["d"], 0)

    def test_missing_yaml_silent(self):
        result = load_layered_config(
            defaults={"a": 1},
            user_path=Path("/nonexistent/path.yaml"),
            workspace_path=Path("/also/nonexistent.yaml"),
        )
        self.assertEqual(result, {"a": 1})

    def test_none_in_cli_dropped(self):
        result = load_layered_config(
            defaults={"a": 1, "b": 2},
            cli_overrides={"a": None, "b": 99},
        )
        self.assertEqual(result["a"], 1)
        self.assertEqual(result["b"], 99)

    def test_yaml_list_value_dropped_by_default(self):
        """Without key_filter, lists are passed through (default behavior)."""
        with TemporaryDirectory() as tmpdir:
            user_path = Path(tmpdir) / "user.yaml"
            user_path.write_text("a:\n  - 1\n  - 2\n", encoding="utf-8")
            result = load_layered_config(
                defaults={"a": "scalar"},
                user_path=user_path,
            )
            self.assertEqual(result["a"], [1, 2])

    def test_scalar_key_filter_drops_list(self):
        with TemporaryDirectory() as tmpdir:
            user_path = Path(tmpdir) / "user.yaml"
            user_path.write_text("a:\n  - 1\n  - 2\nb: hello\n", encoding="utf-8")
            result = load_layered_config(
                defaults={"a": "default", "b": "default"},
                user_path=user_path,
                key_filter=scalar_key_filter,
            )
            # a: list dropped, default kept
            self.assertEqual(result["a"], "default")
            # b: scalar passed through
            self.assertEqual(result["b"], "hello")

    def test_known_keys_filter_rejects_unknown(self):
        result = load_layered_config(
            defaults={"known": 1},
            cli_overrides={"known": 99, "unknown": 42},
            key_filter=known_keys_filter({"known"}),
        )
        self.assertEqual(result["known"], 99)
        self.assertNotIn("unknown", result)

    def test_path_resolver(self):
        """path_resolver is called with the original path."""
        with TemporaryDirectory() as tmpdir:
            real_path = Path(tmpdir) / "real.yaml"
            real_path.write_text("a: 1\n", encoding="utf-8")

            captured: list[Path] = []

            def resolver(p):
                captured.append(p)
                return real_path

            load_layered_config(
                defaults={"a": 0},
                user_path=Path("~/virtual.yaml"),
                path_resolver=resolver,
            )
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0], Path("~/virtual.yaml"))

    def test_returns_new_dict(self):
        """Mutation safety: defaults dict is not mutated."""
        defaults = {"a": 1}
        result = load_layered_config(
            defaults=defaults,
            cli_overrides={"b": 2},
        )
        result["c"] = 3
        self.assertNotIn("c", defaults)


class TestConfigBuilder(unittest.TestCase):

    def test_basic_chain(self):
        cfg = (ConfigBuilder(defaults={"a": 0, "b": 0})
               .with_cli_overrides({"a": 1})
               .build())
        self.assertEqual(cfg, {"a": 1, "b": 0})

    def test_layer_priority(self):
        """Later with_* calls override earlier ones."""
        with TemporaryDirectory() as tmpdir:
            user_path = Path(tmpdir) / "user.yaml"
            ws_path = Path(tmpdir) / "ws.yaml"
            user_path.write_text("a: 1\n", encoding="utf-8")
            ws_path.write_text("a: 2\n", encoding="utf-8")
            cfg = (ConfigBuilder(defaults={"a": 0, "b": 0})
                   .with_user_yaml(user_path)
                   .with_workspace_yaml(ws_path)
                   .with_cli_overrides({"a": 3})
                   .build())
            self.assertEqual(cfg["a"], 3)

    def test_yaml_data_pre_parsed(self):
        cfg = (ConfigBuilder(defaults={"a": 0})
               .with_yaml_data("bridge", {"a": 5, "b": 10})
               .build())
        self.assertEqual(cfg["a"], 5)
        self.assertEqual(cfg["b"], 10)

    def test_post_hook_transform(self):
        def hook(d):
            d["derived"] = d["a"] * 2
            return d
        cfg = (ConfigBuilder(defaults={"a": 3})
               .with_post_hook(hook)
               .build())
        self.assertEqual(cfg["derived"], 6)

    def test_post_hook_return_none_keeps_dict(self):
        def hook(d):
            return None  # do not replace
        cfg = (ConfigBuilder(defaults={"a": 1})
               .with_post_hook(hook)
               .build())
        self.assertEqual(cfg["a"], 1)

    def test_multiple_post_hooks_run_in_order(self):
        calls: list[str] = []

        def hook1(d):
            calls.append("hook1")
            return d

        def hook2(d):
            calls.append("hook2")
            return d

        ConfigBuilder(defaults={}).with_post_hook(hook1).with_post_hook(hook2).build()
        self.assertEqual(calls, ["hook1", "hook2"])

    def test_empty_builder(self):
        cfg = ConfigBuilder(defaults={"a": 1}).build()
        self.assertEqual(cfg, {"a": 1})


if __name__ == "__main__":
    unittest.main()
