"""进程级配置的唯一声明表（批次 A → B → B' 三步建成）。

## 为什么需要这个模块

`src/agentnodes/` 里曾有 **56 个环境变量、71 个读取点**，分散在 5 种前缀
（`AN_` / `AGENTNODES_` / `QNODES_` / `QUANTNODES_` / `SR_`）下，且**每个模块
各自 `os.environ.get(...)`**。后果：

- 无法集中校验（拼错的环境变量静默失效）
- 无法集中文档化（新人不知道有哪些开关）
- 无法做配置快照（出问题时不知道进程实际用了什么）

## 三步演进

| 批 | 内容 | 结果 |
|---|---|---|
| **A** | 建声明表 + 统一读取 + 快照（**零行为变化**） | 71 个读取点被 56 条声明覆盖 |
| **B** | 62 处调用点改走 `settings.get_*`（**不改名**） | 门禁从 58 违规→0，转为硬门禁 |
| **B'** | 声明表 name 改为 `AGENTNODES_*`，旧名进 `aliases` | 零调用点改动（见下） |

## 命名规范（批次 B' 之后）

- **规范名**：`AGENTNODES_<DOMAIN>_<PARAM>`（domain: paths/engine/cache/
  log/api/orch/cli/compact/llm）
- **旧名**：`AN_*` / `QNODES_*` / `SR_*` / 无前缀的（`JWT_SECRET` /
  `CORS_ORIGINS` / `STATIC_DIR`）—— 全部作为 `aliases` 保留，仍可读、仍生效
- **外部生态名不改**：`OPENAI_API_KEY` / `LLM_API_KEY` / `HOME` / `NO_COLOR` /
  `LANGCHAIN_*` 等（改它们会破坏互操作性）

## 归一链（为什么零调用点改动）

```
调用点传旧名 "AN_LOG_LEVEL"
  ↓ _ALIAS_INDEX 归一
规范名 "AGENTNODES_LOG_LEVEL"
  ↓ 查 os.environ[规范名]        ← 用户设了新名 → 命中
  ↓ 未命中则按 aliases 回退      ← 用户只设了旧名 → 命中 + warn 一次
  ↓ 仍未命中则用声明 default
```

所以**用户设新名或旧名都工作，调用点传新名或旧名也都工作**。
`resolve_canonical()` / `legacy_names()` 供诊断与迁移使用。

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
from typing import Any, Callable, Literal, Mapping

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
    "legacy_names",
    "resolve_canonical",
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
    _s("AGENTNODES_DATA_DIR", "paths", "",
       "数据根目录覆盖（空 = 用 profile 默认）"),
    _s("AGENTNODES_PROFILE", "paths", "",
       "数据命名 profile：agentnodes（默认）/ quantnodes"),
    _s("AGENTNODES_SESSION_DB", "paths", None,
       "会话库绝对路径覆盖"),
    _s("AGENTNODES_WORKSPACE", "paths", None,
       "workspace 根覆盖（旧名：AN_WORKSPACE_PATH / SR_WORKSPACE_PATH）"),
    _s("AGENTNODES_MODEL_CATALOG_DIR", "paths", "",
       "model catalog 缓存目录覆盖"),
    _s("AGENTNODES_CHECKPOINTS_DIR", "paths", "",
       "workflow checkpoint 目录覆盖"),
    _s("AGENTNODES_WORKFLOWS_DIR", "paths", "",
       "用户 workflow 目录覆盖"),
    _s("AGENTNODES_MEMORY_DIR", "paths", None,
       "memory 目录覆盖（None = 用数据目录默认）"),
    _s("AGENTNODES_HYPOTHESES_PATH", "paths", None,
       "hypotheses 文件路径"),
    _s("AGENTNODES_STATIC_DIR", "paths", None,
       "静态资源目录（web UI）"),
    # 权限规则 YAML 路径（rules_io.py）。默认值含 Path.home()，属复杂表达式，
    # 声明表只记录 None（= 由 rules_io 自己拼默认路径）。
    _s("AGENTNODES_PERMISSIONS_PATH", "paths", None,
       "permissions.yaml 路径覆盖"),

    # ── 引擎与 agent（domain: engine）──────────────────────────
    _s("AGENTNODES_ENGINE", "engine", "",
       "执行引擎：skeleton（默认）/ legacy"),
    Setting("AGENTNODES_SKELETON", "flag", None, "engine",
            "旧回退开关：=0 等价 AGENTNODES_ENGINE=legacy"),
    _i("AGENTNODES_TOOL_RESULT_MAX_CHARS", "engine", 30000,
       "工具结果入 LLM history 的截断上限（字符）"),
    _s("AGENTNODES_COMPACT_INCLUDE_MESSAGES", "engine", "",
       "compact.ended 事件是否携带压缩后 messages"),
    _s("AGENTNODES_PERMISSION_MODE", "engine", "",
       "权限模式 L0-L4（空 = 按路径默认）"),
    # 注意：源码是 float(...)，允许小数（如 0.5 秒）；声明必须是 float。
    _f("AGENTNODES_BACKTEST_STALL_TIMEOUT", "engine", 300.0,
       "后台进程停滞判定秒数（源码 float，允许小数）"),

    # ── 缓存（domain: cache）───────────────────────────────────
    _s("AGENTNODES_CACHE_WRITE_POLICY", "cache", "write_through",
       "缓存写策略"),
    _s("AGENTNODES_CACHE_EVICTION", "cache", "lru", "缓存淘汰策略"),
    _i("AGENTNODES_CACHE_MIN_ENTRIES", "cache", 1000, "缓存最小条目数"),
    _i("AGENTNODES_CACHE_MAX_ENTRIES", "cache", 1000, "缓存最大条目数"),
    _i("AGENTNODES_CACHE_AVG_TOKENS_FALLBACK", "cache", 200,
       "token 估算回退值"),
    _i("AGENTNODES_CACHE_WINDOW", "cache", 100, "采样窗口大小"),
    _f("AGENTNODES_CACHE_SAFETY_FACTOR", "cache", 2.0, "安全系数"),
    _i("AGENTNODES_CACHE_MIN_SAMPLES", "cache", 10, "最小样本数"),
    _f("AGENTNODES_CACHE_CHARS_PER_TOKEN", "cache", 3.0, "每 token 字符数估算"),
    # 源码是 float(...)，允许小数（如 0.5 秒）。
    _f("AGENTNODES_CACHE_RE_RESOLVE_SEC", "cache", 60.0, "重新解析间隔（秒）"),
    _f("AGENTNODES_CACHE_TTL", "cache", 60.0, "缓存 TTL（秒）"),
    # 跨行读取（cache.py:110 的 os.environ.get( 与变量名不在同一行）——
    # 批次 B 首次迁移时被逐行正则漏掉，是「扫描器自身盲区」的实例。
    _b("AGENTNODES_CACHE_COMPACTION_LINKED", "cache", True,
       "缓存条目数与压缩联动"),

    # ── 压缩（domain: compact）─────────────────────────────────
    # 紧急开关：模块级 import 期求值，运行期改 env 不生效（已知限制）。
    # kind=bool（不是 flag）：源码是 `.get(X, "").lower() in ("1","true",...)`，
    # 即"值真才开"，不是"存在即开"。
    _b("AGENTNODES_COMPACT_KEEP_ALL", "compact", False,
       "紧急开关：强制保留所有压缩记录在历史里"),

    # ── 门禁盲区补登记（2026-11-09）─────────────────────────────
    # 这些变量原先通过**模块级常量**间接读取（os.environ.get(_CONST)），
    # check_env_access 的正则只认字面量，因此长期逃过登记。用 AST/常量
    # 解析后一次性补上。
    _s("AGENTNODES_ALLOW_SHELL_TOOLS", "engine", "",
       "shell 工具 opt-in（1/true/yes 开启）"),
    _s("AGENTNODES_GOLDEN_HELDOUT_PATH", "paths", "",
       "held-out 评测集目录覆盖"),
    _s("AGENTNODES_LLM_PROFILE", "llm", "",
       "LLM profile 覆盖（llm.json 的 active_profile）"),
    _s("AGENTNODES_LOG_FILE", "log", "",
       "日志文件路径覆盖"),
    _s("AGENTNODES_RSI_STATE_PATH", "paths", "",
       "RSI 状态文件路径覆盖"),
    _s("AGENTNODES_HALT_PATH", "paths", "",
       "halt.json 路径覆盖"),
    _s("AGENTNODES_RSI_ROLE", "orch", "",
       "RSI ticket 签名用的 role"),
    _s("AGENTNODES_RSI_TOOLSET", "orch", "",
       "RSI ticket 签名用的 toolset"),

    # ── 日志与可观测（domain: log）─────────────────────────────
    _s("AGENTNODES_LOG_LEVEL", "log", "INFO", "日志级别"),
    _s("AGENTNODES_LOG_JSON", "log", "", "日志 JSON 格式（1/true 开启）"),
    _i("AGENTNODES_LLM_REQUEST_OFFLOAD_THRESHOLD", "log", 4096,
       "llm_request 大字段外置阈值（字符）"),
    _s("AGENTNODES_EVENT_LOG_READ", "log", "1",
       "是否用 event_log 读路径（=0 关闭）"),

    # ── API / 服务（domain: api）──────────────────────────────
    _s("AGENTNODES_JWT_SECRET", "api", None, "JWT 签名密钥", secret=True),
    # 源码两处读取都是 `os.environ.get("CORS_ORIGINS")`（无默认）：
    # 未设置 → None → "不启用 CORS"。空串与 None 在此等价（都 falsy）。
    _s("AGENTNODES_CORS_ORIGINS", "api", None, "CORS 允许来源（逗号分隔；未设=不启用）"),
    _s("AGENTNODES_ADMIN_TOKEN", "api", "", "管理员 token", secret=True),
    _s("AGENTNODES_ADMIN_PASSWORD", "api", "", "初始管理员密码", secret=True),
    _s("AGENTNODES_API_ALLOW_REGISTRATION", "api", "",
       "是否开放注册（1/true 开启）"),
    _flag("AGENTNODES_API_ALLOW_DEV_SECRET", "api",
          "允许开发环境默认 JWT secret"),
    _i("AGENTNODES_CHAT_ATTEMPT_TIMEOUT", "api", 600,
       "单次 chat 尝试超时（秒）"),
    _s("AGENTNODES_CHAT_WAIT_TIMEOUT", "api", "",
       "chat 等待超时（旧名，秒）"),
    _s("AGENTNODES_ENFORCE_STUDY_IDOR", "api", "",
       "study IDOR 校验（=0 关闭；新名）"),
    _s("AGENTNODES_ENFORCE_STUDY_IDOR_LEGACY", "api", "1",
       "study IDOR 校验（旧名 SR_ENFORCE_STUDY_IDOR，默认 1=开；"
       "与上一个语义不同，不能合并）"),
    _s("AGENTNODES_PLATFORM_STORE", "api", "",
       "PlatformStore 开关（0/false 关闭）"),

    # ── 编排与 goal（domain: orch）────────────────────────────
    _s("AGENTNODES_ORCH_MAX_CONTINUES", "orch", "",
       "编排器最大连续推进次数"),
    _s("AGENTNODES_TEST_CHAT", "orch", None,
       "=1 时走测试 chat 路径"),

    # ── CLI（domain: cli）─────────────────────────────────────
    _s("AGENTNODES_CLI_THEME", "cli", "", "CLI 主题覆盖"),

    # ── LLM 凭据（domain: llm）────────────────────────────────
    # 注意：这里只登记"本项目的读取点"。OPENAI_API_KEY / LLM_API_KEY
    # 是生态通用名（第三方工具也读），批次 B 不改它们的语义。
    _s("OPENAI_API_KEY", "llm", "", "OpenAI API key（生态通用名）",
       secret=True),
    _s("LLM_API_KEY", "llm", "", "通用 LLM API key（生态通用名）",
       secret=True),
)

# ── 旧名 -> 规范名（批次 B'）────────────────────────────────────────
#
# 改名后旧名仍然是**受支持的输入**：ENV_TABLE 以规范名为 key，
# 旧名在 `aliases` 里，`resolve_canonical()` 负责归一。
# 这样 62 处既有调用点（传旧名）与用户环境（可能设旧名）都能工作。
_LEGACY_NAMES: dict[str, tuple[str, ...]] = {
    "AGENTNODES_ALLOW_SHELL_TOOLS": ("AN_ALLOW_SHELL_TOOLS",),
    "AGENTNODES_GOLDEN_HELDOUT_PATH": ("AN_GOLDEN_HELDOUT_PATH",),
    "AGENTNODES_LLM_PROFILE": ("LLM_PROFILE",),
    "AGENTNODES_LOG_FILE": ("AN_LOG_FILE",),
    "AGENTNODES_CHECKPOINTS_DIR": ("AN_CHECKPOINTS_DIR",
                                   "STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR"),
    "AGENTNODES_SESSION_DB": ("AN_SESSIONS_DB", "AGENTNODES_SESSIONS_DB"),
    "AGENTNODES_ADMIN_PASSWORD": ("AN_ADMIN_PASSWORD",),
    "AGENTNODES_ADMIN_TOKEN": ("AN_ADMIN_TOKEN",),
    "AGENTNODES_API_ALLOW_DEV_SECRET": ("AGENTNODES_ALLOW_DEV_SECRET",),
    "AGENTNODES_API_ALLOW_REGISTRATION": ("AN_ALLOW_REGISTRATION",),
    "AGENTNODES_BACKTEST_STALL_TIMEOUT": ("AN_BACKTEST_STALL_TIMEOUT",),
    "AGENTNODES_CACHE_AVG_TOKENS_FALLBACK": ("AN_CACHE_AVG_TOKENS_FALLBACK",),
    "AGENTNODES_CACHE_CHARS_PER_TOKEN": ("AN_CACHE_CHARS_PER_TOKEN",),
    "AGENTNODES_CACHE_COMPACTION_LINKED": ("AN_CACHE_COMPACTION_LINKED",),
    "AGENTNODES_CACHE_EVICTION": ("AN_CACHE_EVICTION",),
    "AGENTNODES_CACHE_MAX_ENTRIES": ("AN_CACHE_MAX_ENTRIES",),
    "AGENTNODES_CACHE_MIN_ENTRIES": ("AN_CACHE_MIN_ENTRIES",),
    "AGENTNODES_CACHE_MIN_SAMPLES": ("AN_CACHE_MIN_SAMPLES",),
    "AGENTNODES_CACHE_RE_RESOLVE_SEC": ("AN_CACHE_RE_RESOLVE_SEC",),
    "AGENTNODES_CACHE_SAFETY_FACTOR": ("AN_CACHE_SAFETY_FACTOR",),
    "AGENTNODES_CACHE_TTL": ("AN_CACHE_TTL",),
    "AGENTNODES_CACHE_WINDOW": ("AN_CACHE_WINDOW",),
    "AGENTNODES_CACHE_WRITE_POLICY": ("AN_CACHE_WRITE_POLICY",),
    "AGENTNODES_CHAT_ATTEMPT_TIMEOUT": ("AN_CHAT_ATTEMPT_TIMEOUT",),
    "AGENTNODES_CHAT_WAIT_TIMEOUT": ("QNODES_CHAT_WAIT_TIMEOUT",),
    "AGENTNODES_CLI_THEME": ("AGENTNODES_THEME",),
    "AGENTNODES_COMPACT_INCLUDE_MESSAGES": ("AN_L4_INCLUDE_MESSAGES",),
    "AGENTNODES_COMPACT_KEEP_ALL": ("AN_KEEP_ALL_COMPACTIONS",),
    "AGENTNODES_CORS_ORIGINS": ("CORS_ORIGINS",),
    "AGENTNODES_DATA_DIR": ("AN_DATA_DIR",),
    "AGENTNODES_ENFORCE_STUDY_IDOR": ("AN_ENFORCE_STUDY_IDOR",),
    "AGENTNODES_ENFORCE_STUDY_IDOR_LEGACY": ("SR_ENFORCE_STUDY_IDOR",),
    "AGENTNODES_EVENT_LOG_READ": ("AN_EVENT_LOG_READ",),
    "AGENTNODES_HYPOTHESES_PATH": ("AN_HYPOTHESES_PATH",),
    "AGENTNODES_JWT_SECRET": ("JWT_SECRET",),
    "AGENTNODES_LLM_REQUEST_OFFLOAD_THRESHOLD": ("AN_LLM_REQUEST_OFFLOAD_THRESHOLD",),
    "AGENTNODES_LOG_JSON": ("AN_LOG_JSON",),
    "AGENTNODES_LOG_LEVEL": ("AN_LOG_LEVEL",),
    "AGENTNODES_MODEL_CATALOG_DIR": ("AN_MODEL_CATALOG_DIR",),
    "AGENTNODES_ORCH_MAX_CONTINUES": ("AN_ORCHESTRATOR_MAX_CONTINUES",),
    "AGENTNODES_PLATFORM_STORE": ("QNODES_PLATFORM_STORE",),
    "AGENTNODES_PROFILE": ("AN_DATA_PROFILE",),
    "AGENTNODES_STATIC_DIR": ("STATIC_DIR",),
    "AGENTNODES_TOOL_RESULT_MAX_CHARS": ("AN_TOOL_RESULT_MAX_CHARS",),
    "AGENTNODES_WORKFLOWS_DIR": ("AN_WORKFLOWS_DIR",),
    "AGENTNODES_WORKSPACE": ("AN_WORKSPACE_PATH", "SR_WORKSPACE_PATH"),
}

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

# ── 旧名注入（批次 B'）───────────────────────────────────────────────
#
# **为什么在这里而不是手写进每条声明**：41 组旧名手写容易漏/拼错，且
# `_LEGACY_NAMES` 本身就是要被测试比对的"单一事实源"。注入后用
# `dataclasses.replace` 生成新实例，frozen 语义不变。
from dataclasses import replace as _dc_replace  # noqa: E402

for _canonical, _olds in _LEGACY_NAMES.items():
    _setting = ENV_TABLE.get(_canonical)
    if _setting is None:
        raise RuntimeError(
            f"_LEGACY_NAMES references undeclared canonical name: {_canonical!r}"
        )
    ENV_TABLE[_canonical] = _dc_replace(
        _setting, aliases=tuple(_setting.aliases) + tuple(_olds)
    )

#: 旧名 -> 规范名 反查索引（读取入口用它归一）。
_ALIAS_INDEX: dict[str, str] = {}
for _canonical, _setting in ENV_TABLE.items():
    for _alias in _setting.aliases:
        if _alias in ENV_TABLE:
            raise RuntimeError(
                f"alias {_alias!r} collides with a canonical declaration — "
                f"rename one of them"
            )
        _ALIAS_INDEX[_alias] = _canonical

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
    """读原始字符串值（解析别名）。

    ``default`` 的语义与 ``os.environ.get(name, default)`` 一致：未设置时
    返回 ``default``（可能是 ``None``，表示"调用方自己处理"）。

    **批次 B**：会先查 canonical 名，再按声明表的 ``aliases`` 回退
    （回退时 warn 一次）。
    """
    raw, _ = _resolve(name)
    return default if raw is None else raw


def is_set(name: str) -> bool:
    raw, _ = _resolve(name)
    return raw is not None


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
    """取声明默认值。

    **必须走别名归一**：批 B' 后 ENV_TABLE 的 key 是规范名，但调用点仍传
    旧名（如 ``AGENTNODES_THEME``）。不归一就查不到声明，默认值退化成
    ``fallback``（通常是 None），导致 ``get_str(X).strip()`` 崩在 None 上。
    """
    setting = ENV_TABLE.get(_ALIAS_INDEX.get(name, name))
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
    ``QUANTNODES_`` / ``SR_``，且不在 ``ENV_TABLE``（含 ``aliases``）
    也不在 ``EXTERNAL_ENV_VARS``。用于发现"新增了开关但忘了登记"。

    **别名也算已知**：改名后旧名仍在用户环境里，它是被支持的输入，
    不应被当成"未登记"。
    """
    env = environ if environ is not None else os.environ
    prefixes = ("AN_", "AGENTNODES_", "QNODES_", "QUANTNODES_", "SR_")
    known = set(ENV_TABLE)
    for s in ENV_TABLE.values():
        known.update(s.aliases)
    out = []
    for name in env:
        if name in known or name in EXTERNAL_ENV_VARS:
            continue
        if name.startswith(prefixes):
            out.append(name)
    return sorted(out)


def _mask(value: Any) -> str:
    s = str(value)
    if len(s) <= 4:
        return "***"
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


# ── 别名解析（批次 B）────────────────────────────────────────────────
#
# **为什么需要**：改名不能一步到位——用户环境里可能还留着旧名。别名让
# 新名（canonical）优先、旧名（aliases）回退，并在回退时 warn 一次。
#
# **为什么 warn 只一次**：这些读取点都在热路径（每次工具调用/每轮循环），
# 逐次 warn 会淹没日志。用模块级集合去重，每个进程每个别名一条。

_warned_aliases: set[str] = set()

_logger = None


def _get_logger():
    global _logger
    if _logger is None:
        import logging

        _logger = logging.getLogger(__name__)
    return _logger


def _warn_alias(alias: str, canonical: str) -> None:
    if alias in _warned_aliases:
        return
    _warned_aliases.add(alias)
    _get_logger().warning(
        "[deprecated] env %s -> %s (old name still honored this release; "
        "update your environment/config)", alias, canonical,
    )


def _resolve(name: str) -> tuple[str | None, str | None]:
    """返回 (原始值, 实际命中的变量名)。

    **批次 B' 的归一链**（调用点传旧名也能工作）：

    1. ``name`` 若为旧名 -> 归一为 canonical（``_ALIAS_INDEX``）
    2. 查 ``os.environ[canonical]``
    3. 再按 ``aliases`` 回退（用户设了旧名）+ warn 一次
    """
    canonical = _ALIAS_INDEX.get(name, name)

    if canonical in os.environ:
        return os.environ[canonical], canonical

    setting = ENV_TABLE.get(canonical)
    if setting is not None:
        for alias in setting.aliases:
            if alias in os.environ:
                _warn_alias(alias, canonical)
                return os.environ[alias], alias
    return None, None


def resolve_canonical(name: str) -> str:
    """把可能是旧名的标识符映射到 canonical 名（用于诊断/迁移）。

    若 ``name`` 是某个条目的 canonical 名，返回自身；若是某条目的 alias，
    返回该条目的 canonical 名；否则原样返回。
    """
    if name in ENV_TABLE:
        return name
    return _ALIAS_INDEX.get(name, name)


def legacy_names() -> dict[str, tuple[str, ...]]:
    """返回 规范名 -> 旧名 的映射（供诊断/文档/测试）。"""
    return {k: tuple(v) for k, v in _LEGACY_NAMES.items()}
    return name


# ── 批 E：旧名审计（只读）────────────────────────────────────
#
# 删兼容层（批 E-2）之前必须知道"这台机器上还有哪些旧名在用"——
# 否则删除是**静默失效**：不报错，行为回落默认。
#
# 风险分档来自**显式清单**（``_LEGACY_RISK``），不靠启发式——这份清单
# 是要指导人改配置的，必须准确（启发式猜风险必然出错）。
#
# 设计见 docs/design/legacy-env-audit-command-20261110.md。

#: 风险档："high" | "medium" | "low"。
LegacyRisk = str

#: 高危旧名——删兼容层后回落默认会导致「写到别处」或「认证失效」。
_LEGACY_RISK_HIGH: frozenset[str] = frozenset({
    # 路径：回落 cwd → agent 可能写到别处
    "AN_WORKSPACE_PATH",
    "SR_WORKSPACE_PATH",
    "AN_SESSIONS_DB",
    "AGENTNODES_SESSIONS_DB",
    "AN_DATA_PROFILE",
    "AN_DATA_DIR",
    "AGENTNODES_MEMORY_DIR",
    "AN_HYPOTHESES_PATH",
    "AN_WORKFLOWS_DIR",
    "AN_CHECKPOINTS_DIR",
    "STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR",
    "AN_PERMISSIONS_PATH",
    "AN_MODEL_CATALOG_DIR",
    "AN_GOLDEN_HELDOUT_PATH",
    "STATIC_DIR",
    # 密钥 / 安全开关：回落默认 → 认证失效或安全降级
    "AN_ADMIN_PASSWORD",
    "AN_ADMIN_TOKEN",
    "JWT_SECRET",
    "CORS_ORIGINS",
    "AN_ALLOW_REGISTRATION",
    "AGENTNODES_ALLOW_DEV_SECRET",
    "AN_ENFORCE_STUDY_IDOR",
    "SR_ENFORCE_STUDY_IDOR",
})

#: 中危旧名——回落默认改变行为但不致命。
_LEGACY_RISK_MEDIUM: frozenset[str] = frozenset({
    "AN_CHAT_ATTEMPT_TIMEOUT",
    "QNODES_CHAT_WAIT_TIMEOUT",
    "AN_LOG_FILE",
    "AN_LOG_JSON",
    "AN_LOG_LEVEL",
    "AN_EVENT_LOG_READ",
    "AN_LLM_REQUEST_OFFLOAD_THRESHOLD",
    "AN_TOOL_RESULT_MAX_CHARS",
    "AN_L4_INCLUDE_MESSAGES",
    "AN_KEEP_ALL_COMPACTIONS",
    "AN_BACKTEST_STALL_TIMEOUT",
    "AN_ORCHESTRATOR_MAX_CONTINUES",
    "QNODES_PLATFORM_STORE",
    "AGENTNODES_THEME",
    "AN_ALLOW_SHELL_TOOLS",
    "AN_CACHE_COMPACTION_LINKED",
})

#: 风险说明（给人看的行动指引）。
_LEGACY_RISK_NOTE: dict[str, str] = {
    "high": "路径/密钥/安全开关——删兼容层后回落默认会导致写到别处或认证失效",
    "medium": "超时/预算/路径——回落默认会改变行为但不致命",
    "low": "缓存/引擎调参——回落默认只是性能或细节差异",
}


def legacy_risk(old_name: str) -> LegacyRisk:
    """旧名的风险档（``high`` / ``medium`` / ``low``）。"""
    if old_name in _LEGACY_RISK_HIGH:
        return "high"
    if old_name in _LEGACY_RISK_MEDIUM:
        return "medium"
    return "low"


@dataclass(frozen=True)
class LegacyUsage:
    """一条"当前环境里仍在用的旧名"记录。"""

    old_name: str
    canonical: str
    value: str
    risk: LegacyRisk

    @property
    def secret(self) -> bool:
        """值是否需要脱敏（密钥类 canonical）。"""
        setting = ENV_TABLE.get(self.canonical)
        return bool(setting and setting.secret)

    def to_dict(self) -> dict[str, Any]:
        return {
            "old_name": self.old_name,
            "canonical": self.canonical,
            "value": self.value,
            "risk": self.risk,
        }


def legacy_usage(
    environ: Mapping[str, str] | None = None,
) -> list[LegacyUsage]:
    """扫出当前环境里**仍在用**的旧名（只读，零副作用）。

    返回按风险档排序（high → medium → low）、同档按旧名字母序。

    **不触发任何 settings 读取**——审计不该产生告警噪声或副作用。
    只看"当前生效"的（旧名设置了才报），不看 shell 配置文件里写过什么。
    """
    env = environ if environ is not None else os.environ
    out: list[LegacyUsage] = []
    for old, canonical in _ALIAS_INDEX.items():
        if old not in env:
            continue
        out.append(LegacyUsage(
            old_name=old,
            canonical=canonical,
            value=env[old],
            risk=legacy_risk(old),
        ))
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda u: (order.get(u.risk, 3), u.old_name))
    return out


def legacy_usage_summary(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """审计摘要（机器可读）：按风险档计数 + 逐条明细。

    **密钥脱敏**：``value`` 对 secret 类 canonical 做掩码——审计输出会
    进 CI 日志，不能泄露。
    """
    usages = legacy_usage(environ)
    counts = {"high": 0, "medium": 0, "low": 0}
    items: list[dict[str, Any]] = []
    for u in usages:
        counts[u.risk] = counts.get(u.risk, 0) + 1
        item = u.to_dict()
        if u.secret:
            item["value"] = _mask(item["value"])
        items.append(item)
    return {
        "total": len(usages),
        "counts": counts,
        "items": items,
        "notes": dict(_LEGACY_RISK_NOTE),
    }


def reset_alias_warnings() -> None:
    """清空 warn 去重集合（测试用）。"""
    _warned_aliases.clear()


def snapshot(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """当前生效配置的快照（脱敏）。

    只包含**已设置**的项 + 声明了默认值的项——用于日志与
    ``--print-config``。密钥（``secret=True``）脱敏。

    **别名**：canonical 名未设但别名设了时，``source`` 记录为
    ``"alias:<旧名>"``，让诊断能看出用户还在用旧名。
    """
    env = environ if environ is not None else os.environ
    out: dict[str, Any] = {}
    for name in sorted(ENV_TABLE):
        setting = ENV_TABLE[name]
        raw = env.get(name)
        source = "env"
        if raw is None:
            for alias in setting.aliases:
                if alias in env:
                    raw = env[alias]
                    source = f"alias:{alias}"
                    break
        if raw is None:
            if setting.default is None:
                continue
            value: Any = setting.default
            source = "default"
        else:
            value = raw
        if setting.secret:
            value = _mask(value)
        out[name] = {"value": value, "source": source, "domain": setting.domain}
    return out
