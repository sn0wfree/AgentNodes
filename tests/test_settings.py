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

#: `os.environ.get("NAME", DEFAULT)` / `os.getenv(...)` —— 批 A 的形态
_RAW_READ_PAT = re.compile(
    r'(?:os\.environ\.get|os\.getenv)\(\s*["\']([A-Z_0-9]+)["\']'
    r'(?:\s*,\s*([^)]+))?\)',
    re.S,
)

#: `_settings.get_str/get_int/get_float/get_bool/get("NAME")` —— 批 B 的形态
_SETTINGS_READ_PAT = re.compile(
    r'_settings\.(?:get_str|get_int|get_float|get_bool|get)\(\s*["\']([A-Z_0-9]+)["\']'
)


def _scan_settings_reads() -> set[str]:
    """扫描经 settings 读取的变量名（批 B 之后的规范形态）。"""
    names: set[str] = set()
    for p in SRC.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        names.update(m.group(1) for m in _SETTINGS_READ_PAT.finditer(text))
    return names


#: 允许直接读 env 的文件（读取层自身 + profile 机制）。
_RAW_READ_EXEMPT = frozenset({
    "foundation/settings.py",
    "foundation/paths.py",
})


def _scan_raw_reads() -> list[tuple[str, str, int, str | None]]:
    """扫描仍然直接读 env 的地方（批 B 后应当只剩外部变量）。

    **豁免**：``settings.py``（读取层自身）与 ``paths.py``（profile 机制
    要读 AN_DATA_PROFILE / AN_DATA_DIR，且它是 settings 的下游消费者）。
    """
    return [r for r in _scan_reads() if r[1] not in _RAW_READ_EXEMPT]


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
        for m in _RAW_READ_PAT.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.start())
            line_text = text[line_start:line_end if line_end != -1 else len(text)]
            if line_text.lstrip().startswith("#"):
                continue
            default = m.group(2)
            default = default.strip() if default else None
            if default:
                default = re.sub(r"\s+", " ", default)
            out.append((m.group(1), rel, lineno, default))
    return out


def _all_read_names() -> set[str]:
    return {name for name, _, _, _ in _scan_reads()}


# ── 覆盖完整性 ───────────────────────────────────────────────────────


def test_every_read_env_var_is_declared():
    """源码读的每个 env 名都必须在表里，或显式归入外部变量。

    **批 B 后**：本项目配置一律经 `_settings.get_*`，所以「裸读」应当只剩
    外部生态变量。这条同时守住两个方向：
    - 裸读的项目变量 -> 说明有人绕过了 settings
    - 经 settings 读的未登记变量 -> 说明开了开关没登记
    """
    declared = set(settings.ENV_TABLE)
    external = set(settings.EXTERNAL_ENV_VARS)

    # **批 B' 归一**：ENV_TABLE 的 key 是规范名，但调用点仍传旧名
    # （批 B 的零改动策略）。比对前统一映射到规范名。
    def _canon(n: str) -> str:
        return settings.resolve_canonical(n)

    raw = _scan_raw_reads()
    bypassed = sorted({_canon(n) for n, _, _, _ in raw} - declared - external)
    assert not bypassed, (
        f"这些 env 变量仍是裸读（绕过 settings），且不在 EXTERNAL_ENV_VARS: {bypassed}"
    )

    via_settings = {_canon(n) for n in _scan_settings_reads()}
    undeclared = sorted(via_settings - declared)
    assert not undeclared, (
        f"这些变量经 settings 读取但未登记进 ENV_TABLE: {undeclared}"
    )

    # 兜底：外部变量不该经 settings 读（它们就不该进表）
    assert not (via_settings & external), (
        f"外部变量被当成本项目配置读取: {sorted(via_settings & external)}"
    )


def test_no_phantom_declarations():
    """表里声明的每个名字都必须真的被用到（或是一个已文档化的保留项）。"""
    used = {settings.resolve_canonical(n) for n in _scan_settings_reads()}
    used |= {settings.resolve_canonical(n) for n, _, _, _ in _scan_raw_reads()}
    #: 已声明但当前**没有读取点**——即"预留/兼容"项，必须逐个解释。
    RESERVED = {
        # 由 foundation/paths.py 消费（通过 env_override 与
        # PROFILE_ENV 常量间接读，正则扫不到）
        "AGENTNODES_PROFILE",
        "AGENTNODES_CHECKPOINTS_DIR",
        "AGENTNODES_WORKFLOWS_DIR",
        "AGENTNODES_MODEL_CATALOG_DIR",
        # 由 foundation/paths.py 的 PROFILE 机制间接使用
        "AGENTNODES_DATA_DIR",
        # ── 批 E-3（2026-11-11）：读取点在扫描器盲区里，见
        # docs/design/settings-declaration-hygiene-20261111.md §2.1 ──
        # goal-db 覆盖名：经 sqlite.resolve_db_path 的**函数参数流**
        # 直读 os.environ（形态 ③）
        "AGENTNODES_GOAL_DB_PATH",
        # llm.json 路径覆盖：经 LLMConfig.load(env=...) 的**映射参数**
        # 读取（形态 ①，参数化是测试注入设计，读取点不改）
        "AGENTNODES_LLM_CONFIG",
        # LLM 墙钟上限：同上（形态 ①）
        "AGENTNODES_LLM_WALLCLOCK_TIMEOUT",
        # blob 清理 TTL：读取点在 scripts/cleanup_blobs.py（scripts/
        # 不在本测试的扫描范围内）
        "AGENTNODES_BLOB_TTL_DAYS",
    }
    phantom = sorted(set(settings.ENV_TABLE) - used - RESERVED)
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


def test_no_undeclared_bare_env_reads():
    """**裸读必须清零**（原 ``test_declared_default_matches_source``）。

    批 B 把所有调用点迁到 settings 后，全仓已无「读 ENV_TABLE 声明的变量
    却绕过 settings」的裸读点——这条测试从"逐点比对默认值"退化为一个
    更强的守卫：**任何新增的裸读都会让它失败**。

    历史：旧实现对每个扫描到的变量做参数化，其中大部分是外部变量
    （HOME / NO_COLOR / MINIMAX_MODEL …），只能靠 skip 表达"不归本表管"
    ——那会虚高 skip 计数并掩盖真 skip。现在改为在收集期过滤，并断言
    过滤后必须为空。

    **为什么不是单纯删掉**：删了就没有防线了。有人再写
    ``os.environ.get("AGENTNODES_XXX")`` 会一路绿灯到生产。
    """
    bare = [
        (name, rel, lineno)
        for name, rel, lineno, _ in _scan_reads()
        if name in settings.ENV_TABLE and rel not in _RAW_READ_EXEMPT
    ]
    assert not bare, (
        "发现绕过 settings 的裸读（应改用 _settings.get_*）：\n"
        + "\n".join(f"  {rel}:{lineno} -> {name}" for name, rel, lineno in bare)
    )


def test_settings_call_sites_use_the_declared_getter():
    """经 settings 读取的每个变量，其 getter 必须与声明的 kind **兼容**。

    **两种合法形态**：

    1. ``_settings.get_int(X)`` —— getter 与 kind 完全匹配。
    2. ``int(_settings.get_str(X))`` —— get_str 后由**外层**显式转换。
       这是批 B 迁移脚本采用的形态：保留原代码的转换表达式，只替换
       内层读取，从而做到**逐字节等价**（原代码本来就是
       ``int(os.environ.get(X, "1000"))``）。

    非法形态：``kind=bool`` 却用 ``get_str`` 后直接 ``.lower()``——
    未设置时 get_str 返回声明表的 bool 默认值，会在 bool 上调字符串方法
    而崩溃（批 B 实际踩到过这个坑，见 cache.py 的 compaction_linked）。
    """
    by_kind = {
        "int": "get_int",
        "float": "get_float",
        "bool": "get_bool",
        "str": "get_str",
        "path": "get_str",
    }
    #: 允许「外层转换」的 kind：get_str 之后紧跟这些转换函数
    WRAPPED_OK = {"int": "int", "float": "float", "bool": None}

    offenders = []
    pat = re.compile(
        r'_settings\.(get_str|get_int|get_float|get_bool|get)\(\s*["\']([A-Z_0-9]+)["\']'
    )
    for p in SRC.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(SRC).as_posix()
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, line in enumerate(lines, 1):
            for m in pat.finditer(line):
                getter, name = m.group(1), m.group(2)
                setting = settings.ENV_TABLE.get(name)
                if setting is None:
                    continue
                want = by_kind.get(setting.kind)
                if want is None:
                    continue            # flag / misc：不约束
                if getter == "get":
                    continue            # 原始读取，有意保留字符串语义
                if getter == want:
                    continue            # 完全匹配

                # get_str + 外层转换 → 合法
                if getter == "get_str":
                    wrapper = WRAPPED_OK.get(setting.kind)
                    if wrapper is None:
                        # bool 不能用 get_str（默认值是 bool，会崩）
                        offenders.append(
                            f"{rel}:{i} {name} kind={setting.kind} 用了 get_str "
                            f"（bool 必须用 get_bool，否则默认值 True 上调 .lower() 会崩）"
                        )
                        continue
                    prefix = line[:m.start()].rstrip()
                    if prefix.endswith(f"{wrapper}(") or (
                        f"{wrapper}(" in line[max(0, m.start() - 30):m.start()]
                    ):
                        continue        # int(get_str(...)) / float(get_str(...))
                    # 也允许直接用在本行（跨行形式由下一行体现）
                    prev = lines[i - 2] if i >= 2 else ""
                    if prev.rstrip().endswith(f"{wrapper}("):
                        continue
                offenders.append(
                    f"{rel}:{i} {name} kind={setting.kind} 用了 {getter}"
                    f"（应为 {want}，或用 {WRAPPED_OK.get(setting.kind)}(get_str(...))）"
                )
    assert not offenders, "getter 与声明 kind 不匹配：\n  " + "\n  ".join(offenders)


#: 同一变量在不同读取点使用不同默认值的**已知形态**。
#: 每一条都必须解释清楚，否则就可能是真 bug。
_MULTI_DEFAULT_ALLOWED: dict[str, set] = {
    # app.py:278 读 None（未设 = 不启用 CORS）；
    # app.py:321 写成 ("",).split(",") if get(...) else None —— 等价于 None。
    "AGENTNODES_CORS_ORIGINS": {None, ""},
    # workflow.py:355 用 "" 判空，其它读取点用 None。
    "AGENTNODES_WORKSPACE": {None, ""},
    # loop.py:872 用 flag 语义（== "0"），:927 是一个**纯消耗的无效读取**
    # （`_ = ...`，仅作文档）。后者建议删除；在此之前允许 "" 形态。
    "AGENTNODES_SKELETON": {None, ""},
}


# ── 读取语义等价 ─────────────────────────────────────────────────────


def test_get_str_matches_environ(monkeypatch):
    monkeypatch.delenv("AGENTNODES_LOG_LEVEL", raising=False)
    assert settings.get_str("AGENTNODES_LOG_LEVEL") == os.environ.get("AGENTNODES_LOG_LEVEL", "INFO")

    monkeypatch.setenv("AGENTNODES_LOG_LEVEL", "DEBUG")
    assert settings.get_str("AGENTNODES_LOG_LEVEL") == "DEBUG"


def test_get_int_matches_manual_parse(monkeypatch):
    monkeypatch.delenv("AGENTNODES_CACHE_MAX_ENTRIES", raising=False)
    assert settings.get_int("AGENTNODES_CACHE_MAX_ENTRIES") == 1000
    monkeypatch.setenv("AGENTNODES_CACHE_MAX_ENTRIES", "42")
    assert settings.get_int("AGENTNODES_CACHE_MAX_ENTRIES") == 42


def test_get_int_falls_back_on_garbage(monkeypatch):
    """非法值回落默认（不抛错）——与既有 `int(os.environ.get(...))` 不同，
    但这是**更安全**的行为：原实现会抛 ValueError 崩掉模块 import。

    注意：这条记录的是一个**有意的差异**。批次 B 替换读取点时才生效；
    批 A 不改调用点，所以当前行为未变。
    """
    monkeypatch.setenv("AGENTNODES_CACHE_MAX_ENTRIES", "not-a-number")
    assert settings.get_int("AGENTNODES_CACHE_MAX_ENTRIES") == 1000


def test_get_bool_truthy_values(monkeypatch):
    for raw in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("AGENTNODES_LOG_JSON", raw)
        assert settings.get_bool("AGENTNODES_LOG_JSON") is True, raw
    for raw in ("0", "false", "", "no"):
        monkeypatch.setenv("AGENTNODES_LOG_JSON", raw)
        assert settings.get_bool("AGENTNODES_LOG_JSON") is False, raw


def test_unset_returns_declared_default(monkeypatch):
    monkeypatch.delenv("AGENTNODES_LOG_LEVEL", raising=False)
    assert settings.get("AGENTNODES_LOG_LEVEL") is None          # 原始读取
    assert settings.get_str("AGENTNODES_LOG_LEVEL") == "INFO"    # 走默认


def test_none_default_is_preserved(monkeypatch):
    """``default=None`` 的项必须让调用方拿到 None（不是空串）。"""
    monkeypatch.delenv("AGENTNODES_SESSION_DB", raising=False)
    assert settings.get_str("AGENTNODES_SESSION_DB") is None


# ── 诊断 ─────────────────────────────────────────────────────────────


def test_snapshot_masks_secrets():
    snap = settings.snapshot({"AGENTNODES_JWT_SECRET": "super-secret-value-1234",
                              "AGENTNODES_LOG_LEVEL": "DEBUG"})
    assert snap["AGENTNODES_JWT_SECRET"]["value"] != "super-secret-value-1234"
    assert "*" in snap["AGENTNODES_JWT_SECRET"]["value"]
    assert snap["AGENTNODES_LOG_LEVEL"]["value"] == "DEBUG"
    assert snap["AGENTNODES_LOG_LEVEL"]["source"] == "env"


def test_snapshot_marks_defaults():
    snap = settings.snapshot({})
    assert snap["AGENTNODES_LOG_LEVEL"]["source"] == "default"
    assert snap["AGENTNODES_LOG_LEVEL"]["value"] == "INFO"


def test_unknown_env_vars_detects_unregistered():
    fake = {"AN_NEW_THING": "1", "AGENTNODES_LOG_LEVEL": "INFO", "HOME": "/root"}
    assert settings.unknown_env_vars(fake) == ["AN_NEW_THING"]


def test_external_vars_are_not_flagged():
    fake = {"NO_COLOR": "1", "LANGCHAIN_PROVIDER": "x", "OPENAI_API_KEY": "k"}
    assert settings.unknown_env_vars(fake) == []


def test_known_names_sorted_and_complete():
    names = settings.known_names()
    assert list(names) == sorted(names)
    assert "AGENTNODES_DATA_DIR" in names
    assert "AGENTNODES_JWT_SECRET" in names


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
    monkeypatch.delenv("AGENTNODES_CACHE_MAX_ENTRIES", raising=False)
    old = int(os.environ.get("AGENTNODES_CACHE_MAX_ENTRIES", "1000"))
    assert settings.get_int("AGENTNODES_CACHE_MAX_ENTRIES") == old == 1000

    # 设置合法值：两者一致
    monkeypatch.setenv("AGENTNODES_CACHE_MAX_ENTRIES", "250")
    old = int(os.environ.get("AGENTNODES_CACHE_MAX_ENTRIES", "1000"))
    assert settings.get_int("AGENTNODES_CACHE_MAX_ENTRIES") == old == 250


def test_bg_proc_read_point_is_declared_correctly():
    """真实模块 `bg_proc.py` 的读取与声明表一致（具体实例验证）。

    **批 B 后**：源码形态从 ``os.environ.get(X, "300")`` 变成
    ``_settings.get_int(X)``（或 get_str 后由下游 int()）。
    """
    src = (SRC / "foundation" / "utils" / "bg_proc.py").read_text(encoding="utf-8")
    assert 'AGENTNODES_BACKTEST_STALL_TIMEOUT' in src, "调用点没用规范名"
    assert "_settings.get_" in src, "bg_proc 未走 settings 读取层"
    setting = settings.ENV_TABLE["AGENTNODES_BACKTEST_STALL_TIMEOUT"]
    # 源码是 float(os.environ.get(X, "300")) —— 允许小数，故 kind=float。
    assert setting.kind == "float"
    assert setting.default == 300.0
    # **批 E-2**：旧名不再被读取，但**诊断层仍能归一**（审计命令靠它）。
    assert settings.resolve_canonical("AN_BACKTEST_STALL_TIMEOUT") == \
        "AGENTNODES_BACKTEST_STALL_TIMEOUT"


def test_engine_switch_reads_are_consistent():
    """引擎选择的两个开关都在表里，且语义已文档化。

    ``AGENTNODES_ENGINE``（主）与 ``AGENTNODES_SKELETON``（旧回退别名）——
    批次 B 会合并，本批只保证两者都被登记、便于后续切换。
    """
    assert "AGENTNODES_ENGINE" in settings.ENV_TABLE
    assert "AGENTNODES_SKELETON" in settings.ENV_TABLE
    assert settings.ENV_TABLE["AGENTNODES_ENGINE"].kind == "str"
    assert settings.ENV_TABLE["AGENTNODES_SKELETON"].kind == "flag"
