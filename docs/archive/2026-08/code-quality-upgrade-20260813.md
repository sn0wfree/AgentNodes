# 代码质量升级评估与执行记录（2026-08-13）

> **Status:** In progress (branch `refactor/code-quality-upgrade`)
> **Scope:** `quantnodes-strategy-research` v0.6.0
> **执行范围:** 全部 5 阶段（P0–P5）
> **用户决策:** ① DI 层真接线 ② 事件总线三代暂不动

## 0. 快照

| 指标 | 值 |
| --- | --- |
| 源码 | 821 个 .py，~114,509 LoC |
| 测试 | ~430 个测试文件 |
| 超 500 行文件 | 43 个；超 300 行 79 个 |
| ruff 剩余 | 7（C901×4 + F401×3） |
| TODO/FIXME | 35 处；`type: ignore`/`noqa` 207 处 |

---

## 1. 评估结论（按四维度）

### 1.1 代码架构

| 问题 | 证据 |
| --- | --- |
| 死 DI 层 | `api/container.py` / `api/dependencies.py` 自述未接线；真实用 `chat.py:130 _get_session_service` 私有单例 |
| 路由层过胖 | `api/routers/chat.py`(1900) 含全部 slash 业务逻辑 + 9-agent 配置构造；`web_session.py`(1515) 含整个 SQLite 持久化层 |
| 循环依赖 | `chat.py` ↔ `web_session.py` 函数内互导；`app.py:54` core→api 反向回调 |
| 三套回测引擎 | `core/engine/`(仅 CLI 用) / `backtest.py` 子进程(默认路径) / `utils/strategy_engine.py` + `utils/backtest_engine.py`；`engine/cli.py:99-104` 键名不匹配 bug（读 `sharpe_ratio` 实际返回 `sharpe` → N/A） |
| 双前端双会话库 | 遗留 Jinja `/webui` + React SPA；CLI `sessions.db` 与 API 统一 DB 互不可见 |

### 1.2 代码复用

- MCP server 完全重造：10 个同名工具独立实现（手写 schema/闭包 handler），与 `BaseTool`/`ToolRegistry` 零共享
- `_ok`×5、`_err`×3；两套 coercion（`tools.py` 注解驱动 vs `builtin_tools/utils.py` 名字驱动）；两套 truncate
- 9-agent 配置逐字复制：`chat.py:1205` vs `study.py:344`
- `/goal` 实现 4 遍（API chat / REST / CLI slash / CLI argparse）；`/study` 2 遍
- 算子词汇表 3 份（`ALPHA_ZOO_OPS` / `compute_factor.OPERATORS` / `alpha_zoo_convert` 映射）；gtja191 `_sma`×18、alpha101 `_where_ternary`×10、academic `_cross_sectional_zscore`×10
- study/：`meets_metric_targets`、`ShutdownReason`、`EventEmitter`、`_dlog` 在 executor/runner 双份
- 3 个 risk-parity 实现；2 个 `detect_market`；~408 因子 `.py`+`.yaml` 双事实源

### 1.3 可维护性

- God 模块：`builtin_tools/__init__.py` 3199 行/25 类（docstring 仍写 "11 个工具"）；`AgentLoop` ~1760 行/70+ 方法；`autoresearch.py` 2108 行（7 职责 + ~500 行 stub agent 驻留生产）
- `AgentLoop` 5 对 sync/async 复制粘贴（run/arun、_execute_tool_call/_aexecute_*、heartbeat、batch、compact）
- 名字分派链：`if tc.name == "delegate_to_agent"` sync/async 各一份；`chat_loop.py:93,104` 直接掏 `registry._tools`
- ~600 行工具说明书 docstring 样板；陈旧注释（"11 个工具"）
- 生产路径残留 `print()`（chat.py:958、study.py:234）

### 1.4 技术债

- 存储：6+ SQLite 文件；`event_log` DDL×3 不一致、`messages` DDL×2 不一致；连接样板/PRAGMA/`_write_transaction` 逐 store 复制；`HypothesisRegistry` 整库复制带 JSON 回退分支；JSON 列当伪关系表
- 迁移：仅 `web_session` 有版本化迁移（user_version 1→5），其余 ad-hoc
- 死代码：`data_source/cache.py` 零引用、`study/executor.py` 遗留类、`@slash_command` 装饰器零使用、`container.py`/`dependencies.py` 仅测试用、`run_research_round` 遗留 monolith
- 双事实源：`event_log` DDL 3 处、`messages` DDL 2 处、FTS 两套 tokenizer

---

## 2. 执行计划（含进度）

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| P0 | 清 7 个 ruff 错误（C901×4 + F401×3）；`_ok`/`_err` 收敛 | ✅ 完成 |
| P1 | 拆 `builtin_tools/__init__.py` 为 8 个域模块；MCP server 包装 `ToolRegistry`（删 7 个重复工具）；废弃旧 coercion | ✅ 完成 |
| P2 | `core/storage/sqlite.py` 共享层，goal/study/hypothesis stores 迁移；HypothesisRegistry 删 JSON 后端 | ✅ 完成 |
| P2 | 统一会话库（退役 sessions.db） | ⏸ 推迟：schema 根本冲突（id INTEGER vs TEXT、user_id NOT NULL），需数据迁移 + 6+ 测试改写。与事件总线同款风险决策 |
| P3 | DI 容器真接线（单一构造点 + 统一 session DB schema，修 DDL 分叉根因）；9-agent 配置抽工厂；slash 命令移出 chat.py（1900→921 行） | ✅ 完成 |
| P3 | web_session.py 持久层并入 api/session/store.py；AgentLoop sync/async 去重 | ⏸ 推迟：机械搬运/大改，行为价值低、churn 高，宜作独立后续 |
| P4 | engine CLI 键名 bug 修复（sharpe_ratio→sharpe 等）；删死代码（data_source/cache.py、slash_decorator.py） | ✅ 完成 |
| P4 | 指标套件（calc_metrics/extended_metrics/portfolio_metrics）全量键名统一；alpha zoo `.py` 双事实源清理；stub agent 移测试；`run_research_round`/`study/executor.py` 退役 | ⏸ 推迟：均需大面积测试改写（test_study_executor 582 行等），宜作独立后续 |
| P4 | `study/executor.py` 退役（2026-08-14 追加轮完成） | ✅ `ce5fa88`：双实现合一至 runner——acceptance_config_from_targets 移植 runner；workflow.py 引用直改；`__init__` 全量改从 runner 导出；test_study_executor 582 行改写为 runner 版（monitor 旧语义测试删除，由 test_study_monitor.py 覆盖）；`run_research_round` 退役仍推迟 |
| P5 | CI 加 ruff 门禁；执行记录文档更新；全量测试收尾 | ✅ 完成 |

**额外修复（P3 触发）**：`SQLiteStore` 与 `web_session` 的 sessions/messages DDL 分叉是真实隐患根因（谁先建表谁赢），已统一 schema + user_id 兼容插入 + FK 下先插父行；两个 TUI 流式测试原本依赖该 FK bug 的降级路径才通过，已改为显式 mock 降级。

**追加轮（2026-08-14）：sync/async 公用专项**

| 项 | 状态 |
| --- | --- |
| Tier 1: conftest 会话级 `SR_WORKSPACE_PATH` 隔离（灭 cwd 会话库跨测试污染） | ✅ |
| Tier 1: 删 DELETE-CANDIDATE 死代码（session router、update_message_content、warm fn、SessionStore 遗留方法） | ✅ |
| Tier 1: tests 目录 ruff 1163→0（F841 用保留 RHS 求值的正确变换；F821 补导入；F811 删 5 处重复定义；删 3 个引用已移除 API 的死测试类） | ✅ |
| `run`/`arun` 合一为 `_run_loop_core(async_mode)` + `_run_coro_in_sync` 桥 | ✅ |
| `_maybe_compact`/`_amaybe_compact` 合一为共享同步核心 + to_thread | ✅ |
| `_execute_tool_call`/`_aexecute_tool_call`、heartbeat 对合一（async 核心 + async_mode 分支） | ✅ |
| `_execute_tool_batch` 对 | ⏸ 保留双实现：sync 为 ThreadPoolExecutor 真并行，async 为 gather（loop 内顺序执行），并行语义差异需专门论证 |
| `_stream_chat`/`_astream_chat` 对 | 建议保留双实现（同步/异步迭代器协议不同，消费方不同） |
| 底层双 API（`chat`/`achat`、`stream`/`astream`） | 不合并（httpx sync/async 双 client） |

**公用原则（结论）**：以 async 为基座、sync 薄桥；纯逻辑抽纯函数零成本共用；IO 边界用 async_mode 参数化或 to_thread 整包；底层双 API 与流式不强合。`ainvoke`（async 包装 sync）与 `_run_coro_in_sync`（sync 桥 async）形成闭环。

**完成标准**：每阶段 `ruff check src` 全绿 + 相关测试通过；阶段边界跑全量回归。最终全量 **10732 passed / 0 failed**。

## 3. 风险清单

- P2 会话库统一：CLI/API 双栈迁移，会话互通为行为变更
- P3 DI 接线：牵动 15 个 router，需逐路由验证
- P4 指标键名：下游消费方多（engine CLI、run_card、validation、MCP）
- 删除项（`run_research_round` 等）逐一确认测试引用后动手
