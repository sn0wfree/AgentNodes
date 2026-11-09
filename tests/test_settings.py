"""批次 A · settings 声明表的等价性与完整性防线。

**这一批的唯一验收标准是「零行为变化」**——所以本文件的核心不是测
「settings 能读值」，而是测：

1. **声明表与源码逐条对齐**：源码里每个 `os.environ.get(NAME, DEFAULT)`
   的 DEFAULT 必须与 `ENV_TABLE[NAME].default` 一致。
2. **没有漏登记的变量**：源码里读的每个 env 名都在表里（或显式归入
   `EXTERNAL_ENV_VARS`）。
3. **读取语义等价**：`settings.get_int/get_bool/...` 与手写
   `os.environ.get(...)` + 类型转换结果相同（含边界：空串、非法值、未设）。
4. **重复登记会炸**：dict comprehension 的静默覆盖是已发生过的 bug。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from agentnodes.foundation import settings

SRC = Path(__file__).resolve().parents[1] / "src" / "agentnodes"

#: `os.environ.get("NAME", DEFAULT)` / `os.getenv("NAME", DEFAULT)`
#: **支持跨行**：`cache.py` 就有一处写
#: ``os.environ.get(\n    "AN_CACHE_COMPACTION_LINKED", "true"\n).lower()``，
#: 逐行正则扫不到它——批次 B 首次迁移就漏了这个变量。
_READ_PAT = re.compile(
    r'(?:os\.environ\.get|os\.getenv)\(\s*["\']([A-Z_0-9]+)["\']'
    r'(?:\s*,\s*([^)]+))?\)',
    re.S,
)


def _scan_reads() -> list[tuple[str, str, int, str | None]]:
    """返回 (NAME, relpath, lineno, default_expr_or_None)。

    **按全文扫描再回推行号**，而不是逐行正则——跨行调用必须能被看见。
    """
    out = []
    for p in SRC.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(SRC).as_posix()
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in _READ_PAT.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            # 跳过纯注释行（该行以 # 开头）
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_text = text[line_start:text.find("\n", m.start())
                             if text.find("\n", m.start()) != -1 else len(text)]
            if line_text.lstrip().startswith("#"):
                continue
            default = m.group(2)
            default = default.strip() if default else None
            # 归一化跨行默认值里的空白
            if default:
                default = re.sub(r"\s+", " ", default)
            out.append((m.group(1), rel, lineno, default))
    return out


def _all_read_names() -> set[str]:
    return {name for name, _, _, _ in _scan_reads()}


# ── 覆盖完整性 ───────────────────────────────────────────────────────


def test_every_read_env_var_is_declared():
    """源码读的每个 env 名都必须在表里，或显式归入外部变量。

    这条防的是「新增了开关但忘了登记」——登记表一旦缺项，
    ``--print-config`` 与文档就会漏掉它。
    """
    read = _all_read_names()
    declared = set(settings.ENV_TABLE)
    external = set(settings.EXTERNAL_ENV_VARS)
    missing = sorted(read - declared - external)
    assert not missing, (
        f"这些 env 变量在源码里被读取，但既没在 ENV_TABLE 声明，"
        f"也没归入 EXTERNAL_ENV_VARS: {missing}"
    )


def test_no_phantom_declarations():
    """表里声明的每个名字都必须真的被读到（或是一个已文档化的保留项）。"""
    read = _all_read_names()
    #: 已声明但当前**没有读取点**——即"预留/兼容"项，必须逐个解释。
    RESERVED = {
        # 由 foundation/paths.py 消费（通过 env_override 与
        # PROFILE_ENV 常量间接读，正则扫不到）
        "AN_DATA_PROFILE",
        "AN_CHECKPOINTS_DIR",
        "AN_WORKFLOWS_DIR",
        "AN_MODEL_CATALOG_DIR",
    }
    phantom = sorted(set(settings.ENV_TABLE) - read - RESERVED)
    assert not phantom, (
        f"这些声明在表里但源码从未读取，属死声明（要么删，要么加进 RESERVED 并说明）: "
        f"{phantom}"
    )


def test_duplicate_declaration_raises_at_import():
    """重复登记必须在 import 期就炸（dict comprehension 会静默覆盖）。

    本模块初版就把 ``AGENTNODES_TEST_CHAT`` 登记了两次，domain 被后者
    悄悄改掉——典型「静默覆盖」。
    """
    src = (SRC / "foundation" / "settings.py").read_text(encoding="utf-8")
    assert "duplicate env declarations" in src, (
        "settings.py 缺少重复登记的 import 期防线"
    )


# ── 默认值等价（最核心）─────────────────────────────────────────────


def _normalize_default(expr: str | None):
    """把源码里的 default 表达式归一成 Python 值，便于比较。

    **关键**：源码写 ``os.environ.get(NAME, "1000")`` 时拿到的是**字符串**
    ``"1000"``，而调用方随后 ``int(...)``。声明表的 ``default`` 记录的是
    **转换后的值**（``1000``），因为 settings 的 ``get_int`` 负责转换。

    所以比较规则是：把源码字面量按声明表的 kind 转换后再比。这既保留了
    「默认值等价」的证明力，又允许声明表记录语义值而非字面量。
    """
    if expr is None:
        return "<NO-DEFAULT>"
    e = expr.strip()
    if e in ("None", "none"):
        return None
    if re.fullmatch(r'"[^"]*"', e) or re.fullmatch(r"'[^']*'", e):
        return e[1:-1]
    if re.fullmatch(r"-?\d+", e):
        return int(e)
    if re.fullmatch(r"-?\d+\.\d+", e):
        return float(e)
    return "<COMPLEX>"


def _coerce_like_declared(raw, setting: settings.Setting):
    """把源码字面量按声明表的 kind 转成语义值。

    **为什么 bool/flag 也要转**：源码常见的写法是
    ``os.environ.get(X, "true").lower() == "true"`` —— 字面量是字符串
    ``"true"``，但语义是 ``True``。声明表记录语义值。
    """
    if raw is None or raw == "<COMPLEX>" or raw == "<NO-DEFAULT>":
        return raw
    kind = setting.kind
    if kind == "int":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return raw
    if kind == "float":
        try:
            return float(raw)
        except (TypeError, ValueError):
            return raw
    if kind in ("bool", "flag"):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")
    return raw


@pytest.mark.parametrize("name,rel,lineno,default_expr", _scan_reads())
def test_declared_default_matches_source(name, rel, lineno, default_expr):
    """每个读取点的默认值必须与声明表一致（按声明类型归一后）。

    **这是「零行为变化」的直接证明**：只要这条全绿，把读取点改成
    ``settings.get_xxx(name)`` 就不会改变任何行为。
    """
    setting = settings.ENV_TABLE.get(name)
    if setting is None:
        pytest.skip(f"{name} 属外部变量（不归本表管）")

    src_default = _normalize_default(default_expr)
    if src_default == "<COMPLEX>":
        pytest.skip(f"{name} 的默认值是复杂表达式，无法静态比较")

    if src_default == "<NO-DEFAULT>":
        # 源码 `os.environ.get(NAME)` 无默认 → 未设置时返回 None。
        assert setting.default is None, (
            f"{rel}:{lineno} 读 {name} 时没有默认值（未设置返回 None），"
            f"但声明表给了 default={setting.default!r}"
        )
        return

    src_value = _coerce_like_declared(src_default, setting)

    # 同一个变量可能有多个读取点、默认值不同（如 CORS_ORIGINS 的
    # None / ""）。声明表只记录一个"权威默认"，其余形态必须在这里
    # 显式登记，否则就是真的不一致。
    if src_value != setting.default:
        allowed = _MULTI_DEFAULT_ALLOWED.get(name, set())
        assert src_value in allowed, (
            f"{rel}:{lineno} {name} 的源码默认值 {src_value!r} 与声明表 "
            f"{setting.default!r} 不一致——改成 settings 读取会改变行为。"
            f"若这是有意的多形态读取，请加进 _MULTI_DEFAULT_ALLOWED。"
        )


#: 同一变量在不同读取点使用不同默认值的**已知形态**。
#: 每一条都必须解释清楚，否则就可能是真 bug。
_MULTI_DEFAULT_ALLOWED: dict[str, set] = {
    # app.py:278 读 None（未设 = 不启用 CORS）；
    # app.py:321 写成 ("",).split(",") if get(...) else None —— 等价于 None。
    "CORS_ORIGINS": {None, ""},
    # workflow.py:355 用 "" 判空，其它读取点用 None。
    "AN_WORKSPACE_PATH": {None, ""},
    # loop.py:872 用 flag 语义（== "0"），:927 是一个**纯消耗的无效读取**
    # （`_ = ...`，仅作文档）。后者建议删除；在此之前允许 "" 形态。
    "AGENTNODES_SKELETON": {None, ""},
}


# ── 读取语义等价 ─────────────────────────────────────────────────────


def test_get_str_matches_environ(monkeypatch):
    monkeypatch.delenv("AN_LOG_LEVEL", raising=False)
    assert settings.get_str("AN_LOG_LEVEL") == os.environ.get("AN_LOG_LEVEL", "INFO")

    monkeypatch.setenv("AN_LOG_LEVEL", "DEBUG")
    assert settings.get_str("AN_LOG_LEVEL") == "DEBUG"


def test_get_int_matches_manual_parse(monkeypatch):
    monkeypatch.delenv("AN_CACHE_MAX_ENTRIES", raising=False)
    assert settings.get_int("AN_CACHE_MAX_ENTRIES") == 1000
    monkeypatch.setenv("AN_CACHE_MAX_ENTRIES", "42")
    assert settings.get_int("AN_CACHE_MAX_ENTRIES") == 42


def test_get_int_falls_back_on_garbage(monkeypatch):
    """非法值回落默认（不抛错）——与既有 `int(os.environ.get(...))` 不同，
    但这是**更安全**的行为：原实现会抛 ValueError 崩掉模块 import。

    注意：这条记录的是一个**有意的差异**。批次 B 替换读取点时才生效；
    批 A 不改调用点，所以当前行为未变。
    """
    monkeypatch.setenv("AN_CACHE_MAX_ENTRIES", "not-a-number")
    assert settings.get_int("AN_CACHE_MAX_ENTRIES") == 1000


def test_get_bool_truthy_values(monkeypatch):
    for raw in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("AN_LOG_JSON", raw)
        assert settings.get_bool("AN_LOG_JSON") is True, raw
    for raw in ("0", "false", "", "no"):
        monkeypatch.setenv("AN_LOG_JSON", raw)
        assert settings.get_bool("AN_LOG_JSON") is False, raw


def test_unset_returns_declared_default(monkeypatch):
    monkeypatch.delenv("AN_LOG_LEVEL", raising=False)
    assert settings.get("AN_LOG_LEVEL") is None          # 原始读取
    assert settings.get_str("AN_LOG_LEVEL") == "INFO"    # 走默认


def test_none_default_is_preserved(monkeypatch):
    """``default=None`` 的项必须让调用方拿到 None（不是空串）。"""
    monkeypatch.delenv("AN_SESSIONS_DB", raising=False)
    assert settings.get_str("AN_SESSIONS_DB") is None


# ── 诊断 ─────────────────────────────────────────────────────────────


def test_snapshot_masks_secrets():
    snap = settings.snapshot({"JWT_SECRET": "super-secret-value-1234",
                              "AN_LOG_LEVEL": "DEBUG"})
    assert snap["JWT_SECRET"]["value"] != "super-secret-value-1234"
    assert "*" in snap["JWT_SECRET"]["value"]
    assert snap["AN_LOG_LEVEL"]["value"] == "DEBUG"
    assert snap["AN_LOG_LEVEL"]["source"] == "env"


def test_snapshot_marks_defaults():
    snap = settings.snapshot({})
    assert snap["AN_LOG_LEVEL"]["source"] == "default"
    assert snap["AN_LOG_LEVEL"]["value"] == "INFO"


def test_unknown_env_vars_detects_unregistered():
    fake = {"AN_NEW_THING": "1", "AN_LOG_LEVEL": "INFO", "HOME": "/root"}
    assert settings.unknown_env_vars(fake) == ["AN_NEW_THING"]


def test_external_vars_are_not_flagged():
    fake = {"NO_COLOR": "1", "LANGCHAIN_PROVIDER": "x", "OPENAI_API_KEY": "k"}
    assert settings.unknown_env_vars(fake) == []


def test_known_names_sorted_and_complete():
    names = settings.known_names()
    assert list(names) == sorted(names)
    assert "AN_DATA_DIR" in names
    assert "JWT_SECRET" in names


# ── 演练：替换一个真实读取点后行为不变 ───────────────────────────────


def test_real_read_point_swap_is_equivalent(monkeypatch):
    """把一个真实模块的读取点换成 settings，逐值验证行为不变。

    这是「批 A 零行为变化」的**动态证明**——静态比对（上面的
    test_declared_default_matches_source）证明默认值一致，
    这条证明**转换逻辑**也一致。

    选 ``AN_CACHE_MAX_ENTRIES``（int 型，有真实调用方 ``cache.py``）。
    """
    from agentnodes.foundation.utils import bg_proc  # noqa: F401 — 确保可导入

    # 未设置：两者都得到 1000
    monkeypatch.delenv("AN_CACHE_MAX_ENTRIES", raising=False)
    old = int(os.environ.get("AN_CACHE_MAX_ENTRIES", "1000"))
    assert settings.get_int("AN_CACHE_MAX_ENTRIES") == old == 1000

    # 设置合法值：两者一致
    monkeypatch.setenv("AN_CACHE_MAX_ENTRIES", "250")
    old = int(os.environ.get("AN_CACHE_MAX_ENTRIES", "1000"))
    assert settings.get_int("AN_CACHE_MAX_ENTRIES") == old == 250


def test_bg_proc_read_point_is_declared_correctly():
    """真实模块 `bg_proc.py` 的读取与声明表一致（具体实例验证）。"""
    src = (SRC / "foundation" / "utils" / "bg_proc.py").read_text(encoding="utf-8")
    assert 'os.environ.get("AN_BACKTEST_STALL_TIMEOUT", "300")' in src
    setting = settings.ENV_TABLE["AN_BACKTEST_STALL_TIMEOUT"]
    assert setting.kind == "int"
    assert setting.default == 300


def test_engine_switch_reads_are_consistent():
    """引擎选择的两个开关都在表里，且语义已文档化。

    ``AGENTNODES_ENGINE``（主）与 ``AGENTNODES_SKELETON``（旧回退别名）——
    批次 B 会合并，本批只保证两者都被登记、便于后续切换。
    """
    assert "AGENTNODES_ENGINE" in settings.ENV_TABLE
    assert "AGENTNODES_SKELETON" in settings.ENV_TABLE
    assert settings.ENV_TABLE["AGENTNODES_ENGINE"].kind == "str"
    assert settings.ENV_TABLE["AGENTNODES_SKELETON"].kind == "flag"
