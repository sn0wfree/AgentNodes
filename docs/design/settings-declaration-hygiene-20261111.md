# 批 E-3 · settings 声明表卫生收尾（扫描器盲区清零）

> **本文档先行**。上游：`legacy-env-audit-command-20261110.md` §8（批 E-2）。
> 批 E-2 验收复查时发现：`AN_AGENT_WALLCLOCK_TIMEOUT` 被**生产代码**读取却
> 不在 `ENV_TABLE`——而两道门禁（`check_env_access` /
> `test_settings.test_every_read_env_var_is_declared`）都是绿的。
> 本批回答两个问题：**它为什么逃过了门禁**，以及**同类还有几个**。

---

## §0 结论

| 发现 | 数字 |
|---|---|
| 生产代码里"读了但没声明"的项目 env 名 | **4** |
| 从未入册的"仍然生效"历史名（goal-db 私有 alias 层） | **3** |
| 扫描器逃逸形态 | **3 种**（全部实测定位） |
| 本批后 ENV_TABLE | 58 → **62** |
| 本批后旧名诊断清单 | 46 组/49 个 → **49 组/54 个** |
| `LEGACY_LIVE_ENVS`（仍生效历史名） | 4 → **7** |

一句话：**门禁扫的是"读取的写法"，不是"读取的事实"**——只要写法换了
皮（常量、参数、映射），事实就从网眼里漏过去了。本批补齐声明层，
并给"写法盲区"留下明确的登记位（`RESERVED` + 测试开关例外清单），
让下一轮普查有处可查。

---

## §1 盲区形态学（实测）

门禁的两个扫描器都认**字面形态**：

- `check_env_access.py`：`os.environ.get("NAME")` / `os.getenv("NAME")` 带项目前缀
- `test_settings._scan_raw_reads`：同上；`_scan_settings_reads`：`_settings.get_*("NAME")`

实测逃过两道网的三种写法：

### 形态 ①：映射参数化读取（`env.get("NAME")`）

```python
env_map = dict(env if env is not None else os.environ)   # llm/config.py:231
...
wc_raw = env.get("AN_AGENT_WALLCLOCK_TIMEOUT", "").strip()  # :584
```

`env` 是函数参数（`LLMConfig.load(env=...)` 的测试注入点），默认就是
`os.environ`——**事实**上是生产读取；**写法**上既不是 `os.environ.get`
也不是 `_settings.get_*`，两个扫描器都看不见。

本形态实测命中 **19 处未声明**（另有 7 处已声明的合法使用）。

### 形态 ②：常量间接直读（`os.environ.get(_CONST)`）

批 E-2 之前抓过一次（`logging_setup.LOG_DIR_ENV` 等 13 个变量），
AST 化后清零。本批复扫：**src 内 1 处**，且已声明
（`paths.py:105` `PROFILE_ENV`，属豁免文件）——形态 ② 在 src 内已死。

### 形态 ③：函数参数流（常量 → 传参 → `os.environ.get(param)`）

```python
# apps/scheduled_research/store.py:27,38
_DB_PATH_ENV = "AGENTNODES_GOAL_DB_PATH"
return resolve_db_path("goals.db", _DB_PATH_ENV)

# foundation/storage/sqlite.py:193-194（被调方）
for name in candidates:
    raw_path = os.environ.get(name, "").strip()   # name 是参数，扫描器看不见
```

goal-db 子系统 4 个模块（`scheduled_research/store` /
`observability/hanging_events` / `kernel/orchestration/ticket_store` /
`foundation/storage/sqlite`）全走这条链，读取的事实是
`AGENTNODES_GOAL_DB_PATH`——**不在 ENV_TABLE**。

顺带发现：`sqlite.py` 里还有一个 **settings 之外的私有 alias 层**
（`GOAL_DB_LEGACY_ENVS`，3 个历史名仍被兜底读取）——它从未进过批 B' 的
改名清单，也从不在批 E 的审计视野里。

---

## §2 逐名处置

### §2.1 声明进表（4 个新 canonical）

| 名字 | domain / kind / default | 理由 |
|---|---|---|
| `AGENTNODES_GOAL_DB_PATH` | paths / path / None | 被 4 个模块经形态 ③ 读取；goals.db 唯一覆盖名（A5/F10） |
| `AGENTNODES_LLM_CONFIG` | llm / path / None | llm.json 路径覆盖（形态 ①，`load(env=...)` 参数化读取，**读取点不改**——参数化是测试注入设计） |
| `AGENTNODES_LLM_WALLCLOCK_TIMEOUT` | llm / float / 1800.0 | 原 `AN_AGENT_WALLCLOCK_TIMEOUT`（见 §2.2） |
| `AGENTNODES_BLOB_TTL_DAYS` | storage / int / 365 | blob 清理 TTL（财务合规保留期）；原 `AN_BLOB_TTL_DAYS`，读取点在 `scripts/cleanup_blobs.py` |

### §2.2 改名（2 个，非规范前缀）

| 旧名 | 新名 | 旧名处置 |
|---|---|---|
| `AN_AGENT_WALLCLOCK_TIMEOUT` | `AGENTNODES_LLM_WALLCLOCK_TIMEOUT` | 进 `_LEGACY_NAMES`（**已失效**档——读取点同步改新名） |
| `AN_BLOB_TTL_DAYS` | `AGENTNODES_BLOB_TTL_DAYS` | 同上 |

两个都不是批 B' 清单里的名字（从未入册），但都写在文档/注释里
（REFERENCE.md、blob_schema.py）——改名 + 入册让审计命令能报出
"你设的旧名已失效"。

### §2.3 goal-db 三个历史名入册（**仍生效**档）

`AN_GOAL_DB_PATH` / `QUANTNODES_RESEARCH_GOAL_DB_PATH` /
`STRATEGY_RESEARCH_GOAL_DB`：

- canonical = `AGENTNODES_GOAL_DB_PATH`；
- 进 `_LEGACY_NAMES` **且** 进 `LEGACY_LIVE_ENVS`——它们由
  `sqlite.resolve_db_path` / `goal_db_env_value` 继续兜底读取，
  属"数据位置连续性"（删了会让人找不到自己的 goals.db），
  与 E-2 的 4 个仍生效历史名同一类；
- `sqlite.GOAL_DB_LEGACY_ENVS` **保留原样**——它是读取机制，
  入册是诊断登记，两层职责不同。

### §2.4 显式例外（不进表，留名册）

**测试基础设施开关**（6 个）：`QNODES_LIVE_LLM`（7 个测试文件 +
skip 消息文案）、`AN_E2E_REAL_LLM`、`AN_RUN_BROWSER_TESTS`
（3 个测试文件 + **`.github/workflows/e2e.yml:93` CI 在用**）、
`AN_DOTENV_CWD_ONLY` / `AN_DOTENV_QUANTNODES` / `AN_DOTENV_BOTH`
（dotenv 行为测试的临时变量）。

不进表的理由：它们不是部署配置，是**开发/CI 工作流契约**——
改名需要同步 CI 与所有开发者的肌肉记忆，且改错方向是"CI 里的
live 测试静默 skip"（正是本仓库最忌讳的静默失效）。本批把它们
登记进 `settings.TEST_INFRA_ENV_VARS` 并让 `unknown_env_vars`
豁免——**名册上承认它们，声明表外保留它们**。改名（若做）留待
单独微批，与 CI 同步落地。

**脚本局部名**（1 个）：`AGENTNODES_WORKSPACE_ROOT`
（`scripts/migrate_study_graph.py` 的可选回退根）——生产代码
零读取点，是脚本自洽的参数别名，不动。

---

## §3 实施清单

| # | 文件 | 动作 |
|---|---|---|
| 1 | `foundation/settings.py` | 4 条新声明；`_LEGACY_NAMES` +3 组；`LEGACY_LIVE_ENVS` 4→7；新增 `TEST_INFRA_ENV_VARS`；`unknown_env_vars` 豁免；docstring 数字更新 |
| 2 | `foundation/llm/config.py` | wallclock 读取点/注释改名；`AGENTNODES_LLM_CONFIG` 注释补"已声明" |
| 3 | `foundation/storage/blob_schema.py` | 注释里的 TTL 名更新 |
| 4 | `scripts/cleanup_blobs.py` | env 名改名（读 + warning 文案 + help + docstring） |
| 5 | `tests/test_legacy_env_audit.py` | 清单计数 46/49 → 49/54 |
| 6 | `tests/test_settings.py` | `RESERVED` 补 4 条（扫描器看不见读取点的声明） |
| 7 | `docs/REFERENCE.md` | wallclock 旧行删除；`AGENTNODES_LLM_CONFIG` / `AGENTNODES_LLM_WALLCLOCK_TIMEOUT` / `AGENTNODES_BLOB_TTL_DAYS` 补行 |
| 8 | `scripts/cleanup_blobs.py` / `migrate_event_log_p0_1_a4.py` / `migrate_to_unified_db.py` / `restore_compacted_sessions.py` | **实施时发现**：4 个脚本的 `from agentnodes.core.agent.memory_manager import ...` 是死路径（`agentnodes/core` 随分层重组删除，ModuleNotFoundError）——改指单一事实源 `foundation.storage.session_paths`。与 §5.2 的"脚本无门禁"同类，顺带修 |
| 9 | `tests/test_llm_wallclock.py` | **全量回归抓到的漏网**：`LLMConfig.load(env={"AN_AGENT_WALLCLOCK_TIMEOUT": "42"})`——测试把旧名作为**映射字面量的 key** 注入（形态 ① 的测试侧），本批的 `.get(` 扫描看不见这种形态。改用新名；这确认了 §5.2"人工普查之外还需门禁"的判断 |
| 10 | 本文档 | 即设计 |

> **为什么 `RESERVED` 要补**：`test_no_phantom_declarations` 断言
> "声明的名字必须有可见读取点"。这 4 个名字的读取点都在盲区形态里
> （参数流 / 脚本目录），扫描器看不见——`RESERVED` 是"声明存在、
> 读取点不可见"的**登记处**，不是豁免垃圾桶。

---

## §4 验证

| 项 | 断言 |
|---|---|
| 全量回归 | 0 failed |
| batch_gate | ALL GREEN |
| 审计命令真机 | `config audit-legacy` 对 goal-db 3 旧名报"仍生效"，对 wallclock/blob 旧名报"已失效" |
| 声明完整性 | `unknown_env_vars` 不再报 4 个新 canonical 与 6 个测试开关 |
| 快照 | `snapshot()` 含 4 个新条目 |

## §5 未做（记录，不埋雷）

1. **测试开关改名**（`QNODES_LIVE_LLM` → `AGENTNODES_LIVE_LLM` 等）：
   需 CI（e2e.yml）与文档同步，单独微批。
2. **scripts/ 不在门禁扫描范围**：`cleanup_blobs.py` 等脚本的 env 读取
   无门禁。本批靠人工普查覆盖；把 `scripts/` 纳入 `check_env_access`
   属门禁增强，另立批次。
3. **`AGENTNODES_WORKSPACE_ROOT`**：脚本局部名，生产零消费者，不动。

---

## §6 顺带事件：tool-refs 门禁的"环境性缺失"（已修）

提交前最后一次 batch_gate 在 **step 6（tool refs）** 意外 FAIL：
`TOOLSETS['general']` 的 `read_document` 被判"未实现"。与 E-2/E-3 的改动
**无关**——根因在探测环境：

- `ReadDocumentTool.check_available()` 要求 `import fitz`（PyMuPDF）；
- 本机**应用程序控制策略**在当晚拦截了 3.12 探测环境里 pymupdf 的原生
  DLL（`ImportError: DLL load failed: 应用程序控制策略已阻止此文件`）——
  与 `batch_gate.cmd` 头注里记录的 **hypothesis DLL 被拦是同一类宿主
  策略行为**（今天早些时候同一环境同一门禁还是绿的，策略是异步生效的）；
- 3.13（PYTEST_PI）不受影响，`read_document` 照常注册。

**修法**（`check_tool_refs.py`）：新增 `env_gated_tools()`——从源码
**导入真实工具类**，凡声明了 `check_available()` 且在当前探测环境返回
False 的，记为"环境缺失"：

- **判定侧**：三个配置面（TOOLSETS / plugins / templates）的 missing
  都先扣除 env-gated 名——类若真从源码消失，不在集合里，照旧判违规，
  **真回归不会被掩盖**；
- **显示侧**：这类缺失标 `〔环境缺失: N〕`，不再显示成"悬空"红 X；
- selftest 注入参数时跳过环境探测（`injected` 标记），假集合不受
  真实环境干扰。

**教训**：门禁用"运行时 registry"当事实源，就继承了运行时环境的
依赖波动。把"实现存在但环境 gate 掉"从"未实现"里分出来，门禁才
既能在干净的机器上拦真悬空，也能在被宿主策略干扰的机器上不误报。
