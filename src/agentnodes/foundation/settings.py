"""进程级配置的唯一声明表（批次 A → B → B' 三步建成）。

## 为什么需要这个模块

`src/agentnodes/` 里曾有 **56 个环境变量、71 个读取点**，分散在 5 种前缀
（`AN_` / `AGENTNODES_` / `QNODES_` / `QUANTNODES_` / `SR_`）下，且**每个模块
各自 `os.environ.get(...)`**。后果：

- 无法集中校验（拼错的环境变量静默失效）
- 无法集中文档化（新人不知道有哪些开关）
- 无法做配置快照（出问题时不知道进程实际用了什么）

## 四步演进

| 批 | 内容 | 结果 |
|---|---|---|
| **A** | 建声明表 + 统一读取 + 快照（**零行为变化**） | 71 个读取点被 56 条声明覆盖 |
| **B** | 62 处调用点改走 `settings.get_*`（**不改名**） | 门禁从 58 违规→0，转为硬门禁 |
| **B'** | 声明表 name 改为 `AGENTNODES_*`，旧名进 `aliases` | 零调用点改动（见下） |
| **E-2** | **删掉 `aliases` 兼容层**（旧名不再被读取） | 读取层只认 canonical；旧名降级为诊断清单 |

## 命名规范（批次 E-2 之后）

- **规范名**：`AGENTNODES_<DOMAIN>_<PARAM>`（domain: paths/engine/cache/
  log/api/orch/cli/compact/llm）
- **旧名**：`AN_*` / `QNODES_*` / `SR_*` / 无前缀的（`JWT_SECRET` /
  `CORS_ORIGINS` / `STATIC_DIR`）—— **不再被读取**（批 E-2 删掉了兼容层）
- **外部生态名不改**：`OPENAI_API_KEY` / `LLM_API_KEY` / `HOME` / `NO_COLOR` /
  `LANGCHAIN_*` 等（改它们会破坏互操作性）

## 读取链（批 E-2 之后）

```
调用点传规范名 "AGENTNODES_LOG_LEVEL"
  ↓ 查 os.environ[规范名]        ← 用户设了规范名 → 命中
  ↓ 未命中则用声明 default
```

**旧名不再有回退分支**。删兼容层最怕的是「静默失效」——不报错，只是行为
回落默认。所以读取层加了一道**显式告警**：读某个规范名时若发现环境里还留着
它的旧名，就 warn 一次并指出该改成什么（见 :func:`_resolve`）。

旧名本身作为**纯诊断数据**留在本模块（`_LEGACY_NAMES` / `_LEGACY_CATALOG`），
供 `agentnodes config audit-legacy` 报告「你还在用旧名 X，请改成 Y」。
**诊断层认旧名，读取层不认**——这是批 E-2 的核心分界。

`resolve_canonical()` / `legacy_names()` / `legacy_usage()` 是诊断入口。

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
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

__all__ = [
    "ENV_TABLE",
    "LEGACY_LIVE_ENVS",
    "TEST_INFRA_ENV_VARS",
    "Setting",
    "get",
    "get_bool",
    "get_float",
    "get_int",
    "get_legacy",
    "get_str",
    "is_set",
    "known_names",
    "legacy_names",
    "legacy_risk",
    "legacy_usage",
    "legacy_usage_summary",
    "reset_warnings",
    "resolve_canonical",
    "snapshot",
    "unknown_env_vars",
]

Kind = Literal["str", "int", "float", "bool", "path", "flag"]


@dataclass(frozen=True)
class Setting:
    """一个环境变量声明的不可变描述。

    Attributes:
        name: 环境变量名（**规范名**）。
        kind: 解析类型。``flag`` = 存在即为真（不看值）。
        default: 默认值。``None`` 表示"未设置时不提供默认值"——
            调用方自己处理 ``None``（这与既有代码的 ``os.environ.get(NAME)``
            语义一致，必须保留）。
        domain: 语义域（用于分组展示与未来 CLI 前缀映射）。
        doc: 一句话说明。
        secret: 是否敏感（``snapshot()`` 中脱敏）。

    **批 E-2 起没有 ``aliases`` 字段**：旧名兼容层已删除，声明表只描述
    规范名。旧名清单在模块级 :data:`_LEGACY_NAMES`（诊断用），不进本表。
    """

    name: str
    kind: Kind
    default: Any = None
    domain: str = "misc"
    doc: str = ""
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
    # goals.db 唯一覆盖名（A5/F10 统一）。读取点在
    # foundation/storage/sqlite.py 的 resolve_db_path / goal_db_env_value
    # ——经函数参数流（形态 ③）直读 os.environ，扫描器看不见，故这里
    # 声明 + RESERVED 登记（tests/test_settings.py）。3 个历史名仍被
    # 兜底读取（见 LEGACY_LIVE_ENVS）。
    _s("AGENTNODES_GOAL_DB_PATH", "paths", None,
       "goals.db（goal 台账）路径覆盖"),

    # ── 存储保留（domain: storage）─────────────────────────────
    _i("AGENTNODES_BLOB_TTL_DAYS", "storage", 365,
       "blob 清理 TTL（天，财务合规保留期）；消费者 scripts/cleanup_blobs.py"),

    # ── 引擎与 agent（domain: engine）──────────────────────────
    # U6-2（2026-11-14）：AGENTNODES_ENGINE / AGENTNODES_SKELETON 已随
    # legacy 循环体退役——skeleton 是唯一引擎，env 不再注册（设了无效）。
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
    # llm.json 路径覆盖。读取点在 llm/config.py 的 _resolve_bridge_path
    # ——经 load(env=...) 的映射参数（形态 ①）读取，扫描器看不见，
    # RESERVED 登记。参数化是测试注入设计，读取点不改。
    _s("AGENTNODES_LLM_CONFIG", "llm", None,
       "llm.json 配置路径覆盖"),
    # 单次 LLM 请求的墙钟上限（秒）。原 AN_AGENT_WALLCLOCK_TIMEOUT
    # （批 E-3 改名，旧名已失效）；读取点在 llm/config.py 的
    # _env_to_overrides（映射参数，形态 ①），RESERVED 登记。
    _f("AGENTNODES_LLM_WALLCLOCK_TIMEOUT", "llm", 1800.0,
       "单次 LLM 请求墙钟上限（秒，0/None 不禁用时默认 1800）"),
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
    # 源码两处读取都是 `os.environ.get("AGENTNODES_CORS_ORIGINS")`（无默认）：
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

# ── 旧名清单（批 E-2：诊断层，不是读取层）──────────────────────────
#
# 批 B' 改名时把 49 个旧名（AN_* / QNODES_* / SR_* / 无前缀的几个）挂进
# `aliases`，让旧名继续生效。批 E-2 把兼容层删掉了——**但旧名不能从代码里
# 消失**，因为审计命令 `agentnodes config audit-legacy` 要靠它告诉用户
# 「你还在用旧名 X，请改成 Y」。
#
# 批 E-3 又补录 3 组（goal-db 3 个历史名 + wallclock/blob 两个改名），
# 现在 **49 组 / 54 个旧名**。
#
# 所以本表从「读取层的别名」**降级为「诊断层的清单」**：
#
# | 层 | 认旧名吗 | 谁在用 |
# |---|---|---|
# | 读取层（`ENV_TABLE` / `_resolve`）| **不认** | 全部配置读取 |
# | 诊断层（本表 / `_LEGACY_CATALOG`）| 认 | `audit-legacy` 命令、`resolve_canonical()` |
#
# **前提**（批 E-1 的 `agentnodes config audit-legacy`）已确认：本机 0 个
# 旧名在用。
#
# 删之前先做的两件事（否则是静默失效）：
# 1. **51 处调用点全部改成 canonical**——旧实现大量直接读旧名
#    （`_settings.get_str("AN_WORKSPACE_PATH")`），alias 一删就回落默认；
# 2. **审计命令落地**——给"删前必须改名"提供可执行的检查；
# 3. **读取层加 deprecation 告警**（见 `_resolve`）——万一还有旧名残留，
#    读的时候明确喊出来，而不是静默回落默认。
_LEGACY_NAMES: dict[str, tuple[str, ...]] = {
    "AGENTNODES_ALLOW_SHELL_TOOLS": ("AN_ALLOW_SHELL_TOOLS",),
    "AGENTNODES_GOLDEN_HELDOUT_PATH": ("AN_GOLDEN_HELDOUT_PATH",),
    "AGENTNODES_LLM_PROFILE": ("LLM_PROFILE",),
    "AGENTNODES_LOG_FILE": ("AN_LOG_FILE",),
    # ── 批 E-3 补录（2026-11-11）─────────────────────────────
    # goal-db 的 3 个历史名：sqlite.GOAL_DB_LEGACY_ENVS 仍在兜底读取
    # （数据位置连续性），但从未进过批 B' 清单/本表——审计命令看不见
    # 它们。入册 + LEGACY_LIVE_ENVS。
    "AGENTNODES_GOAL_DB_PATH": (
        "AN_GOAL_DB_PATH",
        "QUANTNODES_RESEARCH_GOAL_DB_PATH",
        "STRATEGY_RESEARCH_GOAL_DB",
    ),
    # 两个非规范前缀的生产开关（批 E-3 改名，旧名已失效）：
    "AGENTNODES_LLM_WALLCLOCK_TIMEOUT": ("AN_AGENT_WALLCLOCK_TIMEOUT",),
    "AGENTNODES_BLOB_TTL_DAYS": ("AN_BLOB_TTL_DAYS",),
    "AGENTNODES_CHECKPOINTS_DIR": (
        "AN_CHECKPOINTS_DIR", "STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR",
    ),
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
    "AGENTNODES_LLM_REQUEST_OFFLOAD_THRESHOLD": (
        "AN_LLM_REQUEST_OFFLOAD_THRESHOLD",
    ),
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

#: 旧名 -> 规范名（由 :data:`_LEGACY_NAMES` 反转派生）。
#:
#: **诊断层专用**：读取层不查这张表（批 E-2 删掉了 alias 回退）。
_LEGACY_CATALOG: dict[str, str] = {
    old: canonical
    for canonical, olds in _LEGACY_NAMES.items()
    for old in olds
}

#: 规范名 -> 旧名（读取层用它判断"环境里是不是还留着旧名"→ 告警）。
_LEGACY_BY_CANONICAL: dict[str, tuple[str, ...]] = {
    canonical: olds for canonical, olds in _LEGACY_NAMES.items()
}

#: **仍然生效**的历史名（数据位置连续性，不是"配置改名兼容"）。
#:
#: 这几个不是"改名兼容"——它们指向的是**用户数据在哪**。删掉不会让程序
#: 报错，只会让用户"找不到自己的数据"，所以它们由各自的模块**显式直读**
#: （`paths.active_profile` / `workflow._definition_workspace` /
#: `goal._session_owned` / `checkpoint_store.CheckpointStore` /
#: `storage.sqlite.resolve_db_path` 的 goal-db 兜底链），
#: 不走本模块的 alias 机制。
#:
#: 审计命令会把它们标成"仍生效（已废弃）"而不是"已失效"。
LEGACY_LIVE_ENVS: dict[str, str] = {
    "AN_DATA_PROFILE": "AGENTNODES_PROFILE",
    "SR_WORKSPACE_PATH": "AGENTNODES_WORKSPACE",
    "SR_ENFORCE_STUDY_IDOR": "AGENTNODES_ENFORCE_STUDY_IDOR_LEGACY",
    "STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR": "AGENTNODES_CHECKPOINTS_DIR",
    # ── 批 E-3 补录：goal-db 的 3 个历史名 ──────────────────────
    # 读取机制在 sqlite.GOAL_DB_LEGACY_ENVS（不经 get_legacy()），
    # 这里登记的是**诊断分类**：审计命令据它报"仍生效"。
    "AN_GOAL_DB_PATH": "AGENTNODES_GOAL_DB_PATH",
    "QUANTNODES_RESEARCH_GOAL_DB_PATH": "AGENTNODES_GOAL_DB_PATH",
    "STRATEGY_RESEARCH_GOAL_DB": "AGENTNODES_GOAL_DB_PATH",
}

#: **测试基础设施开关**（不进 ENV_TABLE，但也不算"未登记"）。
#:
#: 这些是开发/CI 工作流契约，不是部署配置：
#: - `QNODES_LIVE_LLM` — live-LLM 测试 opt-in（7 个测试文件 + skip 文案）
#: - `AN_E2E_REAL_LLM` — Playwright 真 LLM E2E opt-in
#: - `AN_RUN_BROWSER_TESTS` — 浏览器 E2E opt-in（**.github/workflows/e2e.yml
#:   在用**——改名必须连 CI 一起改，单独微批）
#: - `AN_DOTENV_*` 三个 — dotenv 行为测试写入临时 .env 的变量名
#:
#: `unknown_env_vars()` 豁免它们：名册上承认，声明表外保留。
TEST_INFRA_ENV_VARS: frozenset[str] = frozenset({
    "QNODES_LIVE_LLM", "AN_E2E_REAL_LLM", "AN_RUN_BROWSER_TESTS",
    "AN_DOTENV_CWD_ONLY", "AN_DOTENV_QUANTNODES", "AN_DOTENV_BOTH",
})

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

#: **旧名不得与规范名撞车**（批 E-2 的显式防线）。
#:
#: 旧名一旦变成规范名，读取层就会认它——那等于把兼容层偷偷加回来，
#: 而且没有任何告警。这里在 import 期就炸。
_collisions = sorted(set(_LEGACY_CATALOG) & set(ENV_TABLE))
if _collisions:
    raise RuntimeError(
        f"legacy env names collide with canonical declarations: {_collisions} "
        f"(a legacy name must never become a canonical name)"
    )

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

    **批 E-2**：只读规范名。旧名不再回退——环境里若还留着旧名，
    :func:`_resolve` 会 warn 一次并指出该改成什么。
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

    **批 E-2**：名字必须是规范名。传旧名会查不到声明 → 回落 ``fallback``
    （通常是 None）——这正是删兼容层最怕的「静默回落默认」。
    所以这里对未登记的名字打一次告警（见 :func:`_warn_unknown`）。
    """
    setting = ENV_TABLE.get(name)
    if setting is None:
        _warn_unknown(name)
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

    **批 E-2**：旧名**不再算已知**——它们已经不被读取了，还留在环境里
    就是"设了但不生效"，正是审计命令要报出来的东西。
    """
    env = environ if environ is not None else os.environ
    prefixes = ("AN_", "AGENTNODES_", "QNODES_", "QUANTNODES_", "SR_")
    known = set(ENV_TABLE)
    out = []
    for name in env:
        if name in known or name in EXTERNAL_ENV_VARS \
                or name in TEST_INFRA_ENV_VARS:
            continue
        if name.startswith(prefixes):
            out.append(name)
    return sorted(out)


def _mask(value: Any) -> str:
    s = str(value)
    if len(s) <= 4:
        return "***"
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


# ── 告警（批 E-2）─────────────────────────────────────────────────────
#
# 删兼容层最怕的是**静默失效**：不报错，只是行为回落默认。所以读取层
# 在两个地方喊出来：
#
# 1. **环境里还留着旧名** → 读对应规范名时 warn，指名该改成什么；
# 2. **调用点传了未登记的名字** → warn（多半是把旧名改漏了）。
#
# **为什么 warn 只一次**：这些读取点都在热路径（每次工具调用/每轮循环），
# 逐次 warn 会淹没日志。用模块级集合去重，每个进程每条一条。

_warned_legacy: set[str] = set()
_warned_unknown: set[str] = set()

_logger = None


def _get_logger():
    global _logger
    if _logger is None:
        import logging

        _logger = logging.getLogger(__name__)
    return _logger


def _warn_legacy(old: str, canonical: str) -> None:
    """环境里还留着旧名 → 明确告警（不静默回落默认）。"""
    if old in _warned_legacy:
        return
    _warned_legacy.add(old)
    if old in LEGACY_LIVE_ENVS:
        _get_logger().warning(
            "[deprecated] env %s is still honored but deprecated — "
            "switch to %s", old, canonical,
        )
    else:
        _get_logger().warning(
            "[deprecated] env %s is no longer read (it was renamed to %s) — "
            "this value is IGNORED, update your environment/config",
            old, canonical,
        )


def _warn_unknown(name: str) -> None:
    """调用点传了未登记的名字 → 告警（多半是漏改的旧名）。"""
    if name in _warned_unknown:
        return
    _warned_unknown.add(name)
    hint = ""
    legacy = _LEGACY_CATALOG.get(name)
    if legacy:
        hint = f" (did you mean {legacy}?)"
    _get_logger().warning(
        "[settings] env %s is not declared in ENV_TABLE%s — "
        "the lookup will fall back to the default",
        name, hint,
    )


def _resolve(name: str) -> tuple[str | None, str | None]:
    """返回 (原始值, 实际命中的变量名)。

    **批 E-2 之后只认规范名**：

    1. 查 ``os.environ[name]`` → 命中即返回；
    2. 未命中：如果环境里还留着它的**旧名**，warn 一次（明确告诉用户
       旧名已失效/已废弃），但**不**回退到旧值——兼容层已删；
    3. 仍未命中 → 回落声明默认值（由 :func:`get_str` 等处理）。
    """
    if name in os.environ:
        return os.environ[name], name

    for old in _LEGACY_BY_CANONICAL.get(name, ()):
        if old in os.environ:
            _warn_legacy(old, name)
            break
    return None, None


def get_legacy(name: str, default: Any = None) -> str | None:
    """读一个**仍然生效**的历史名（数据位置连续性）。

    只有 :data:`LEGACY_LIVE_ENVS` 里的名字能走这个入口——它们指向的是
    "**用户的数据在哪**"。删掉不会让程序报错，只会让用户找不到自己的
    数据，所以由各自的模块**显式**直读它们。

    与读取层的关系：读取层只认规范名；这里是**唯一的例外入口**，
    且必须显式指明旧名（传别的名字直接抛错）——免得兼容层被无声加回来。

    读到值时会 warn 一次（已废弃，但仍生效）。
    """
    if name not in LEGACY_LIVE_ENVS:
        raise KeyError(
            f"{name!r} is not a still-honored legacy env name; "
            f"only {sorted(LEGACY_LIVE_ENVS)} may be read this way"
        )
    raw = os.environ.get(name)
    if raw is not None:
        _warn_legacy(name, LEGACY_LIVE_ENVS[name])
    return default if raw is None else raw


def resolve_canonical(name: str) -> str:
    """把可能是旧名的标识符映射到 canonical 名（用于诊断/迁移）。

    若 ``name`` 是某个条目的 canonical 名，返回自身；若是某个旧名，
    返回它的 canonical 名；否则原样返回。

    **批 E-2**：这是**诊断入口**——读取层不再用它归一。
    """
    if name in ENV_TABLE:
        return name
    return _LEGACY_CATALOG.get(name, name)


def legacy_names() -> dict[str, tuple[str, ...]]:
    """返回 规范名 -> 旧名 的映射（供诊断/文档/测试）。"""
    return {k: tuple(v) for k, v in _LEGACY_NAMES.items()}


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
    # ── 批 E-3 补录：goal-db 路径（回落默认 → goals.db 找不到）──
    "AN_GOAL_DB_PATH",
    "QUANTNODES_RESEARCH_GOAL_DB_PATH",
    "STRATEGY_RESEARCH_GOAL_DB",
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
    # ── 批 E-3 补录 ──────────────────────────────────────────
    "AN_BLOB_TTL_DAYS",   # 回落 365 天默认 → 清理行为变化但不致命
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
    #: 该旧名**是否仍然生效**（``LEGACY_LIVE_ENVS`` 的那几个）。
    #:
    #: - ``False`` → 兼容层已删，**这个值被忽略**（设了不生效）；
    #: - ``True``  → 仍生效（数据位置连续性），但已废弃。
    honored: bool = False

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
            "honored": self.honored,
        }


def legacy_usage(
    environ: Mapping[str, str] | None = None,
) -> list[LegacyUsage]:
    """扫出当前环境里**仍在用**的旧名（只读，零副作用）。

    返回按风险档排序（high → medium → low）、同档按旧名字母序。

    **不触发任何 settings 读取**——审计不该产生告警噪声或副作用。
    只看"当前生效"的（旧名设置了才报），不看 shell 配置文件里写过什么。

    **批 E-2 后**：清单来源是诊断层的 :data:`_LEGACY_CATALOG`
    （不再是读取层的 alias 索引）——兼容层删了，审计照样能跑。
    """
    env = environ if environ is not None else os.environ
    out: list[LegacyUsage] = []
    for old, canonical in _LEGACY_CATALOG.items():
        if old not in env:
            continue
        out.append(LegacyUsage(
            old_name=old,
            canonical=canonical,
            value=env[old],
            risk=legacy_risk(old),
            honored=old in LEGACY_LIVE_ENVS,
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
    ignored = 0
    for u in usages:
        counts[u.risk] = counts.get(u.risk, 0) + 1
        item = u.to_dict()
        if u.secret:
            item["value"] = _mask(item["value"])
        if not u.honored:
            ignored += 1
        items.append(item)
    return {
        "total": len(usages),
        "counts": counts,
        #: **仍然生效**的旧名数（数据位置连续性，只是已废弃）。
        "honored": sum(1 for u in usages if u.honored),
        #: **已经失效**的旧名数——这些值被静默忽略，必须改名。
        "ignored": ignored,
        "items": items,
        "notes": dict(_LEGACY_RISK_NOTE),
    }


def reset_warnings() -> None:
    """清空告警去重集合（测试用）。"""
    _warned_legacy.clear()
    _warned_unknown.clear()


def snapshot(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """当前生效配置的快照（脱敏）。

    只包含**已设置**的项 + 声明了默认值的项——用于日志与
    ``--print-config``。密钥（``secret=True``）脱敏。

    **批 E-2**：``source`` 只有 ``env`` / ``default`` 两种——旧名不再
    被读取，所以不存在 ``"alias:<旧名>"`` 这一档了。环境里若还留着
    旧名，用 `agentnodes config audit-legacy` 查。
    """
    env = environ if environ is not None else os.environ
    out: dict[str, Any] = {}
    for name in sorted(ENV_TABLE):
        setting = ENV_TABLE[name]
        raw = env.get(name)
        source = "env"
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
