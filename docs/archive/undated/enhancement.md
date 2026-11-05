# 借鉴增强方案（P0 + P1 + P1.5 + P2）

> 对应调研：`docs/archive/undated/vibe-trading-survey.md`（1805 行，完整功能清单）
> 对应总计划：本文件即主执行计划
> 状态：**执行中（P0+P1+P1.5 完成，P2 设计已确定）**
> 创建日期：2026-07-22

---

## 1. 总览与决策摘要

### 1.1 已锁定决策

| 决策点 | 选择 |
|---|---|
| 借鉴来源 | vibe-trading-ai 0.1.11（HKUDS，MIT License）+ llmwikify（Hook 系统） |
| 借鉴方式 | 整包复制关键模块 + 自写最小版 |
| 当前范围 | P0 + P1 + P1.5 + P2（17 周） |
| Skills 范围 | P0+P1 不涉及；P2 阶段再讨论 |
| CLI 名 | 保持 `quantnodes-research` |
| LLM 集成 | OpenAI 兼容通用（httpx，零 LangChain） |
| 沙箱策略 | AST guard + 路径白名单 |
| 测试策略 | 仅 e2e smoke test |
| 交付方式 | 按阶段 PR 交付（P0 → P1 → ...） |
| License 处理 | 保留 MIT，致敬原作者（HKUDS + llmwikify） |
| Hook 系统 | 纯 llmwikify 模式（UnifiedHook + AgentHook 两层） |
| Session-Memory | 自动归档到 `<workspace>/memory/` |
| Memory FTS5 | 全局 `~/.quantnodes-research/memory/memory_fts5.db` |
| Session 存储 | 全局 `~/.quantnodes-research/sessions.db` |
| Memory 注入 | 每个 user message 前注入 recalled memories |
| 自动 reindex | 启动时自动 reindex |

### 1.2 阶段总览

| 阶段 | 时间 | 交付 | 净增行数 | 复制行数 |
|---|---|---|---|---|
| P0 修通 init | Week 1-2 | PR #1-#3 | ~880 | 0 |
| P1 Agent 基础设施 | Week 3-5 | PR #4-#6 + AgentLoop 改造 | ~2 800 | ~940 |
| P1.5-a 核心接口 | Week 6 | types + agents + dag | ~300 | 0 |
| P1.5-b Controller | Week 7 | controller + prompt + validator | ~400 | 0 |
| P1.5-c 集成 | Week 8 | grounding + executors + cli | ~300 | 0 |
| P2 Hook + Memory + Session | Week 9-11 | Hook 系统 + FTS5 + Session | ~800 | ~500 |
| P3 Goal + Hypothesis | Week 12-14 | PR #9 | TBD | TBD |
| **合计** | **17 周** | **9 PRs** | **~5 480+** | **~1 440** |

### 1.3 与现有文档的关系

| 文档 | 关系 |
|---|---|
| `docs/archive/undated/vibe-trading-survey.md` | 总览（已完成，1805 行） |
| `docs/archive/2026-11/backtest-overhaul/README.md` Phase 1 | ⊆ 本计划 P0-T0.5 |
| `docs/archive/2026-11/backtest-overhaul/README.md` Phase 3 | ⊆ 本计划 P3（暂不执行） |
| `docs/archive/undated/autoresearch-design.md` | agent 角色定义，P1.5 替换 `_spawn_agent` |

---

## 2. P0 — 修通 init（Week 1-2，PR #1）

### 2.1 目标

`quantnodes-research init /path/to/workspace` 真正能跑出 workspace，且 baseline 回测有真实指标。

### 2.2 现状痛点（来自子代理扫描）

| # | 问题 | 影响 |
|---|---|---|
| G2 | `cmd_init()` 在 `.format()` 上踩 `{}` 报错 | workspace 生成 crash |
| G3 | CLI 与 `core/db.py` 的 DuckDB schema 重复且不一致 | init 后 baseline 导入失败 |
| G4 | `data_import.py` 把 OHLCV 压成 close 面板再丢失 | 数据丢失 bug |
| G5 | 默认 `FACTOR_EXPRS=[]`，`prepare.evaluate()` 返空指标 | baseline 不是 buy-and-hold |
| G7 | `FALLBACK_CHAINS` 引用 mootdx/eastmoney/baostock 等未实现 loader | 数据源死链 |
| G10 | `cmd_init --baseline` 选项未实现 | 文档承诺的功能缺失 |
| G17 | 没有 CLI `evaluate` 子命令 | 无法手动复跑 |

### 2.3 TODO 清单

#### Week 1 — 修 bug + 接数据源

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T0.1 | 修 `.format()` bug（改 `string.Template`）| `cli.py::_render_template` | ☐ | `cmd_init` 不再 KeyError |
| T0.2 | 统一 DuckDB schema（抽出 `_INIT_SCHEMA_SQL` 常量）| `core/db.py` + `cli.py` | ☐ | 两处共用一份 SQL |
| T0.3 | 修复 OHLCV 丢失 | `core/data_import.py` + `core/db.py::save_price_data` | ☐ | OHLV 保留，volume 非 0 |
| T0.4 | baseline `FACTOR_EXPRS` 默认值 | `templates/prepare.py` | ☐ | 跑出真实指标 |
| T0.5 | 新增 `DataLoaderProtocol` | `core/data_source/base.py`（新）| ☐ | 接口定义清晰 |
| T0.6 | 新增 `LOADER_REGISTRY` + `FALLBACK_CHAINS` | `core/data_source/registry.py`（新）| ☐ | 装饰器注册可用 |
| T0.7 | 实现 tencent loader | `core/data_source/tencent_loader.py`（新）| ☐ | `is_available()` True |
| T0.8 | 实现 eastmoney loader | `core/data_source/eastmoney_loader.py`（新）| ☐ | A 股 + 港股 EOD |
| T0.9 | 实现 akshare loader | `core/data_source/akshare_loader.py`（新）| ☐ | 多市场覆盖 |

#### Week 2 — preflight + init baseline + CLI

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T0.10 | 实现 preflight（4 项 check）| `core/preflight.py`（新）| ☐ | Rich 表输出 |
| T0.11 | preflight 检查 LLM key | `core/preflight.py` | ☐ | 检测 `OPENAI_API_KEY / DEEPSEEK_API_KEY` |
| T0.12 | preflight 检查 DuckDB 可写 | `core/preflight.py` | ☐ | 创建/删除测试表 |
| T0.13 | preflight 检查数据源 | `core/preflight.py` | ☐ | tencent.eastmoney.fetch 测试 |
| T0.14 | `cmd_init` 自动 baseline 导入 | `cli.py::cmd_init` | ☐ | init 后立刻有 OHLCV |
| T0.15 | baseline 回测 + 写 `run_0000/` | `cli.py::cmd_init` | ☐ | metrics.json 8 项 |
| T0.16 | CLI `evaluate <strategy>` 子命令 | `cli.py`（argparse）| ☐ | 手动复跑可 |
| T0.17 | 删除 mootdx/baostock 占位 | `core/data_source/` | ☐ | fallback 链更新 |
| T0.18 | 跑现有测试 | `tests/` | ☐ | 全部 PASS |

### 2.4 P0 验收

```bash
$ pip install -e .
$ quantnodes-research init /tmp/ws
✓ workspace 已创建（不再 crash）
$ ls /tmp/ws/strategies/<n>/runs/
run_0000/
$ cat /tmp/ws/strategies/<n>/runs/run_0000/metrics.json
{"sharpe": 0.42, "calmar": 0.38, "max_dd": -0.18, ...}  # 8 项真实指标
$ quantnodes-research preflight /tmp/ws
✓ LLM provider: ok
✓ DuckDB: writable
✓ Data source (tencent): reachable
✓ OHLCV integrity: ok
$ quantnodes-research evaluate /tmp/ws --strategy momentum_20_60
✓ 重新跑通
$ pytest tests/ -v
... PASSED
```

### 2.5 P0 PR 内容

```diff
src/agentnodes/
├── cli.py                       M  +85 -65
├── core/
│   ├── db.py                    M  +20 -10
│   ├── data_import.py           M  +15 -10
│   ├── data_source/
│   │   ├── base.py              +  80
│   │   ├── registry.py          +  80
│   │   ├── tencent_loader.py    +  150
│   │   ├── eastmoney_loader.py  +  150
│   │   ├── akshare_loader.py    +  150
│   │   ├── mootdx_loader.py     -  -30
│   │   └── baostock_loader.py   -  -30
│   └── preflight.py             +  200
└── templates/
    └── prepare.py               M  +5
```

---

## 3. P1 — Agent 真跑起来（Week 3-5，PR #2）

### 3.1 目标

LLM agent 通过 ReAct 循环修改 `strategy.py` 并 commit；cross-run memory 保留；3 道停止条件生效。

### 3.2 复制清单（来自 vibe-trading-ai 0.1.11，MIT License）

| 编号 | 源文件 | 行数 | 目标路径 | 改动 |
|---|---|---|---|---|
| C1 | `src/agent/tools.py` | 94 | `core/agent/tools.py` | 仅改 import |
| C2 | `src/agent/frontmatter.py` | ~50 | `core/agent/frontmatter.py` | 无 |
| C3 | `src/agent/progress.py` | ~150 | `core/agent/progress.py` | 改 home dir |
| C4 | `src/agent/trace.py` | ~300 | `core/agent/trace.py` | 改 home dir |
| C5 | `src/tools/redaction.py` | ~80 | `core/agent/redaction.py` | 仅改 import |
| C6 | `src/memory/persistent.py` | 265 | `core/memory/persistent.py` | 改 `~/.quantnodes-research/memory/` |
| **合计** | | **~940** | | |

每个文件头加：
```python
# Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS)
# Original: https://github.com/HKUDS/Vibe-Trading
# See docs/archive/undated/vibe-trading-credits.md
```

### 3.3 TODO 清单

#### Week 3 — 复制 + 核心工具

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T1.1 | 复制 6 个模块（C1-C6）| `core/agent/`, `core/memory/` | ✅ | 无文件头 (PR4) |
| T1.2 | 新建 `vibe-trading-credits.md` | `docs/` | ✅ | 列出 6 个文件 + LICENSE |
| T1.3 | OpenAI 兼容 LLM 客户端 | `core/llm/openai_client.py` | ✅ | httpx 调通 4 provider |
| T1.4 | LLM 响应解析（tool_calls）| `core/llm/parser.py` | ✅ | 3 段 JSON 兜底 |
| T1.5 | `read_file` 工具 | `core/agent/builtin_tools/` | ✅ | 读 workspace 文件 |
| T1.6 | `write_file` 工具 | `core/agent/builtin_tools/` | ✅ | AST guard + 路径白名单 |
| T1.7 | `run_backtest` 工具 | `core/agent/builtin_tools/` | ✅ | 调 run_backtest_from_yaml |
| T1.8 | `compute_factor` 工具 | `core/agent/builtin_tools/` | ✅ | 单 asset 单列 wide 格式 |
| T1.9 | `git_diff` 工具 | `core/agent/builtin_tools/` | ✅ | git diff 包装 + flag injection |
| T1.10 | `list_history` 工具 | `core/agent/builtin_tools/` | ✅ | results.tsv 读取 + 排序 |
| T1.11 | ToolRegistry 装配 | `core/agent/builtin_tools/` | ✅ | build_default_registry() |
| T1.12 | AST guard + 路径白名单 | `core/agent/sandbox.py` | ✅ | 23 模块拦截 + 路径越界拦截 |

#### Week 4 — AgentLoop mini + 集成

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T1.13 | ContextBuilder（tool list + workspace + memory）| `core/agent/context.py` | ✅ | 系统 prompt 含工具列表 |
| T1.14 | mini AgentLoop ReAct | `core/agent/loop.py` | ✅ | 循环跑通 |
| T1.15 | microcompact 层 | `core/agent/loop.py` | ✅ | 0.5×threshold 触发 |
| T1.16 | context_collapse 层 | `core/agent/loop.py` | ✅ | 0.7×threshold 触发 |
| T1.17 | HeartbeatTimer 接入 | `core/agent/loop.py` + `progress.py` | ✅ | 长工具调用 keepalive |
| T1.18 | TraceWriter 接入 | `core/agent/loop.py` + `trace.py` | ✅ | JSONL trace 写出 |
| T1.19 | PersistentMemory.snapshot 注入 | `core/agent/context.py` | ✅ | system prompt 冻结 |
| T1.20 | auto-recall `find_relevant` | `core/agent/context.py` | ✅ | `<recalled-memories>` 注入 user msg |
| T1.21 | git commit after run | `core/agent/loop.py` | ✅ | 每 run 自动 commit |

#### Week 5 — AgentLoop 改造（已完成）

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T1.22 | ContextBuilder 自定义 system_prompt | `core/agent/context.py` | ✅ | 自定义 prompt 可用 |
| T1.23 | AgentLoop allowed_tools + readonly | `core/agent/loop.py` | ✅ | 工具过滤可用 |
| T1.24 | AgentLoop run(context=) | `core/agent/loop.py` | ✅ | context 注入可用 |
| T1.25 | WriteFileTool is_readonly=False | `core/agent/builtin_tools/__init__.py` | ✅ | readonly 模式正确过滤 |
| T1.26 | 新增测试 12 个 | `tests/` | ✅ | 3607 passed |

> 注：原 T1.22-T1.28（停止条件 + CLI flags + e2e）已移至 P1.5。

### 3.4 P1 验收

```bash
$ export OPENAI_API_KEY=sk-xxx
$ export OPENAI_BASE_URL=https://api.deepseek.com/v1
$ export OPENAI_MODEL=deepseek-chat

$ quantnodes-research init /tmp/ws
$ quantnodes-research run --workspace /tmp/ws \
    --max-iter 3 --target-calmar 0.5 \
    --prompt "试试 momentum_20_60，看 sharpe 能不能到 0.8"

[iter=1] sharpe=0.42 calmar=0.38 tools=4 elapsed=12s
[iter=2] sharpe=0.55 calmar=0.49 tools=3 elapsed=15s
[iter=3] sharpe=0.81 calmar=0.62 tools=2 elapsed=14s
✓ Calmar ≥ 0.5 持续 3 轮，停止

$ git -C /tmp/ws log --oneline
abc1234 agent: iter=1 momentum_20_60 sharpe=0.42
def5678 agent: iter=2 momentum_20_60 sharpe=0.55
789abcd agent: iter=3 momentum_20_60 sharpe=0.81

$ cat ~/.quantnodes-research/memory/MEMORY.md
# Memory Index (1 entries)
- [feedback] momentum_20_60 在中小盘失效

$ pytest tests/test_e2e.py -v
test_init_and_baseline PASSED
test_agent_loop_3_iter PASSED
test_git_commits PASSED
test_persistent_memory PASSED
```

### 3.5 P1 PR 内容

```diff
src/agentnodes/
├── cli.py                              M  +10 -55
├── core/
│   ├── agent/                          +  ~1 700
│   │   ├── tools.py                    +   94 (复制)
│   │   ├── frontmatter.py              +   50 (复制)
│   │   ├── progress.py                 +  150 (复制)
│   │   ├── trace.py                    +  300 (复制)
│   │   ├── redaction.py                +   80 (复制)
│   │   ├── loop.py                     +  500 (自写)
│   │   ├── context.py                  +  150 (自写)
│   │   ├── registry.py                 +   30 (自写)
│   │   ├── sandbox.py                  +  100 (自写)
│   │   └── tools/                      +  440 (自写 6 文件)
│   ├── llm/                            +  250
│   │   ├── openai_client.py            +  200
│   │   └── parser.py                   +   50
│   ├── memory/                         +  265 (复制)
│   │   └── persistent.py
│   └── autoresearch.py                 M  +60
└── tests/
    └── test_e2e.py                     +  100

docs/
├── vibe-trading-survey.md              ✓ 已存在
└── vibe-trading-credits.md             +   30

README.md                               M  +30
```

---

## 4. P2 — Hook + Memory + Session（Week 9-11，**设计已确定**）

### 4.1 目标

实现 Hook 系统（借鉴 llmwikify）、Memory 增强（FTS5 + recency boost）、Session 管理（FTS5 搜索 + 自动归档）。

### 4.2 架构设计

```
src/agentnodes/
├── core/
│   ├── hooks/                          # 新建
│   │   ├── __init__.py
│   │   ├── unified.py                  # UnifiedHook (16 事件点)
│   │   ├── adapter.py                  # AgentHookAdapter
│   │   ├── composite.py                # CompositeHook + AgentHook
│   │   ├── context.py                  # AgentHookContext + UnifiedContext
│   │   ├── utils.py                    # maybe_await
│   │   └── bundled/                    # 内置 Hooks
│   │       ├── __init__.py
│   │       ├── session_memory.py       # SessionMemoryHook
│   │       └── command_logger.py       # CommandLoggerHook
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── persistent.py              # 增强
│   │   ├── fts5.py                    # 新建
│   │   └── models.py                  # 新建
│   ├── session/
│   │   ├── __init__.py
│   │   ├── models.py                  # 新建
│   │   ├── manager.py                 # 新建
│   │   └── db.py                      # 新建
│   └── agent/
│       └── context.py                 # 增强
└── tests/
    ├── test_hooks.py                  # 新建
    ├── test_session_memory.py         # 新建
    ├── test_memory_fts5.py            # 新建
    ├── test_memory_enhance.py         # 新建
    ├── test_session.py                # 新建
    └── test_p2_e2e.py                 # 新建
```

### 4.3 设计决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| Hook 系统 | ✅ 纯 llmwikify 模式 | 直接复制代码，经过验证 |
| 自定义 hooks | ✅ 支持 | `<workspace>/hooks/` 目录 |
| LLM 生成 slug | ✅ 需要 | 会话归档文件名 |
| Hook 执行方式 | ✅ 异步 | asyncio |
| 会话结束判断 | ✅ 用户主动 `/reset` | 简单明确 |
| 归档位置 | `<workspace>/memory/` | 项目局部性 |
| FTS5 存储 | 全局 `memory_fts5.db` | 跨 workspace |
| Session 存储 | 全局 `sessions.db` | 便于搜索 |
| Memory 注入 | 每个 user message 前 | 上下文最新 |
| 自动 reindex | 启动时 | 性能优先 |

### 4.4 Hook 系统设计

#### 4.4.1 两层架构

```
UnifiedHook (16 事件点)
    │
    ▼ (AgentHookAdapter 桥接)
AgentHook (13 事件点)
    │
    ▼ (CompositeHook 扇出)
具体 Hook 实现
```

#### 4.4.2 事件点（13 个）

| # | 事件 | 触发时机 |
|---|---|---|
| 1 | `wants_streaming()` | 循环开始前 |
| 2 | `before_iteration(ctx)` | 每次迭代开始 |
| 3 | `on_stream(ctx, delta)` | 流式 token 接收 |
| 4 | `on_stream_end(ctx, resuming)` | 流结束 |
| 5 | `emit_reasoning(ctx, content)` | 推理内容输出 |
| 6 | `emit_reasoning_end(ctx)` | 推理结束 |
| 7 | `before_execute_tools(ctx)` | 工具执行前 |
| 8 | `after_tool_executed(ctx, tc, result)` | 工具执行后 |
| 9 | `on_tool_error(ctx, tc, error)` | 工具执行失败 |
| 10 | `on_confirmation(ctx, tc)` | 用户确认 |
| 11 | `after_iteration(ctx)` | 迭代结束 |
| 12 | `finalize_content(ctx, content)` | 最终内容转换 |
| 13 | `on_error(ctx, error)` | 未处理异常 |

#### 4.4.3 CompositeHook 模式

```python
# 借鉴 llmwikify 的错误隔离
class CompositeHook:
    async def _fire(self, method_name, *args, **kwargs):
        for hook in self._hooks:
            try:
                method = getattr(hook, method_name)
                await maybe_await(method(*args, **kwargs))
            except Exception:
                logger.warning(f"Hook {hook.name}.{method_name} failed")
                # 单个失败不影响其他 hook
```

### 4.5 Memory 增强设计

#### 4.5.1 FTS5 索引

```sql
-- ~/.quantnodes-research/memory/memory_fts5.db
CREATE VIRTUAL TABLE memory_fts USING fts5(
    path,
    title,
    description,
    body
);
```

#### 4.5.2 Recency Boost

```python
# find_relevant() 增强
def find_relevant(self, query: str, max_results: int = 5) -> list[MemoryEntry]:
    # 现有: token_score = metadata_hits * 2.0 + body_hits * 1.0
    # 新增: recency_score = 1.0 / (1 + days_since_modified / 7)
    # 最终: score = token_score * recency_score
```

#### 4.5.3 Context Injection

```python
# 每个 user message 前注入
def format_context_for_prompt(self, query: str, max_results: int = 3) -> str:
    entries = self.find_relevant(query, max_results)
    if not entries:
        return ""
    lines = ["<recalled-memories>"]
    for e in entries:
        lines.append(f"- [{e.title}]({e.path.name}) — {e.description}")
    lines.append("</recalled-memories>")
    return "\n".join(lines)
```

### 4.6 Session 管理设计

#### 4.6.1 数据库 Schema

```sql
-- ~/.quantnodes-research/sessions.db
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    created_at REAL,
    updated_at REAL,
    workspace TEXT,
    metadata_json TEXT
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    role TEXT,
    content TEXT,
    timestamp REAL,
    metadata_json TEXT
);

CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=id
);
```

#### 4.6.2 Session-Memory Hook

```python
# 自动归档会话
class SessionMemoryHook(AgentHook):
    async def after_iteration(self, ctx):
        # 归档到 <workspace>/memory/
        pass
```

### 4.7 TODO 清单

#### P2-a: Hook 系统核心（Week 9）

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T2.a.1 | UnifiedHook | `core/hooks/unified.py` | ✅ | 从 llmwikify 复制 |
| T2.a.2 | AgentHookAdapter | `core/hooks/adapter.py` | ✅ | 从 llmwikify 复制 |
| T2.a.3 | CompositeHook + AgentHook | `core/hooks/composite.py` | ✅ | 从 llmwikify 复制 |
| T2.a.4 | AgentHookContext | `core/hooks/context.py` | ✅ | 从 llmwikify 复制 |
| T2.a.5 | maybe_await | `core/hooks/utils.py` | ✅ | 从 llmwikify 复制 |
| T2.a.6 | 测试 | `tests/test_hooks.py` | ✅ | 23 个测试通过 |

#### P2-b: Session-Memory Hook（Week 9-10）

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T2.b.1 | SessionMemoryHook | `core/hooks/bundled/session_memory.py` | ☐ | 会话归档 + LLM slug |
| T2.b.2 | CommandLoggerHook | `core/hooks/bundled/command_logger.py` | ☐ | 审计日志 |
| T2.b.3 | 测试 | `tests/test_session_memory.py` | ☐ | 8+ 测试 |

#### P2-c: Memory 增强（Week 10）

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T2.c.1 | FTS5 索引 | `core/memory/fts5.py` | ✅ | 全局 memory_fts5.db + 自动 reindex |
| T2.c.2 | Recency boost | `core/memory/persistent.py` | ✅ | find_relevant() |
| T2.c.3 | Context injection | `core/memory/persistent.py` | ✅ | format_context_for_prompt() |
| T2.c.4 | Write dedup | `core/memory/persistent.py` | ✅ | add() hash |
| T2.c.5 | 测试 | `tests/test_memory_fts5.py` + `tests/test_memory_enhance.py` | ✅ | 9 个测试通过 |

#### P2-d: Session 管理（Week 10-11）

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T2.d.1 | Session 模型 | `core/session/models.py` | ✅ | 数据类 |
| T2.d.2 | SessionManager | `core/session/manager.py` | ✅ | CRUD + FTS5 搜索 |
| T2.d.3 | SQLite 管理 | `core/session/db.py` | ✅ | 连接 + schema |
| T2.d.4 | 测试 | `tests/test_session.py` | ✅ | 15 个测试通过 |

#### P2-e: 集成 + E2E（Week 11）

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T2.e.1 | AgentLoop 集成 | `core/agent/loop.py` | ☐ | 插入 hook 调用点 |
| T2.e.2 | ContextBuilder 增强 | `core/agent/context.py` | ☐ | 注入 recalled memories |
| T2.e.3 | CLI 集成 | `cli.py` | ☐ | `session list/show/search` |
| T2.e.4 | E2E 测试 | `tests/test_p2_e2e.py` | ☐ | 8+ 测试 |

### 4.8 P2 验收

```bash
# Hook 系统
$ quantnodes-research run --workspace /tmp/ws --prompt "test"
# 验证 hooks 被调用

# Session-Memory
$ quantnodes-research /reset
# 验证会话归档到 <workspace>/memory/

# Memory FTS5
$ quantnodes-research memory search --query "momentum"
# 验证 FTS5 搜索

# Session 管理
$ quantnodes-research session list
$ quantnodes-research session search --query "strategy"
# 验证 session 管理

# 测试
$ pytest tests/test_hooks.py tests/test_session_memory.py tests/test_memory_fts5.py tests/test_memory_enhance.py tests/test_session.py tests/test_p2_e2e.py -v
# 56+ 测试通过
```

### 4.9 P2 预期测试数

| 模块 | 测试数 |
|---|---|
| Hook 系统 | 10+ |
| Session-Memory | 8+ |
| FTS5 | 10+ |
| Memory 增强 | 8+ |
| Session | 12+ |
| E2E | 8+ |
| **总计** | **56+** |

---

## 5. P3 Roadmap（P0 + P1 + P1.5 + P2 完成后执行）

### P3 — Goal + Hypothesis + Validation（Week 12-14，**全部完成**）

**复制清单**：
- `src/goal/{models,store,policy,context}.py` — ✅ P3-a
- `src/hypotheses/registry.py` — ✅ P3-b
- `backtest/validation.py` — ✅ P3-c

### P3-a — Goal 系统（已完成，162 测试）

- 完成日期：2026-07-22
- 提交人：ll
- 内容：
  - `core/goal/models.py`: GoalStatus (12 值) + RiskTier (4 值全部照搬) + GoalRecord (19 fields) + GoalClaim + GoalCriterion + EvidenceInput/Record + AuditRow + StaleGoalError
  - `core/goal/policy.py`: normalize_required_text + reject_live_execution_objective（拒 EN/CN live-trading）
  - `core/goal/store.py`: SQLite-backed GoalStore (1017 行复制 + 去 src.config accessor 依赖)
  - `core/goal/context.py`: format_goal_context + format_goal_continuation_prompt
  - `core/goal/cli.py`: 7 子命令 (start/status/evidence/audit/complete/list/cancel)
  - `core/goal/__init__.py`: 公共 API 导出
  - `cli.py`: 接入 `goal` 子命令
- 测试 (162 个)：
  - `test_goal_models.py` — 26 测试
  - `test_goal_policy.py` — 34 测试
  - `test_goal_store.py` — 32 测试
  - `test_goal_context.py` — 32 测试
  - `test_goal_cli.py` — 26 测试
  - `test_goal_e2e.py` — 12 测试
- 文档：`docs/archive/undated/goal-design.md`
- Commits：`0dab8b8` (Day 1) + `057a9dc` (Day 2) + `9540de3` (Day 3) + `1ae86e1` (Day 4)
- 测试结果：3770 → 3932 passed (+162, 0 regression)
- 备注：RiskTier 全部 4 个值照搬，LIVE_TRADING_OR_EXECUTION 在 replace_goal 显式拒绝

### P3-b — Hypothesis 系统（已完成，58 测试）

- 完成日期：2026-07-22
- 提交人：ll
- 内容：
  - `core/hypothesis/registry.py`: file-backed JSON registry（373 行复制 + 去 src.config accessor）
    - `Hypothesis` dataclass (mutable, 12 fields)
    - `HypothesisRegistry`: create / update / link_backtest / search / list / get
    - 5 statuses: exploring / testing / validated / rejected / monitoring
    - SHA-256-derived `hyp_<12hex>` IDs with collision suffix
    - Token-overlap search + recency tiebreak
    - 原子写 (temp + replace)
  - `core/hypothesis/auto_create.py`: `HypothesisAutoCreator` 幂等 helper
    - AgentLoop 首次调用时按 (strategy, market) 创建 exploring 状态
  - `core/hypothesis/cli.py`: 6 子命令 (create/list/show/update/search/link)
  - `cli.py`: 接入 `hypothesis` 子命令
- 测试 (58 个)：
  - `test_hypothesis.py` — 38 测试（dataclass + CRUD + search + atomicity + auto_create）
  - `test_hypothesis_cli.py` — 20 测试（6 子命令 + argparse）
- Commits：（待）
- 测试结果：3932 → 3990 passed (+58, 0 regression)
- 备注：自动创建触发条件 = session_id + strategy_name 都设置 + enable_hypothesis_auto_create=True + 现有 registry 无匹配 (strategy, market)

### P3-c — Validation 系统（已完成，52 测试）

- 完成日期：2026-07-22
- 提交人：ll
- 内容：
  - `core/validation/market.py`: MarketType (7 值) + bars_per_year + warn_if_unsupported_market
  - `core/validation/trade_input.py`: 轻量 `TradeInput` dataclass
  - `core/validation/utils.py`: `_json_safe` (NaN/inf → None) + `_sharpe`
  - `core/validation/monte_carlo.py`: MC permutation test
  - `core/validation/bootstrap.py`: Bootstrap Sharpe CI
  - `core/validation/walk_forward.py`: Walk-Forward analysis
  - `core/validation/runner.py`: orchestrator with multi-market support
  - `core/validation/cli.py`: `validate-run <run_dir>` 命令
  - `cli.py`: 接入 `validate-run` 子命令
- 测试 (52 个)：
  - `test_validation_market.py` — 6 测试
  - `test_validation_trade_input.py` — 10 测试
  - `test_validation_monte_carlo.py` — 6 测试
  - `test_validation_bootstrap_wf.py` — 11 测试
  - `test_validation_runner.py` — 7 测试
  - `test_validation_cli.py` — 12 测试
- 文档：`docs/archive/undated/validation-design.md`（多市场 roadmap）
- 测试结果：3990 → 4030 passed (+40, 0 regression)
- 备注：仅 A_SHARE / HK_EQUITY / US_EQUITY 完全支持；其他市场 emit UserWarning 并保留 market tag（不实现算法分支）

### P3-d — Integration + Docs（已完成，37 测试）

- 完成日期：2026-07-22
- 提交人：ll
- 内容：
  - `AgentLoop.__init__` 新增 4 个可选参数：
    - `session_id`：启用 goal context injection
    - `strategy_name`：启用 hypothesis auto-create
    - `enable_goal_injection` (默认 True)
    - `enable_hypothesis_auto_create` (默认 True)
  - `AgentLoop.run()` flow 增加 2 个 P3-d 钩子：
    - `_maybe_auto_create_hypothesis(task)`: 幂等按 (strategy, market) 创建
    - `_get_goal_context()`: 返回 `<current-research-goal>` block
  - `core/validation/cli.py`: `validate-run <run_dir>` 命令
  - 文档：
    - `docs/archive/undated/goal-design.md`（P3-a）
    - `docs/archive/undated/validation-design.md`（P3-c）
    - `CHANGELOG.md`（v0.3.0 entry）
    - `README.md`（CLI 表从 13 增至 27 个子命令）
- 测试 (37 个)：
  - `test_p3_integration.py` — 13 测试（agent + goal + hypothesis 联动）
  - `test_p3_e2e.py` — 4 测试（完整 lifecycle）
  - `test_validation_cli.py` — 12 测试（其中 12 已在 P3-c 计入）
  - `test_p3_integration.py` — 13 测试（新增）
- Commits：（待）
- 测试结果：4030 → 4053 passed (+23 净增，含 CLI tests 计入 P3-c)
- 备注：所有现有测试无回归

---

## 6. 验收矩阵

| 指标 | 当前 | P0 后 | P1 后 | P1.5 后 | P2 后 | **P3 后** |
|---|---|---|---|---|---|---|
| `cmd_init` 成功率 | 0% | 100% | 100% | 100% | 100% | **100%** |
| baseline 指标 | 空 | 8 项真实 | 8 项真实 | 8 项真实 | 8 项真实 | **8 项真实** |
| 数据源可达 loader | 1（占位）| ≥ 3 | ≥ 3 | ≥ 3 | ≥ 3 | **≥ 3** |
| 启动健康检查 | 无 | 4 项 | 4 项 | 4 项 | 4 项 | **4 项** |
| agent LLM 真跑 | 否（stub）| 否 | **是** | **是** | **是** | **是** |
| 工具数 | 0 | 0 | 6 | 6 | 6 | **6** |
| persistent memory | 无 | 无 | MEMORY.md + auto-recall | MEMORY.md + auto-recall | MEMORY.md + FTS5 + auto-recall | **MEMORY.md + FTS5 + auto-recall** |
| git commit 自动化 | 无 | 无 | 每 run 自动 | 每 run 自动 | 每 run 自动 | **每 run 自动** |
| Workflow 层 | 无 | 无 | 无 | **DAG + input_from + 4 种执行** | **DAG + input_from + 4 种执行** | **DAG + input_from + 4 种执行** |
| Grounding 接口 | 无 | 无 | 无 | **预留接口** | **预留接口** | **预留接口** |
| AST sandbox | 无 | 无 | guard + 路径白名单 | guard + 路径白名单 | guard + 路径白名单 | **guard + 路径白名单** |
| Hook 系统 | 无 | 无 | 无 | 无 | **UnifiedHook + AgentHook + 2 内置** | **UnifiedHook + AgentHook + 2 内置** |
| Memory FTS5 | 无 | 无 | 无 | 无 | **全局 FTS5 索引** | **全局 FTS5 索引** |
| Session 管理 | 无 | 无 | 无 | 无 | **FTS5 搜索 + 自动归档** | **FTS5 搜索 + 自动归档** |
| Memory 注入 | 无 | 无 | 无 | 无 | **每个 user message 注入** | **每个 user message 注入** |
| Goal 系统 | 无 | 无 | 无 | 无 | 无 | **GoalStore + 4 RiskTier + SHA-256 校验** |
| Hypothesis 系统 | 无 | 无 | 无 | 无 | 无 | **Registry + 5 statuses + token-overlap search** |
| Validation 工具 | 无 | 无 | 无 | 无 | 无 | **MC + Bootstrap + WF + multi-market** |
| Goal Context 注入 | 无 | 无 | 无 | 无 | 无 | **每个 user message 顶部注入** |
| 自动 Hypothesis 创建 | 无 | 无 | 无 | 无 | 无 | **首次 (strategy, market) 幂等创建** |
| CLI 子命令数 | 13 | 13 | 13 | 13 | 14 | **32（+19）** |
| Goal CLI | — | — | — | — | — | **start/status/evidence/audit/complete/list/cancel** |
| Hypothesis CLI | — | — | — | — | — | **create/list/show/update/search/link** |
| Validation CLI | — | — | — | — | — | **validate-run** |
| 仓库总行数 | ~4 000 | ~4 880 | ~6 655 | ~7 655 | ~8 500+ | **~11,500+** |
| 测试数 | 3144 | 3233 | 3607 | 3681 | **3737+** | **4,053+（+283 净增）** |
| e2e 测试 | 无 | 无 | 1 套 4 case | 2 套 9 case | 3 套 17 case | **6 套 35 case** |

> **P4 完成状态（2026-07-22）**：CLI 32 子命令（+19）、测试数 4,219（+449 净增 vs P2 结束）、仓库总行数 ~12,500+、e2e 测试 6 套 35 case、0 回归

---

## 7. 风险与回退

| 风险 | 概率 | 对策 | 回退 |
|---|---|---|---|
| `.format()` bug 影响多个文件 | 高 | T0.1 全局搜改 | 手动逐文件修 |
| DuckDB schema 不一致 | 高 | T0.2 抽常量共用 | 暂保留两套 |
| LLM 写非法 strategy.py | 高 | T1.12 sandbox | 截断 + 提示 |
| httpx 流式响应出错 | 中 | 参考 OpenAI Python SDK | 降级非流式 |
| 复制代码 import 路径冲突 | 中 | 复制时统一改 `core.X` | 全部重写 |
| P1.5 DAG 调度复杂度 | 中 | 简化串行 fallback | 降级到串行执行 |
| P1.5 input_from 数据流错误 | 中 | 校验 + 默认空输入 | 跳过失败 agent |
| P2 Hook 系统复杂度 | 中 | 简化事件点 | 降级到 8 个事件点 |
| P2 FTS5 中文分词 | 中 | 简单空格分词 | 回退到 token matching |
| P2 Session 存储性能 | 低 | 索引优化 | 清理旧 session |
| 17 周时间不够 | 中 | P0+P1+P1.5 必修；P2 可砍 e2e | P3 留待后续 |

---

## 7. 执行日志

> 执行时填充，记录每个 PR 的实际进度。

### PR #1（P0 - bug fixes）

- 启动日期：2026-07-22
- 提交人：ll
- commits：`c468e24` fix(cli): 修 .format() bug + 统一 DuckDB schema + 补 critic.md
- 测试结果：3144 → 3144 passed（无回归）
- 备注：3 个 bug 修复，net -72 行（删 99 加 27）

### PR #2（P0 - baseline + CLI + preflight + eastmoney）

- 启动日期：2026-07-22
- 提交人：ll
- commits：`f47d548` feat(cli): 修 OHLCV 丢失 + 加 cmd_evaluate/preflight + eastmoney loader + 默认因子
- 测试结果：3144 → 3144 passed（无回归）
- 备注：5 个 task + 1 extra，net +629 行

### PR #2.5（P0 - 77 个单元测试）

- 启动日期：2026-07-22
- 提交人：ll
- commits：`e1f05e6` test: 为 P0 新增功能补 77 个单元测试 + 修 OHLCV 生成器 invariant
- 测试结果：3144 → 3221 passed (+77)
- 备注：4 个新测试文件 + 修复 generate_sample_ohlcv_data invariant

### PR #3（P0 补完 + 文档/示例）

- 启动日期：2026-07-22
- 提交人：ll
- commits：`e5ab5ea` feat: PR3 补 P0 范围 + 文档示例 (与并行 agent 协作后状态)
- 内容：
  - **G11** 修 run_card 白名单：SCHEMA_VERSION 0.1 → 0.2，BACKTEST_SUMMARY_KEYS 增加 run/strategy/action
  - **G8** FactorStrategy 支持 alpha_id + alpha_ids（Alpha Zoo 单/组合因子）
  - **G10** cmd_init 加 `--no-baseline` 选项
  - **G12** cmd_init 写入完整 config.yaml（data/rebalance/cost/risk 节）
  - **G13** FactorStrategy 修 nlargest 在 wide factor_values 时的 crash
- 测试：新增 12 个 + 修复 2 个回归 → 3233 passed（无回归）
- 备注：更新 README.md + 新增 examples/demo_workflow.py

### PR #4（P1 - 复制 6 个 agent 基础模块）

- 启动日期：2026-07-22
- 提交人：ll
- commits：
  - `0defd47` feat(agent): PR4 commit 1 — 复制 6 个 agent 基础模块
- 内容：从 vibe-trading-ai 0.1.11 (HKUDS, MIT) 复制 6 个模块到 `core/agent/` 和 `core/memory/`
  - **tools.py** (94 行): BaseTool + ToolRegistry
  - **frontmatter.py** (40 行): YAML frontmatter 解析
  - **progress.py** (184 行): ProgressEvent + HeartbeatTimer
  - **trace.py** (284 行): TraceWriter（env var 重命名 VIBE_TRADING_ → STRATEGY_RESEARCH_）
  - **redaction.py** (209 行): redact_payload + is_sensitive_arg
  - **persistent.py** (368 行): PersistentMemory（路径改 ~/.quantnodes-research/memory/）
- 适配原则：**不修改文件内容（除上述必要适配），不加任何 header 注释**
- 归属文档：`docs/archive/undated/vibe-trading-credits.md`（新增，~80 行）
- 测试：3233 passed（无回归）
- 备注：PR4 拆为 commit 1（复制）+ commit 2（credits + verification）

### PR #5（P1 - LLM 客户端 + 工具 + 沙箱 + CLI flags）

- 启动日期：2026-07-22
- 提交人：ll
- commits：
  - `af2995f` PR5-c1: LLMConfig + 4 层合并 + .env + profile 切换
  - `6c9ee5d` PR5-c2: OpenAI 兼容客户端 + parser + errors
  - `072666a` PR5-c3: sandbox AST guard + 路径白名单
  - `448f549` PR5-c4: 6 BaseTool 工具 + registry
  - `e1ac378` PR5-c5: --llm-* flags + deps(httpx,dotenv) + 测试
- 内容（5 子 commits，+4436 行）：

  **PR5-c1 LLMConfig** — 17 字段 immutable dataclass + 4 层合并 (CLI > env > yaml > 默认)
  - 文档：`docs/llm-config-template.yaml`（100 行，7 profile 示例）
  - 支持 4 provider 默认: openai/deepseek/kimi/qwen
  - api_key 永远从 env 读 (OPENAI_API_KEY)，不进 yaml
  - 隐式 profile 不存在 / yaml 缺失 → 静默回退到代码默认值
  - python-dotenv 自动加载（best-effort，可选依赖）

  **PR5-c2 OpenAI 客户端** — sync/async/stream + 指数退避重试 + 4 段错误映射
  - 401/403 → LLMAuthError, 429 → LLMRateLimitError, 5xx → LLMServerError
  - timeout → LLMTimeoutError, malformed → LLMMalformedResponseError
  - 缺 api_key → LLMConfigError（启动期早抛）
  - Parser: 3 段兜底 JSON (standard / ```json``` / strip non-JSON)
  - SSE: data: 行解析，跳过空行/[DONE]/malformed
  - 用量统计 + finish_reason 透传

  **PR5-c3 Sandbox** — AST guard + PathWhitelist 两层防护
  - AST 拦截 exec/eval/compile/__import__/breakpoint
  - AST 拦截 23 个危险模块 (os/subprocess/shutil/socket/requests/...)
  - AST 拦截 dunder 属性访问（除白名单）
  - 允许 pandas/numpy/scipy/typing/常见策略代码
  - PathWhitelist: 默认 write_roots=strategies/templates/memory/logs
  - 默认 read_roots: + data/docs/. (workspace 根文件)
  - 拒绝: 绝对路径 / ../ / UNC / ~ / 空 / 非字符串

  **PR5-c4 6 个 BaseTool** — kwargs 注入 workspace，无状态
  - ReadFileTool (read_file): 读 + limit/offset + UTF-8 校验
  - WriteFileTool (write_file): 写 + AST guard + 自动创建父目录
  - RunBacktestTool (run_backtest): 包装 core.backtest.run_backtest_from_yaml
  - ComputeFactorTool (compute_factor): 单 asset 单列 wide 格式
  - GitDiffTool (git_diff): staged / ref / pathspec + flag injection 防护
  - ListHistoryTool (list_history): results.tsv + 排序 + limit
  - build_default_registry() 注册全部 6 个
  - 错误统一 JSON envelope: {status: 'error', error: '...'}

  **PR5-c5 CLI flags** — 12 个 --llm-* flag + pyproject deps
  - parent parser: --llm-profile / --llm-model / --llm-base-url / --llm-temperature
                   / --llm-max-tokens / --llm-top-p / --llm-timeout
                   / --llm-max-retries / --llm-seed / --llm-stream / --llm-no-stream
  - 顶层 --llm-list-profiles 调试入口
  - 挂到 run / evaluate / autoresearch 3 个子命令
  - pyproject.toml: httpx>=0.27 (强制), python-dotenv (可选)

- 边界：**不动 _spawn_agent / core/autoresearch.py**（PR7 才替换）
- 测试：3252 → 3526 passed（+274 无回归）
- 备注：5 commits 拆分，c1 准备配置 / c2 准备客户端 / c3 准备安全 / c4 准备工具 / c5 暴露 CLI

### PR #6（P1 - AgentLoop + Context + 3 层压缩）

- 启动日期：2026-07-22
- 提交人：ll
- commits：
  - `7ab7888` PR6-c1: ContextBuilder + token 估算
  - `5f995dd` PR6-c2: AgentLoop ReAct 核心
  - `dfc893e` PR6-c3: 3 层压缩 + Heartbeat + Trace + git commit
- 内容（3 commits，+~1160 行）：

  **PR6-c1 ContextBuilder** — 构建 system + user 提示，冻结 memory snapshot
  - build_system_prompt(): 角色 + 工具清单 + workspace + memory
  - build_initial_messages(): system + user (含 auto-recalled memories)
  - estimate_tokens(): 粗估 (chars / 3)
  - 冻结行为：构造时一次性 snapshot，后续 add() 不影响

  **PR6-c2 AgentLoop ReAct 核心** — 循环执行 ReAct 推理
  - 主循环: run(task) → LoopResult
  - 4 种 finished_reason: stop / max_iter / no_progress / error
  - no_progress 检测: 最近 N 个 tool_calls hash 相同
  - workspace 自动注入到 tool_call kwargs
  - tool 执行异常 → JSON error envelope (不 crash loop)

  **PR6-c3 3 层压缩 + Heartbeat + Trace + git commit**
  - L1 microcompact (50%): 截断 tool_result > 500 chars
  - L2 context_collapse (70%): 摘要旧消息, 保留最近 4 条
  - L3 hard_truncate (90%): 仅保留 system + 最近 4 条
  - HeartbeatTimer: tool 执行时自动包装, 长时间工具 keepalive
  - TraceWriter: trace.jsonl 记录全部事件 (可选)
  - auto_git_commit: run() 末尾自动提交 (可选)

- 边界：**不动 _spawn_agent / core/autoresearch.py**（PR7 才替换）
- 测试：3526 → 3595 passed（+69 无回归）
- 备注：3 commits 拆分，c1 准备提示 / c2 实施循环 / c3 扩展压缩+trace

### AgentLoop 改造（P1 补充）

- 启动日期：2026-07-22
- 提交人：ll
- 内容：
  - ContextBuilder 加 system_prompt + user_message_prefix 参数
  - AgentLoop 加 system_prompt + allowed_tools + readonly + run(context=)
  - WriteFileTool 加 is_readonly = False
  - 新增测试 12 个
- 测试结果：3595 → 3607 passed（+12 无回归）
- 备注：为 P1.5 Workflow 层做准备

### P1.5 计划

- 计划日期：2026-07-22
- 设计文档：`docs/design/workflow-design.md`
- 阶段划分：P1.5-a（Week 6）+ P1.5-b（Week 7）+ P1.5-c（Week 8）
- 总测试数：88+ 个新测试
- 状态：P1.5-a/b/c 完成

### P1.5-a 核心接口（已完成）

- 完成日期：2026-07-22
- 提交人：ll
- 内容：
  - `core/workflow/types.py`: AgentStatus / AgentCall / RoundResult / SwarmTask
  - `core/workflow/agents.py`: AgentExecutor Protocol + AgentRegistry
  - `core/workflow/dag.py`: topological_layers() + validate_dag()
  - `core/workflow/__init__.py`: 导出
- 测试：3607 → 3644 passed（+37 无回归）

### P1.5-b Controller + input_from（已完成）

- 完成日期：2026-07-22
- 提交人：ll
- 内容：
  - `core/workflow/controller.py`: WorkflowController（DAG 调度 + input_from + 重试）
  - `core/workflow/prompt.py`: PromptBuilder（动态 prompt 构造 + 缓存）
  - `core/workflow/validator.py`: AgentValidator（Schema + 逻辑验证）
- 测试：3644 → 3689 passed（+45 无回归）

### P1.5-c 集成（已完成）

- 完成日期：2026-07-22
- 提交人：ll
- 内容：
  - `core/workflow/grounding.py`: GroundingProvider Protocol + DummyGroundingProvider
  - `core/workflow/executors.py`: AgentLoopExecutor + PythonExecutor + CLIExecutor + StubExecutor
  - `tests/test_workflow_e2e.py`: 6 个 e2e 测试
- 测试：3689 → 3695 passed（+6 无回归）

### P2 计划

- 计划日期：2026-07-22
- 设计文档：`docs/archive/undated/enhancement.md` P2 章节
- 阶段划分：P2-a（Week 9）+ P2-b（Week 9-10）+ P2-c（Week 10）+ P2-d（Week 10-11）+ P2-e（Week 11）
- 总测试数：81+ 个新测试
- 状态：P2-a/b/c/d 完成，P2-e 待开始
- Hook 系统：纯 llmwikify 模式（UnifiedHook + AgentHook 两层）
- 复制清单：5 个文件从 llmwikify 复制

### P2-a Hook 系统核心（已完成）

- 完成日期：2026-07-22
- 提交人：ll
- 内容：
  - `core/hooks/unified.py`: UnifiedHook (16 事件点)
  - `core/hooks/adapter.py`: AgentHookAdapter
  - `core/hooks/composite.py`: CompositeHook + AgentHook (13 事件点)
  - `core/hooks/context.py`: AgentHookContext
  - `core/hooks/utils.py`: maybe_await
  - `core/hooks/bundled/session_memory.py`: SessionMemoryHook
  - `core/hooks/bundled/command_logger.py`: CommandLoggerHook
- 测试：3695 → 3718 passed（+23 无回归）

### P2-c Memory 增强（已完成）

- 完成日期：2026-07-22
- 提交人：ll
- 内容：
  - `core/memory/fts5.py`: MemoryFTS5 (全局 FTS5 索引)
  - `core/memory/persistent.py`: recency boost + write dedup + format_context_for_prompt()
  - `core/memory/__init__.py`: 导出
- 测试：3718 → 3776 passed（+58 无回归）

### P2-d Session 管理（已完成）

- 完成日期：2026-07-22
- 提交人：ll
- 内容：
  - `core/session/models.py`: Session + SessionMessage
  - `core/session/db.py`: SessionDB (SQLite + FTS5)
  - `core/session/manager.py`: SessionManager
  - `core/session/__init__.py`: 导出
- 测试：3776 passed（无回归，已计入 P2-c）

---

## 3.6 P1.5 — Workflow 层（Week 6-8，PR #7）

### 3.6.1 目标

实现 Workflow 层，替代 `cmd_autoresearch` 中的 `_spawn_agent` stub，
提供 DAG 调度 + input_from 数据流 + 4 种执行方式 + Grounding 预留接口。

### 3.6.2 设计文档

详见 `docs/design/workflow-design.md`。

### 3.6.3 TODO 清单

#### P1.5-a 核心接口（Week 6）

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T1.5.a.1 | types.py — AgentStatus / AgentCall / RoundResult / SwarmTask | `core/workflow/types.py` | ✅ | 8 个测试通过 |
| T1.5.a.2 | agents.py — AgentExecutor Protocol + AgentRegistry | `core/workflow/agents.py` | ✅ | 8 个测试通过 |
| T1.5.a.3 | dag.py — topological_layers() + validate_dag() | `core/workflow/dag.py` | ✅ | 10 个测试通过 |
| T1.5.a.4 | __init__.py — 导出 | `core/workflow/__init__.py` | ✅ | 导入可用 |

#### P1.5-b Controller + input_from（Week 7）

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T1.5.b.1 | controller.py — WorkflowController（DAG 调度 + input_from + 重试）| `core/workflow/controller.py` | ✅ | 15 个测试通过 |
| T1.5.b.2 | prompt.py — 动态 prompt 构造（.prompts/*.md + context）| `core/workflow/prompt.py` | ✅ | 5 个测试通过 |
| T1.5.b.3 | validator.py — Agent 输出验证（Schema + 逻辑）| `core/workflow/validator.py` | ✅ | 10 个测试通过 |

#### P1.5-c 集成（Week 8）

| # | 任务 | 文件 | 状态 | 验收 |
|---|---|---|---|---|
| T1.5.c.1 | grounding.py — GroundingProvider Protocol（预留接口）| `core/workflow/grounding.py` | ✅ | 3 个测试通过 |
| T1.5.c.2 | executors.py — 4 种 AgentExecutor 实现 | `core/workflow/executors.py` | ✅ | 10 个测试通过 |
| T1.5.c.3 | 集成到 cmd_autoresearch | `cli.py` | ✅ | 8 个测试通过 |
| T1.5.c.4 | e2e smoke test | `tests/test_workflow_e2e.py` | ✅ | 5 个测试通过 |

### 3.6.4 P1.5 验收

```bash
$ quantnodes-research autoresearch /tmp/ws --strategy momentum_20_60
[Workflow] 启动研究循环
[Round 1] DAG 调度: 9 个 agent 串行执行
  [Layer 0] researcher → data_quality → factor_analyst → strategist → portfolio_construction
  [Layer 1] risk_controller → attribution_analyst → anti_overfit_analyst → backtest_diagnostics
  [Result] calmar=0.42, sharpe=0.55, verdict=keep
[Round 2] ...
✓ Workflow 层正常工作
```

### 3.6.5 文件结构

```diff
src/agentnodes/
├── core/
│   ├── workflow/                    # 新增
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── agents.py
│   │   ├── dag.py
│   │   ├── grounding.py
│   │   ├── controller.py
│   │   ├── prompt.py
│   │   └── validator.py
│   ├── agent/                       # 已存在
│   └── llm/                         # 已存在
└── tests/
    ├── test_workflow_types.py       # 新增
    ├── test_workflow_agents.py      # 新增
    ├── test_workflow_dag.py         # 新增
    ├── test_workflow_controller.py  # 新增
    ├── test_workflow_prompt.py      # 新增
    ├── test_workflow_validator.py   # 新增
    ├── test_workflow_grounding.py   # 新增
    ├── test_workflow_executors.py   # 新增
    └── test_workflow_e2e.py         # 新增
```

---

## 附录：借鉴模块来源对照表

### vibe-trading-ai 0.1.11（HKUDS，MIT License）

| 来源 | 行数 | License | 借鉴方式 |
|---|---|---|---|
| `src/agent/tools.py` | 94 | MIT | 整包复制 |
| `src/agent/frontmatter.py` | ~50 | MIT | 整包复制 |
| `src/agent/progress.py` | ~150 | MIT | 整包复制 |
| `src/agent/trace.py` | ~300 | MIT | 整包复制 |
| `src/tools/redaction.py` | ~80 | MIT | 整包复制 |
| `src/memory/persistent.py` | 265 | MIT | 整包复制 |

### llmwikify（Hook 系统）

| 来源 | 行数 | License | 借鉴方式 |
|---|---|---|---|
| `foundation/callback/composite.py` | ~150 | MIT | 整包复制 |
| `foundation/callback/context.py` | ~50 | MIT | 整包复制 |
| `foundation/utils.py` | ~30 | MIT | 整包复制 |
| `kernel/agent/hook.py` | ~100 | MIT | 整包复制 |
| `apps/chat/agent/unified/hook_adapter.py` | ~80 | MIT | 整包复制 |

> 完整功能清单与设计模式见 `docs/archive/undated/vibe-trading-survey.md`（1805 行）。
> 所有借鉴代码均在文件头标注 `# Adapted from <source> (MIT License, <author>)`。