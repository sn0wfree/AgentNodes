# 日志统一入口设计（P0–P2）

日期：2026-11-02　分支：`refactor/v6-gateway`　状态：已确认（用户四项决策落地）

前置讨论见 `docs/study-goal-agent-events-design-20261102.md` §2.5 与本轮决策表。
本文是实施契约：所有改动以本文件为准，门禁为验收标准。

---

## 1. 背景与问题

| # | 问题 | 证据 |
|---|------|------|
| 1 | 日志装配散点：4 处 `basicConfig` + `setup_trace_logging` 各自为政，格式/流/级别不一致 | `api/app.py:44`、`api/session/backfill_event_log.py:326`、`cli/__main__.py:167`、`core/mcp/server.py:882` |
| 2 | `SR_LOG_LEVEL` 重复解析 3 处 | `api/app.py:42`、`cli/__main__.py:170`、`core/mcp/server.py:879` |
| 3 | 默认文本格式不含 trace 字段——trace↔log 的 join key（trace_id/session_id/study_id/round_num）只在 JSON 模式可见 | `trace.py` 仅 `JsonFormatter` 输出四字段；default basicConfig 格式无 |
| 4 | `_dlog` 双写（`logger.info` + `print`）重复定义 4 处，web/MCP 下双份输出（logger→stderr + print→stdout） | `study/{runner,scheduler,store,monitor}.py` 各一份 `def _dlog` |
| 5 | `hanging_events` 表无 trace_id/round_num 列——卡死事件无法与 trace/日志按轮次关联 | `hanging_events.py:41-54` schema、`:99-123` record() |
| 6 | 库内 print 与 CLI/UI 输出混杂，无门禁约束新增 | 全包 556 处 print（见 §5 分类） |

## 2. 用户决策（2026-11-02 确认）

1. **文件日志**：选 (b) —— `SR_LOG_FILE` 开关 + RotatingFileHandler，默认关闭。
2. **`_dlog` 双写**：保留 `logger.info + print` 双写能力，**借鉴 CodersWheel `logger.py` 的双写方案**（借鉴分析见 §3）。
3. **print 治理**：选 (a) 全量治理，先做 subprocess-feed 分类再批量替换。
4. **hanging_events 加 trace_id/round_num 列**：做，并入 P0.5。

## 3. CodersWheel `logger.py` 借鉴分析

来源：`sn0wfree/CodersWheel` → `CodersWheel/QuickTool/logger.py`（97 行）。

它的"双写"实现：**一个 logger 挂两个 handler**（`FileHandler` 写文件 + `StreamHandler(sys.stdout)` 写控制台，同一 formatter），调用方只调 `logger.info(...)` 一次，输出自动到达两个目的地。外加 Singleton 防重复挂载、`deco` 计时装饰器、`sql()/status()` 语义前缀。

**关键启示：双写的正确层级是 handler 层，不是 call-site 层。**

| CodersWheel 要素 | 本项目映射 | 采纳 |
|---|---|---|
| FileHandler + StreamHandler 同挂一 logger | `configure_logging` 一次装配 console+file 两个 handler | ✅ P0+P1 |
| console 指向 `sys.stdout` | 子进程 surface 的 console handler 指向 stdout → 输出进 run.log，喂 log_stall 检测 | ✅ 策略表按 surface 定向 |
| Singleton 防重复挂载 | 模块级 `_CONFIGURED` 幂等守卫 + 旧 handler 移除重挂（比 Singleton 更强：支持测试重复调用） | ✅ P0 |
| `self.info = logger.info` 便捷入口 | `dual_log()` 共享双写 helper（收敛 4 处 `_dlog`） | ✅ P2 |
| `deco` 计时装饰器 | 暂不需要（hooks 体系已有计时） | ❌ |
| `sql()/status()` 前缀 | 结构化字段已由事件/trace 体系承担 | ❌ |
| （未处理）trace 字段 | TraceFilter + 默认格式带四 trace 字段 | ✅ P0.5 |
| （未处理）轮转 | `RotatingFileHandler(maxBytes=10MB, backupCount=5)` | ✅ P1 |
| （未处理）无 handler 时静默 | `dual_log` 在「root 无 handler」时保留 print 兜底——这正是 `_dlog` print 的原始用途 | ✅ P2 |

**`_dlog` print 的真实语义澄清**：它不服务于 log_stall（那是 engine 子进程的 `print(flush=True)` → `run_bg` 重定向进 run.log）；它是「CLI 默认模式不装配 handler 时的可见性兜底」。收编后语义不变、缺陷修复：有 handler 时不再双份输出，无 handler 时 print 兜底保留。

## 4. 设计

### 4.1 新模块 `core/observability/logging_setup.py`

```python
DEFAULT_TEXT_FORMAT = (
    "%(asctime)s %(levelname)-8s %(name)s "
    "[t=%(trace_id)s sid=%(session_id)s st=%(study_id)s r=%(round_num)s] %(message)s"
)

def configure_logging(
    surface: str,                        # "web" | "cli" | "mcp" | "script"
    *,
    level: int | str | None = None,      # None → SR_LOG_LEVEL → INFO
    json_output: bool | None = None,     # None → SR_LOG_JSON → False
    log_file: str | Path | None = None,  # None → SR_LOG_FILE → off
    force: bool = False,                 # cli 严格模式下强制装配
) -> None: ...
```

策略表（console 流定向）：

| surface | console 流 | 理由 |
|---|---|---|
| `web` | stderr | uvicorn 常规；stdout 不承诺 |
| `cli` | stderr | TUI/界面输出走 print/rich 占 stdout；**默认不装配**（严格开关语义，与现状逐字节一致），`SR_LOG_JSON`/`SR_LOG_FILE` 任一设置或 `force=True` 才装配 |
| `mcp` | stderr | stdout = JSON-RPC 协议帧，绝不能混日志 |
| `script` | **stdout** | 子进程/脚本输出被 `run_bg` 重定向进 run.log（喂 log_stall） |

行为细节：
- 幂等：重复调用先移除本模块此前挂的 handler 再重挂（输出不翻倍）；`TraceFilter` 仍全局只装一次（复用 `trace.py` 守卫）。
- 级别统一解析：`level` 显式参数 > `SR_LOG_LEVEL` > INFO；同时设 `strategy_research` 命名空间级别。
- JSON：`SR_LOG_JSON=1` → 全部 handler 换 `JsonFormatter`（复用现有类）。
- 文件（P1）：`SR_LOG_FILE=path` 或显式 `log_file` → `RotatingFileHandler(path, maxBytes=10MB, backupCount=5, encoding="utf-8")`；相对路径基于 `~/.quantnodes/logs/` 解析；`SR_LOG_FILE` 未设时**不创建**任何文件（默认关闭）。
- 任何装配失败不得阻断主流程（与现状 try/except 语义一致）。

### 4.2 `dual_log()`（P2，收敛 `_dlog`）

```python
def dual_log(module: str, msg: str, *args) -> None:
    text = msg % args if args else msg
    logger.info("[%s] %s", module.upper(), text)
    if not logging.getLogger().handlers:      # 无 handler → print 兜底（CLI 默认模式）
        print(f"[{module.upper()}] {text}", flush=True)
```

- `study/{runner,scheduler,store,monitor}.py` 的 4 处 `def _dlog` 删除，改为 `from ..observability.logging_setup import dual_log as _dlog`（调用点零改动）。
- 语义对照：CLI 默认模式输出不变（print 兜底）；web/MCP 下从双份变单份（logger→stderr，print 停发）。
- 前缀从 `[RUNNER:runner]` 简化为 `[RUNNER]`（module 传的就是大写段名，消除 runner.py 传 `"runner"`、调用处传 `"loop"` 的命名错位）。

### 4.3 hanging_events 加列（P0.5）

- schema 增加 `trace_id TEXT`、`round_num INTEGER`；`__init__` 里 `PRAGMA table_info` 检查 + `ALTER TABLE ADD COLUMN`（幂等，旧库自动迁移）。
- `record()` 增 keyword 参数 `trace_id`/`round_num`，为 `None` 时自动从 `get_trace_context()` 取——**6 个写入点零改动**（api/session/service、agent/circuit_breaker、agent/loop_progress、llm/openai_client、study/scheduler×2）。
- `report()` / `list_recent()` 输出补两列。
- `record_event()` 便捷函数同步透传。

### 4.4 替换点

| 现状 | 改为 |
|---|---|
| `api/app.py:42-55` | `configure_logging("web")` |
| `cli/__main__.py:161-178` | `configure_logging("cli")`（严格开关语义内置） |
| `core/mcp/server.py:874-889` | `configure_logging("mcp")` |
| `api/session/backfill_event_log.py:326-329` | `configure_logging("script")` |
| `setup_trace_logging()` | 保留（薄壳，向后兼容既有测试/调用方），内部复用新常量 |

## 5. print 治理分类（P2，全量 556 处）

扫描口径：`(?<![\w.])(print|pprint)\(`，全 `src/strategy_research`。

| 类 | 判定 | 数量 | 处置 |
|---|---|---|---|
| A. CLI/UI 输出 | `cli/**`、`core/*/cli.py`、`templates/**`、`scripts/**` | ~475 | 保留——产品面输出，print/rich 是正确工具 |
| B. 库内诊断 | core 非-cli 文件（db.py 26、data_import 14、preflight 10、alpha_zoo_convert 5、config_runner 5、autoresearch 3、mcp/server 3、openai_client 2、backtest/loop/shell_tools/loader/dag_renderer/llm__init__/slash_commands 各 1 等） | ~80 | 改 `logger.info/warning`（实现时逐处抽查场景，确属 CLI 工具输出的并入 A） |
| C. 子进程数据通道 | `engine/base.py:182,388`（`print(flush=True)` → run_bg 重定向 → run.log → log_stall 检测） | 2 | **保留 print**（数据通道语义，非日志），文件级豁免并冻结数量 |
| D. `_dlog` 双写 | study ×4 文件 ~40 处调用 | ~40 | §4.2 收敛为 `dual_log` |

**架构门禁** `tests/test_print_governance.py`：
1. 扫 `core/`（排除 `*/cli.py` 与豁免清单）禁止裸 `print(`——防新增回潮；
2. 豁免文件（engine/base.py）print 数量冻结不增长；
3. 禁止 `def _dlog` 在 study 包重新出现。

## 6. 分批与验收

| 批 | 内容 | 门禁 |
|---|---|---|
| P0 | `logging_setup.py`（策略表+幂等+级别/JSON 解析）+ 4 处替换 | 新增单测 + c1 基线不涨 |
| P0.5 | `DEFAULT_TEXT_FORMAT` 带 trace 字段 + hanging_events 加列 | 新增单测（旧库迁移、ContextVar 自动补） |
| P1 | `SR_LOG_FILE` RotatingFileHandler | 新增单测（默认不建文件/开启落盘/轮转参数） |
| P2 | `dual_log` 收敛 + B 类替换 + print 门禁测试 | 门禁测试 + c1 基线不涨 |

全量验收：cov 四段 ≥90（A 平台内核 / B 研究域 / C 能力层 / H core 剩余）、c1_gate 冻结基线 104、inventory `+N/0`、e2e `-m e2e` 全绿。
