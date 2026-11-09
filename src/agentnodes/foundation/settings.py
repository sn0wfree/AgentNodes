"""进程级配置的唯一声明表（批次 A：只声明 + 统一读取，不改名）。

## 为什么需要这个模块

`src/agentnodes/` 里有 **56 个环境变量、71 个读取点**，分散在 5 种前缀
（`AN_` / `AGENTNODES_` / `QNODES_` / `QUANTNODES_` / `SR_`）下，且**每个模块
各自 `os.environ.get(...)`**。后果：

- 无法集中校验（拼错的环境变量静默失效）
- 无法集中文档化（新人不知道有哪些开关）
- 无法做配置快照（出问题时不知道进程实际用了什么）

## 本批（A）的边界：**零行为变化**

只做三件事：

1. **声明表**：`SETTINGS` 把 71 个读取点归纳成带类型/默认值/域名的条目
2. **统一读取**：`settings.get(name, default=...)` 走声明表 → `os.environ`
3. **快照**：`settings.snapshot()` 输出脱敏后的当前生效值（供日志/诊断）

**不改任何变量名**（改名是批次 B），**不强制改造既有读取点**（那需要逐点
验证默认值等价，属于批次 B）。

## 与 `foundation/paths.py` 的分工

- `paths.py`：**路径计算**（profile、数据目录、会话库文件名）
- `settings.py`：**值读取**（env → 类型 → 默认值 → 校验）
  路径类设置（如 `AGENTNODES_DATA_DIR`）两者都涉及：settings 负责读取，
  paths 负责把它拼成实际路径。

## 与构造参数的边界

`AgentLoop.__init__` 的 38 个参数**不在**本表——它们是运行时对象
（registry / memory / hooks / asyncio.Queue 等），不可序列化、不可从 env 来。
判据见 `docs/design/param-naming-unification-20261109.md` §5.1：

1. 可从 env / 文件 / CLI 设置？
2. 可序列化为 JSON/YAML？
3. 进程生命周期内基本不变？

三条全「是」才进本表。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

__all__ = [
    "ENV_TABLE",
    "Setting",
    "get",
    "get_bool",
    "get_float",
    "get_int",
    "get_str",
    "is_set",
    "known_names",
    "snapshot",
    "unknown_env_vars",
]

Kind = Literal["str", "int", "float", "bool", "path", "flag"]


@dataclass(frozen=True)
class Setting:
    """一个环境变量声明的不可变描述。

    Attributes:
        name: 环境变量名（**当前名字**，批次 B 才改名）。
        kind: 解析类型。``flag`` = 存在即为真（不看值）。
        default: 默认值。``None`` 表示"未设置时不提供默认值"——
            调用方自己处理 ``None``（这与既有代码的 ``os.environ.get(NAME)``
            语义一致，必须保留）。
        domain: 语义域（用于分组展示与未来 CLI 前缀映射）。
        doc: 一句话说明。
        aliases: 兼容别名（批次 B 用于旧名回退；本批留空或只记录现状）。
        secret: 是否敏感（``snapshot()`` 中脱敏）。
    """

    name: str
    kind: Kind
    default: Any = None
    domain: str = "misc"
    doc: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)
    secret: bool = False


def _s(name: str, domain: str, default: Any = None, doc: str = "",
       *, secret: bool = False) -> Setting:
    return Setting(name, "str", default, domain, doc, secret=secret)


def _i(name: str, domain: str, default: int | None = None, doc: str = "") -> Setting:
    return Setting(name, "int", default, domain, doc)


def _f(name: str, domain: str, default: float | None = None, doc: str = "") -> Setting:
    return Setting(name, "float", default, domain, doc)


def _b(name: str, domain: str, default: bool = False, doc: str = "") -> Setting:
    return Setting(name, "bool", default, domain, doc)


def _flag(name: str, domain: str, doc: str = "") -> Setting:
    """存在即为真（``os.environ.get(NAME)`` 的真值语义）。"""
    return Setting(name, "flag", None, domain, doc)


# ── 声明表（71 个读取点的归纳，与现状逐条对齐）──────────────────────
#
# **顺序即文档**：按 domain 分组，每组的 doc 说明这一组管什么。
# 每条的 default 必须与既有代码的 `os.environ.get(NAME, DEFAULT)` 逐字一致，
# 否则「零行为变化」不成立（tests/test_settings.py 逐条比对）。

_DECLARED: tuple[Setting, ...] = (
    # ── 路径与数据（domain: paths）─────────────────────────────
    # 与 foundation/paths.py 配合：settings 读取值，paths 拼路径。
    _s("AN_DATA_DIR", "paths", "",
       "数据根目录覆盖（空 = 用 profile 默认）"),
    _s("AN_DATA_PROFILE", "paths", "",
       "数据命名 profile：agentnodes（默认）/ quantnodes"),
    _s("AN_SESSIONS_DB", "paths", None,
       "会话库绝对路径覆盖"),
    _s("AN_WORKSPACE_PATH", "paths", None,
       "workspace 根覆盖"),
    _s("AGENTNODES_WORKSPACE", "paths", None,
       "workspace 根覆盖（CLI show 用；与 AN_WORKSPACE_PATH 同义）"),
    _s("AN_MODEL_CATALOG_DIR", "paths", "",
       "model catalog 缓存目录覆盖"),
    _s("AN_CHECKPOINTS_DIR", "paths", "",
       "workflow checkpoint 目录覆盖"),
    _s("AN_WORKFLOWS_DIR", "paths", "",
       "用户 workflow 目录覆盖"),
    _s("AGENTNODES_MEMORY_DIR", "paths", None,
       "memory 目录覆盖（None = 用数据目录默认）"),
    _s("AN_HYPOTHESES_PATH", "paths", None,
       "hypotheses 文件路径"),
    _s("STATIC_DIR", "paths", None,
       "静态资源目录（web UI）"),

    # ── 引擎与 agent（domain: engine）──────────────────────────
    _s("AGENTNODES_ENGINE", "engine", "",
       "执行引擎：skeleton（默认）/ legacy"),
    Setting("AGENTNODES_SKELETON", "flag", None, "engine",
            "旧回退开关：=0 等价 AGENTNODES_ENGINE=legacy"),
    _i("AN_TOOL_RESULT_MAX_CHARS", "engine", 30000,
       "工具结果入 LLM history 的截断上限（字符）"),
    _s("AN_L4_INCLUDE_MESSAGES", "engine", "",
       "compact.ended 事件是否携带压缩后 messages"),
    _s("AGENTNODES_PERMISSION_MODE", "engine", "",
       "权限模式 L0-L4（空 = 按路径默认）"),
    _i("AN_BACKTEST_STALL_TIMEOUT", "engine", 300,
       "后台进程停滞判定秒数"),

    # ── 缓存（domain: cache）───────────────────────────────────
    _s("AN_CACHE_WRITE_POLICY", "cache", "write_through",
       "缓存写策略"),
    _s("AN_CACHE_EVICTION", "cache", "lru", "缓存淘汰策略"),
    _i("AN_CACHE_MIN_ENTRIES", "cache", 1000, "缓存最小条目数"),
    _i("AN_CACHE_MAX_ENTRIES", "cache", 1000, "缓存最大条目数"),
    _i("AN_CACHE_AVG_TOKENS_FALLBACK", "cache", 200,
       "token 估算回退值"),
    _i("AN_CACHE_WINDOW", "cache", 100, "采样窗口大小"),
    _f("AN_CACHE_SAFETY_FACTOR", "cache", 2.0, "安全系数"),
    _i("AN_CACHE_MIN_SAMPLES", "cache", 10, "最小样本数"),
    _f("AN_CACHE_CHARS_PER_TOKEN", "cache", 3.0, "每 token 字符数估算"),
    _i("AN_CACHE_RE_RESOLVE_SEC", "cache", 60, "重新解析间隔（秒）"),
    _f("AN_CACHE_TTL", "cache", 60.0, "缓存 TTL（秒）"),

    # ── 日志与可观测（domain: log）─────────────────────────────
    _s("AN_LOG_LEVEL", "log", "INFO", "日志级别"),
    _s("AN_LOG_JSON", "log", "", "日志 JSON 格式（1/true 开启）"),
    _i("AN_LLM_REQUEST_OFFLOAD_THRESHOLD", "log", 4096,
       "llm_request 大字段外置阈值（字符）"),
    _s("AN_EVENT_LOG_READ", "log", "1",
       "是否用 event_log 读路径（=0 关闭）"),

    # ── API / 服务（domain: api）──────────────────────────────
    _s("JWT_SECRET", "api", None, "JWT 签名密钥", secret=True),
    # 源码两处读取都是 `os.environ.get("CORS_ORIGINS")`（无默认）：
    # 未设置 → None → "不启用 CORS"。空串与 None 在此等价（都 falsy）。
    _s("CORS_ORIGINS", "api", None, "CORS 允许来源（逗号分隔；未设=不启用）"),
    _s("AN_ADMIN_TOKEN", "api", "", "管理员 token", secret=True),
    _s("AN_ADMIN_PASSWORD", "api", "", "初始管理员密码", secret=True),
    _s("AN_ALLOW_REGISTRATION", "api", "",
       "是否开放注册（1/true 开启）"),
    _flag("AGENTNODES_ALLOW_DEV_SECRET", "api",
          "允许开发环境默认 JWT secret"),
    _i("AN_CHAT_ATTEMPT_TIMEOUT", "api", 600,
       "单次 chat 尝试超时（秒）"),
    _s("QNODES_CHAT_WAIT_TIMEOUT", "api", "",
       "chat 等待超时（旧名，秒）"),
    _s("AN_ENFORCE_STUDY_IDOR", "api", "",
       "study IDOR 校验（=0 关闭）"),
    _s("SR_ENFORCE_STUDY_IDOR", "api", "1",
       "study IDOR 校验（旧名）"),
    _s("SR_WORKSPACE_PATH", "api", "",
       "workspace 根（旧名）"),
    _s("QNODES_PLATFORM_STORE", "api", "",
       "PlatformStore 开关（0/false 关闭）"),

    # ── 编排与 goal（domain: orch）────────────────────────────
    _s("AN_ORCHESTRATOR_MAX_CONTINUES", "orch", "",
       "编排器最大连续推进次数"),
    _s("AGENTNODES_TEST_CHAT", "orch", None,
       "=1 时走测试 chat 路径"),

    # ── CLI（domain: cli）─────────────────────────────────────
    _s("AGENTNODES_THEME", "cli", "", "CLI 主题覆盖"),

    # ── LLM 凭据（domain: llm）────────────────────────────────
    # 注意：这里只登记"本项目的读取点"。OPENAI_API_KEY / LLM_API_KEY
    # 是生态通用名（第三方工具也读），批次 B 不改它们的语义。
    _s("OPENAI_API_KEY", "llm", "", "OpenAI API key（生态通用名）",
       secret=True),
    _s("LLM_API_KEY", "llm", "", "通用 LLM API key（生态通用名）",
       secret=True),
)

#: **重复登记的显式防线**：dict comprehension 会让后一条静默覆盖前一条
#: （本模块初版就把 `AGENTNODES_TEST_CHAT` 登记了两次，domain 悄悄变了）。
#: 这里在 import 期就炸，不留给运行时。
_dupes = sorted(
    {s.name for s in _DECLARED if [x.name for x in _DECLARED].count(s.name) > 1}
)
if _dupes:
    raise RuntimeError(
        f"duplicate env declarations in settings.ENV_TABLE: {_dupes} "
        f"(a later entry would silently override the earlier one)"
    )

ENV_TABLE: dict[str, Setting] = {s.name: s for s in _DECLARED}

#: 外部生态 / 操作系统提供的变量——**不纳入统一管理**。
#: 它们不属于本项目的配置，改名会破坏互操作性。
EXTERNAL_ENV_VARS: frozenset[str] = frozenset({
    "HOME", "COLORFGBG", "TERM_PROGRAM", "NO_COLOR",
    "LANGCHAIN_MODEL_NAME", "LANGCHAIN_PROVIDER",
    "MINIMAX_MODEL", "MINIMAX_PROVIDER", "MINIMAX_API_HOST",
    "AUTORESEARCH_BEHAVIOR",
})


# ── 读取 ─────────────────────────────────────────────────────────────


def get(name: str, default: Any = None) -> str | None:
    """读原始字符串值。

    ``default`` 的语义与 ``os.environ.get(name, default)`` 一致：未设置时
    返回 ``default``（可能是 ``None``，表示"调用方自己处理"）。
    """
    return os.environ.get(name, default)


def is_set(name: str) -> bool:
    return name in os.environ


def _coerce(setting: Setting, raw: str) -> Any:
    kind = setting.kind
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "bool":
        return raw.strip().lower() in ("1", "true", "yes", "y", "on")
    if kind == "flag":
        return True
    return raw


def _default_of(name: str, fallback: Any = None) -> Any:
    setting = ENV_TABLE.get(name)
    if setting is None:
        return fallback
    return setting.default if setting.default is not None else fallback


def get_str(name: str, default: Any = None) -> str | None:
    return get(name, _default_of(name, default))


def get_int(name: str, default: int | None = None) -> int | None:
    raw = get(name)
    if raw is None or str(raw).strip() == "":
        return _default_of(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _default_of(name, default)


def get_float(name: str, default: float | None = None) -> float | None:
    raw = get(name)
    if raw is None or str(raw).strip() == "":
        return _default_of(name, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _default_of(name, default)


def get_bool(name: str, default: bool = False) -> bool:
    raw = get(name)
    if raw is None or str(raw).strip() == "":
        base = _default_of(name, default)
        return bool(base) if not isinstance(base, str) else base.strip().lower() in (
            "1", "true", "yes", "y", "on",
        )
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


# ── 诊断 ─────────────────────────────────────────────────────────────


def known_names() -> tuple[str, ...]:
    return tuple(sorted(ENV_TABLE))


def unknown_env_vars(environ: dict[str, str] | None = None) -> list[str]:
    """返回进程环境里"看起来像本项目、但没在声明表里"的变量。

    判据：前缀是 ``AN_`` / ``AGENTNODES_`` / ``QNODES_`` /
    ``QUANTNODES_`` / ``SR_``，且不在 ``ENV_TABLE`` 也不在
    ``EXTERNAL_ENV_VARS``。用于发现"新增了开关但忘了登记"。
    """
    env = environ if environ is not None else os.environ
    prefixes = ("AN_", "AGENTNODES_", "QNODES_", "QUANTNODES_", "SR_")
    out = []
    for name in env:
        if name in ENV_TABLE or name in EXTERNAL_ENV_VARS:
            continue
        if name.startswith(prefixes):
            out.append(name)
    return sorted(out)


def _mask(value: Any) -> str:
    s = str(value)
    if len(s) <= 4:
        return "***"
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


def snapshot(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """当前生效配置的快照（脱敏）。

    只包含**已设置**的项 + 声明了默认值的项——用于日志与
    ``--print-config``。密钥（``secret=True``）脱敏。
    """
    env = environ if environ is not None else os.environ
    out: dict[str, Any] = {}
    for name in sorted(ENV_TABLE):
        setting = ENV_TABLE[name]
        raw = env.get(name)
        if raw is None:
            if setting.default is None:
                continue
            value: Any = setting.default
            source = "default"
        else:
            value = raw
            source = "env"
        if setting.secret:
            value = _mask(value)
        out[name] = {"value": value, "source": source, "domain": setting.domain}
    return out
