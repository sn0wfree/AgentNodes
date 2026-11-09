# 参数统一命名方案（2026-11-09）

> 用户要求：「完整修改参数名称」——不做别名兼容层，直接把命名收敛成一套。
> 本文档给出**目标命名规范、全量映射表、执行批次、兼容窗口与回滚方案**。
>
> **三条已拍板的边界（2026-11-09）**：
> 1. **77 个 CLI 参数中，57 个业务参数不动**——只有约 20 个平台配置加 `--an-` 前缀。
> 2. **构造参数（`AgentLoop` 的 38 个等）不进 settings**——它们不是配置，是运行时对象。
>    已由 `LoopSpec` 管理，settings 只管**进程级配置**（env / 文件 / 平台 CLI）。
> 3. 默认数据目录 `.agentnodes`，但保留 `AGENTNODES_PROFILE=quantnodes`
>    兼容模式（供未来 quantnodes 集成）。

---

## §0 现状实测

扫描 `src/agentnodes/`（`.params_scan.py`）：

| 类别 | 数量 | 问题 |
|---|---|---|
| 环境变量 | **56 个 / 71 个读取点** | 5 种前缀并存；同一语义多个名字 |
| CLI 参数 | **77 个** | 无统一前缀；部分与 env 同名不同义 |
| 配置文件键 | `llm.json` 的 `llm` 段 + `compact` 段 | 无版本号、无 schema |

### 5 种前缀的分布

| 前缀 | 数量 | 评价 |
|---|---|---|
| `AN_*` | 29 | 实际最主流，但 `AN` 无人知道是 AgentNodes 缩写 |
| 无前缀 | 17 | `JWT_SECRET` / `CORS_ORIGINS` / `STATIC_DIR` —— 环境污染风险最高 |
| `AGENTNODES_*` | 8 | 完整品牌名，最可读，但太长 |
| `QNODES_*` | 2 | 旧项目残留 |
| `QUANTNODES_*` / `STRATEGY_RESEARCH_*` / `SR_*` | 2+ | 历史兼容 |

### 同一语义多个名字（**真正的隐患**）

| 语义 | 现有名字 | 冲突风险 |
|---|---|---|
| workspace 根 | `AN_WORKSPACE_PATH`(7) / `SR_WORKSPACE_PATH` / `AGENTNODES_WORKSPACE` | 三者设不同值时行为取决于读取顺序 |
| 引擎选择 | `AGENTNODES_ENGINE` / `AGENTNODES_SKELETON` | 两个开关控同一件事，组合值语义未定义 |
| LLM key | `LLM_API_KEY` / `OPENAI_API_KEY` / `<PROVIDER>_API_KEY` | 优先级不明确 |
| memory 目录 | `AGENTNODES_MEMORY_DIR` / `AN_DATA_DIR`（间接） | 局部覆盖 vs 全局 |
| 权限模式 | `AGENTNODES_PERMISSION_MODE`（1 处） | 名字与其它不一致 |

---

## §1 目标命名规范

### 1.1 环境变量：统一 `AGENTNODES_` 前缀

**理由**：
- `AN_` 太短，易与其它工具冲突（`AN_` 不是公认前缀）
- `AGENTNODES_` 是完整品牌，与包名 `agentnodes` 一致，可自解释
- 已有 8 个 `AGENTNODES_*`，说明这条路已经是既成事实
- 与 `quantnodes` 集成时，quantnodes 侧用自己的 `QUANTNODES_` 前缀，不冲突

**结构**：`AGENTNODES_<DOMAIN>_<PARAM>`

| Domain | 含义 | 例子 |
|---|---|---|
| （无 domain） | 顶层核心 | `AGENTNODES_PROFILE`、`AGENTNODES_WORKSPACE` |
| `SESSION` | 会话库 | `AGENTNODES_SESSION_DB` |
| `LLM` | 模型调用 | `AGENTNODES_LLM_API_KEY`、`AGENTNODES_LLM_MODEL` |
| `CACHE` | 缓存 | `AGENTNODES_CACHE_MAX_ENTRIES` |
| `PERMISSION` | 权限 | `AGENTNODES_PERMISSION_MODE` |
| `LOG` | 日志 | `AGENTNODES_LOG_LEVEL`、`AGENTNODES_LOG_JSON` |
| `TOOL` | 工具 | `AGENTNODES_TOOL_RESULT_MAX_CHARS` |
| `ENGINE` | 引擎 | `AGENTNODES_ENGINE` |
| `API` | HTTP 服务 | `AGENTNODES_API_PORT`、`AGENTNODES_API_HOST` |
| `GOAL` | 目标 | `AGENTNODES_GOAL_DB` |
| `ORCH` | 编排 | `AGENTNODES_ORCH_MAX_CONTINUES` |

### 1.2 CLI 参数：只给平台配置加 `--an-` 前缀

现状 77 个参数全是裸的。**只改其中约 20 个平台配置**：

```
agentnodes chat --an-workspace ./ws --an-engine skeleton --strategy momentum
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  ^^^^^^^^^^^^^^^^
                平台配置（加 --an-）                        业务参数（不动）
```

**平台配置（加前缀）候选清单**：
`--workspace`、`--db`、`--sessions-db`、`--goal-db`、`--hypotheses-path`、
`--static-dir`、`--host`、`--port`、`--reload`、`--transport`、
`--llm-config`、`--llm-profile`、`--llm-model`、`--llm-base-url`、
`--llm-temperature`、`--llm-top-p`、`--llm-max-tokens`、`--llm-timeout`、
`--llm-max-retries`、`--llm-seed`、`--llm-stream` / `--llm-no-stream`、
`--data-profile`

**业务参数（**不动**，57 个）**：
`--strategy`、`--symbol`、`--timeframe`、`--metric`、`--method`、`--category`、
`--objective`、`--criterion`、`--criterion-id`、`--evidence`、`--notes`、
`--protocol`、`--result`、`--source`、`--summary`、`--text`、`--type`、
`--recap`、`--confidence`、`--caveat`、`--dataset`、`--only`、`--limit`、
`--json`、`--out`、`--dry-run`、`--list`、`--show`、`--use`、`--add-key`、
`--session-id`、`--task`、`--prompt`、`--model`（业务选择）、`--cron`、
`--interval`、`--hours`、`--recent`、`--tick`、`--max-rounds`、
`--max-iterations`、`--monitor-interval`、`--benchmark` 等

> **这条边界比「统一加前缀」更重要**：`--strategy` / `--symbol` 是量化业务
> 语义，它们不该带平台品牌前缀；而 `--workspace` / `--port` 是平台配置，
> 需要与业务参数区分开。混在一起正是当前 77 个参数难以区分的根因。

### 1.3 配置文件：`llm.json` → `config.yaml`（带 schema 版本）

现状 `~/.agentnodes/llm.json` 只放 LLM；未来的参数无处可放。

```
~/.agentnodes/config.yaml
version: 2
profile: agentnodes
llm:
  provider: deepseek
  model: deepseek-chat
  api_key_env: AGENTNODES_LLM_API_KEY   # 存 env 名，不存明文
paths:
  data_dir: ~/.agentnodes
  session_db: .agentnodes_session.db
engine:
  name: skeleton
  max_iterations: 10
```

**优先级**（统一，全参数一致）：
```
CLI 参数 > 环境变量 > 配置文件 > 内置默认
```

---

## §2 全量映射表（56 个 env）

### 2.1 直接改名（语义不变）

| 旧名 | 新名 | 读取点 |
|---|---|---|
| `AN_DATA_DIR` | `AGENTNODES_DATA_DIR` | 3 |
| `AN_SESSIONS_DB` | `AGENTNODES_SESSION_DB` | 1 |
| `AN_WORKSPACE_PATH` | `AGENTNODES_WORKSPACE` | 7 |
| `AN_LOG_LEVEL` | `AGENTNODES_LOG_LEVEL` | 1 |
| `AN_LOG_JSON` | `AGENTNODES_LOG_JSON` | 2 |
| `AN_TOOL_RESULT_MAX_CHARS` | `AGENTNODES_TOOL_RESULT_MAX_CHARS` | 1 |
| `AN_L4_INCLUDE_MESSAGES` | `AGENTNODES_COMPACT_INCLUDE_MESSAGES` | 1 |
| `AN_MODEL_CATALOG_DIR` | `AGENTNODES_MODEL_CATALOG_DIR` | 1 |
| `AN_CHECKPOINTS_DIR` | `AGENTNODES_CHECKPOINTS_DIR` | 新增 |
| `AN_WORKFLOWS_DIR` | `AGENTNODES_WORKFLOWS_DIR` | 新增 |
| `AN_DATA_PROFILE` | `AGENTNODES_PROFILE` | 新增 |
| `AN_GOAL_DB_PATH` | `AGENTNODES_GOAL_DB` | 3 |
| `AN_HYPOTHESES_PATH` | `AGENTNODES_HYPOTHESES_PATH` | 1 |
| `AN_ADMIN_PASSWORD` | `AGENTNODES_ADMIN_PASSWORD` | 1 |
| `AN_ADMIN_TOKEN` | `AGENTNODES_ADMIN_TOKEN` | 1 |
| `AN_ALLOW_REGISTRATION` | `AGENTNODES_API_ALLOW_REGISTRATION` | 1 |
| `AN_ENFORCE_STUDY_IDOR` | `AGENTNODES_ENFORCE_STUDY_IDOR` | 1 |
| `AN_CHAT_ATTEMPT_TIMEOUT` | `AGENTNODES_CHAT_ATTEMPT_TIMEOUT` | 1 |
| `AN_LLM_REQUEST_OFFLOAD_THRESHOLD` | `AGENTNODES_LLM_REQUEST_OFFLOAD_THRESHOLD` | 1 |
| `AN_EVENT_LOG_READ` | `AGENTNODES_EVENT_LOG_READ` | 1 |
| `AN_ORCHESTRATOR_MAX_CONTINUES` | `AGENTNODES_ORCH_MAX_CONTINUES` | 1 |
| `AN_BACKTEST_STALL_TIMEOUT` | `AGENTNODES_BACKTEST_STALL_TIMEOUT` | 1 |
| `AGENTNODES_ALLOW_DEV_SECRET` | `AGENTNODES_API_ALLOW_DEV_SECRET` | 1 |
| `AGENTNODES_MEMORY_DIR` | `AGENTNODES_MEMORY_DIR`（不变） | 1 |
| `AGENTNODES_THEME` | `AGENTNODES_CLI_THEME` | 1 |
| `AGENTNODES_WORKSPACE` | `AGENTNODES_WORKSPACE`（保留，作为正式名） | 1 |
| `AGENTNODES_TEST_CHAT` | `AGENTNODES_TEST_CHAT`（不变） | 1 |

### 2.2 缓存组（10 个，整体加 domain）

`AN_CACHE_*` → `AGENTNODES_CACHE_*`（10 个）：
`WRITE_POLICY` / `EVICTION` / `MIN_ENTRIES` / `MAX_ENTRIES` /
`AVG_TOKENS_FALLBACK` / `WINDOW` / `SAFETY_FACTOR` / `MIN_SAMPLES` /
`CHARS_PER_TOKEN` / `RE_RESOLVE_SEC` / `TTL`

### 2.3 合并重复语义（**需要行为决策**）

| 现状 | 目标 | 决策 |
|---|---|---|
| `AGENTNODES_ENGINE` + `AGENTNODES_SKELETON` | 只留 `AGENTNODES_ENGINE`（`skeleton`/`legacy`） | `SKELETON=0` 视为 `ENGINE=legacy` 的别名，**只读不写**，一个版本后删 |
| `AN_WORKSPACE_PATH` / `SR_WORKSPACE_PATH` / `AGENTNODES_WORKSPACE` | 只留 `AGENTNODES_WORKSPACE` | 另两个一个版本内作为 fallback，启动时 warn |
| `LLM_API_KEY` / `OPENAI_API_KEY` / `<PROVIDER>_API_KEY` | 只留 `AGENTNODES_LLM_API_KEY` + `<PROVIDER>_API_KEY` | `OPENAI_API_KEY` 保留（业界通用，第三方工具也用）|

### 2.4 无前缀的 17 个：**不改**

`JWT_SECRET` / `CORS_ORIGINS` / `STATIC_DIR` / `HOME` / `COLORFGBG` /
`TERM_PROGRAM` / `NO_COLOR` / `LANGCHAIN_*` / `MINIMAX_*` / `AUTORESEARCH_BEHAVIOR`

**理由**：这些是**外部生态约定的名字**（第三方工具会读 `OPENAI_API_KEY`、
`NO_COLOR`），或操作系统提供（`HOME`、`COLORFGBG`）。改它们会破坏互操作性。

> **这是本方案唯一"不完整"的地方，且是有意的。** 统一命名不等于霸占所有
> 环境变量——只统一「本项目的配置」，不接管「生态的约定」。

### 2.5 历史前缀：删除

`QUANTNODES_RESEARCH_GOAL_DB_PATH` / `STRATEGY_RESEARCH_*` / `SR_*` / `QNODES_*`：
全部并入 `AGENTNODES_*`。但 `quantnodes` profile 下（见
`data-dir-unification-20261109`）保留读取兼容。

---

## §3 执行批次（5 批，每批独立可回滚）

### 批 A · 建立读取层（不改变任何名字）

| 动作 | 产出 |
|---|---|
| 新建 `foundation/settings.py` | 所有 env 的**单一声明表**：`Setting(name, aliases, type, default, domain, doc)` |
| `settings.get("data.dir")` / `settings.snapshot()` | 统一读取 + 脱敏快照 |
| 门禁 `check_env_access.py` | 禁止 `src/` 直接 `os.environ.get("AN_...")`（白名单：settings.py）|

**验证**：现有 56 个名字全部经 settings 读取，行为零变化，全量绿。

### 批 B · 改名（一次性机械替换）

按 §2 映射表替换 71 个读取点。**同时**在 settings 里声明 `aliases=(旧名,)`，
让旧名继续生效（读旧名时 warn 一次）。

**验证**：新增 `test_settings_aliases.py`（28 个改名逐条验证新名生效 + 旧名 warn）。

### 批 C · CLI 参数统一

77 个参数中，识别「平台配置」子集（约 20 个）加 `--an-` 前缀；
业务参数（`--strategy` / `--symbol` / `--metric` …）不动。

**验证**：`agentnodes --help` 快照测试；旧参数保留为 hidden alias 一个版本。

### 批 D · 配置文件 `config.yaml`

`llm.json` → `config.yaml`（带 `version: 2`）。
读取顺序：`config.yaml` → 若不存在则读 `llm.json` 并提示升级。
`agentnodes config migrate` 子命令做转换。

### 批 E · 删除旧名（一个版本后）

删 `aliases`、删 hidden CLI 参数、删 `llm.json` 支持。

---

## §4 兼容窗口与回滚

| 阶段 | 旧名状态 | 回滚 |
|---|---|---|
| 批 A | 完全不变 | 无风险 |
| 批 B–C | 旧名可读 + warn | 单 commit revert |
| 批 D | `llm.json` 仍可读 | 单 commit revert |
| 批 E | 旧名删除 | 需 revert 批 E |

**warn 的实现**：读旧名时 `logger.warning` 一次（每个进程每个名字一次），
消息里给出新名与迁移命令：
```
[deprecated] AN_WORKSPACE_PATH → AGENTNODES_WORKSPACE (still honored this release)
```

---

## §5 明确不做

- **不改外部生态约定的 env**（`OPENAI_API_KEY` / `NO_COLOR` / `HOME` / `LANGCHAIN_*`）
- **不改 57 个业务 CLI 参数**（`--strategy` / `--symbol` / `--timeframe` / `--metric` …）
  —— 已确认；它们不是平台配置，加品牌前缀反而增加噪音
- **不把构造参数并进 settings** —— 已确认。`AgentLoop.__init__` 的 38 个参数
  是运行时对象（registry / memory / hooks / strategy / injectors），不是配置值；
  它们由 `LoopSpec` 分层管理（见 `batch12`）。settings 的边界是
  **进程级、可序列化、可从 env/文件/CLI 来的配置**
- **不引入第三方配置库**（pydantic-settings 等）——需求简单，手写表 + 类型转换足够

### §5.1 边界判定标准（写给后来者）

一个参数该不该进 settings，用这三条判断：

| 判据 | 进 settings | 不进 |
|---|---|---|
| 是否可从 env / 文件 / CLI 设置？ | 是 | 否（如 `registry` 实例）|
| 是否可序列化为 JSON/YAML？ | 是 | 否（如 `asyncio.Queue`、`threading.Event`）|
| 进程生命周期内是否基本不变？ | 是 | 否（如 `_approval_response`、`_budget_tokens`）|

三条全「是」→ 进 settings；任一「否」→ 留在构造参数 / `LoopSpec` / 实例状态。

---

## §6 风险

| 风险 | 缓解 |
|---|---|
| 用户脚本硬编码了 `AN_*` | 兼容窗口 + warn；`--print-config` 显示实际生效值 |
| 改名遗漏 → 某处静默失效 | 门禁禁止直接 `os.environ.get`，必须经 settings |
| `AGENTNODES_ENGINE` 与 `SKELETON` 合并语义错 | 单独测试三组合（engine 只设 / skeleton 只设 / 都设冲突）|
| quantnodes 集成后前缀冲突 | 用 profile 区分：quantnodes 侧只读 `QUANTNODES_*`，互不干扰 |

---

## §7 工作量估计

| 批 | 文件改动 | 测试 | 时间 |
|---|---|---|---|
| A | settings.py + 1 门禁 | 别名/快照 15 个 | 0.5 天 |
| B | 71 个读取点 + 28 条别名 | 28 个 | 0.5 天 |
| C | ~20 个 CLI 参数（**57 个业务参数不动**） | help 快照 + 别名 | 0.3 天 |
| D | config.yaml + migrate 子命令 | 转换 + 优先级 | 0.5 天 |
| E | 删兼容 | — | 0.2 天 |
| **合计** | | | **2.0 天** |

---

## §8 与既有工作的关系

本方案**不是孤立的新工作**，它接在三件已完成的事之后：

| 已完成 | 关系 |
|---|---|
| `foundation/paths.py`（数据目录统一） | settings 的**第一个 domain**（`AGENTNODES_DATA_DIR` / `PROFILE` 等）；批 A 直接复用它的 `active_profile` |
| `LoopSpec`（批次 12 S2） | **反向边界**：`LoopSpec` 管构造参数，settings 管进程配置，两者不重叠（§5.1 的判据）|
| `check_path_literals`（新增品牌目录规则）| settings 门禁（`check_env_access`）的**同型实现**，可直接参照 |

**顺序建议**：先完成 V1（已 done）→ V2/V3（真实 LLM 验证）→ 再做本方案批 A。
理由：settings 会改 71 个读取点，在 V2 还没验证完引擎可靠性时动它，
会让"真实任务失败"的归因变复杂（是引擎问题还是配置接错了？）。
若希望并行，可只做批 A（建读取层、不改名），它零行为变化。
