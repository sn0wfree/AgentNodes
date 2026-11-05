# P5 执行计划：P2-b+P2-e 集成 → Skills → Swarm → MCP

> 创建日期：2026-07-22
> 完成日期：2026-07-22
> 状态：**全部完成**
> 最终版本：5092 tests（原 5050，+42）

---

## 总览

| 阶段 | 名称 | 周期 | 新增行数 | 新增测试 | 新增文件 |
|------|------|------|---------|---------|---------|
| 1 | P2-b+P2-e Hook/Memory/Session 集成 | Week 1-2 | ~800 | ~25 | 3 new + 3 modified |
| 2 | P5-Skills 系统 | Week 3 | ~600 | ~25 | 5 new |
| 3 | P5-Swarm 多智能体 | Week 4-5 | ~1200 | ~30 | 8 new |
| 4 | P5-MCP Server | Week 6 | ~800 | ~20 | 5 new |
| **总计** | | **6 周** | **~3400** | **~100** | **~21 new** |

预计总测试数：5050 → **~5150 tests**

---

## 阶段 1：P2-b+P2-e Hook/Memory/Session 集成

### 1.1 AgentLoop Hook 集成

**文件：** `src/strategy_research/core/agent/loop.py`

#### 改动内容

1. **新增 import**
   ```python
   from ..hooks.composite import CompositeHook
   from ..hooks.context import AgentHookContext
   ```

2. **`__init__` 新增参数**
   ```python
   hooks: CompositeHook | None = None,
   ```

3. **新增 `_fire_hooks()` 同步适配器**
   - CompositeHook 方法是 async，AgentLoop 是 sync
   - 用 `asyncio.run()` 桥接（或 `asyncio.get_event_loop().run_until_complete`）
   - 异常隔离：单个 hook 失败不影响循环

   ```python
   def _fire_hooks(self, method_name: str, *args: Any, **kwargs: Any) -> None:
       """Sync adapter for async CompositeHook methods."""
       if self._hooks is None:
           return
       import asyncio
       try:
           method = getattr(self._hooks, method_name)
           coro = method(*args, **kwargs)
           if asyncio.iscoroutine(coro):
               loop = asyncio.new_event_loop()
               try:
                   loop.run_until_complete(coro)
               finally:
                   loop.close()
       except Exception:
           logger.warning("Hook %s failed", method_name, exc_info=True)
   ```

4. **新增 `_build_hook_context()` 构造方法**
   ```python
   def _build_hook_context(self, iteration: int, messages: list) -> AgentHookContext:
       return AgentHookContext(
           iteration=iteration,
           messages=messages,
       )
   ```

5. **在迭代循环中插入 7 个 hook 调用点**

   | 位置 | 调用 | 参数 |
   |------|------|------|
   | `run()` 开头 | `before_run` (新增事件) | ctx |
   | 每次迭代开始 | `before_iteration(ctx)` | ctx |
   | 工具执行前 | `before_execute_tools(ctx)` | ctx |
   | 工具执行后 | `after_tool_executed(ctx, tc, result)` | ctx, tool_call, result |
   | 工具执行失败 | `on_tool_error(ctx, tc, error)` | ctx, tool_call, error |
   | 迭代结束 | `after_iteration(ctx)` | ctx |
   | `run()` 结束 | `after_run` (新增事件) | ctx, result |

   **注意：** `before_run` / `after_run` 是 `AgentHook` 新增的两个方法（在 `composite.py` 中）。

6. **AgentHook 新增两个方法**（`composite.py`）
   ```python
   class AgentHook:
       def before_run(self, ctx: AgentHookContext) -> None:
           pass
       def after_run(self, ctx: AgentHookContext, result: Any) -> None:
           pass
   ```

   `CompositeHook` 同步新增对应的 async 方法。

#### 关键代码片段

```python
# loop.py run() 方法中
def run(self, task: str, *, context: str | None = None) -> LoopResult:
    # ... existing code ...
    
    # P2-b: Hook integration
    hook_ctx = self._build_hook_context(0, messages)
    self._fire_hooks("before_run", hook_ctx)
    
    for iteration in range(1, self.max_iterations + 1):
        result.iterations = iteration
        hook_ctx = self._build_hook_context(iteration, messages)
        
        self._fire_hooks("before_iteration", hook_ctx)
        
        # ... compression ...
        # ... LLM call ...
        # ... tool execution ...
        
        for tc in response.tool_calls:
            # before tool
            self._fire_hooks("before_execute_tools", hook_ctx)
            
            tool_result_msg = self._execute_tool_with_heartbeat(tc, result)
            
            # after tool
            self._fire_hooks("after_tool_executed", hook_ctx, tc, tool_result_msg)
            
            messages.append(tool_result_msg)
        
        self._fire_hooks("after_iteration", hook_ctx)
    
    self._fire_hooks("after_run", hook_ctx, result)
    # ... existing git commit ...
    return result
```

---

### 1.2 SessionMemoryHook 增强

**文件：** `src/strategy_research/core/hooks/bundled/session_memory.py`

#### 改动内容

1. **`__init__` 新增参数**
   ```python
   session_manager: SessionManager | None = None,
   ```

2. **`archive_session()` 增强**
   - 原逻辑：写 Markdown 文件到 `<workspace>/memory/`
   - 新增：同时调用 `session_manager.add_message_batch()` 写入 SQLite

   ```python
   def archive_session(self, session_id: str | None = None) -> Path | None:
       # ... existing Markdown write ...
       
       # P2-b: 同时写入 SessionDB
       if self._session_manager and session_id and self._pending_archive:
           for msg in self._pending_archive:
               self._session_manager.add_message(
                   session_id=session_id,
                   role=msg.get("role", "unknown"),
                   content=msg.get("content", ""),
               )
       
       self._pending_archive.clear()
       return filepath
   ```

---

### 1.3 ContextBuilder 跨会话记忆

**文件：** `src/strategy_research/core/agent/context.py`

#### 改动内容

1. **`__init__` 新增参数**
   ```python
   session_manager: SessionManager | None = None,
   ```

2. **`_recall_relevant()` 增强**
   - 原逻辑：仅查询 `PersistentMemory.find_relevant(task)`
   - 新增：同时查询 `SessionDB.search_messages(task)`
   - 合并去重后返回

   ```python
   def _recall_relevant(self, task: str, max_entries: int = 5) -> str:
       entries = []
       
       # 1. PersistentMemory (workspace-level)
       if self.memory:
           entries.extend(self.memory.find_relevant(task, max_results=max_entries))
       
       # 2. SessionDB (cross-session) — P2-e
       if self._session_manager:
           try:
               session_results = self._session_manager.search_messages(task, limit=max_entries)
               for r in session_results:
                   entries.append(f"- [{r.get('role', '?')}] {r.get('content', '')[:200]}")
           except Exception:
               pass  # FTS5 可能不可用
       
       if not entries:
           return ""
       lines = ["<recalled-memories>"]
       for e in entries[:max_entries]:
           lines.append(f"- {e}" if isinstance(e, str) else f"- {e}")
       lines.append("</recalled-memories>")
       return "\n".join(lines)
   ```

---

### 1.4 CLI Session 命令补全

**文件：** `src/strategy_research/cli.py`

#### 新增 3 个子命令

| 命令 | 函数 | 功能 |
|------|------|------|
| `session show <id>` | `cmd_session_show(args)` | 显示会话消息列表（时间、角色、内容前 100 字） |
| `session search <query>` | `cmd_session_search(args)` | FTS5 全文搜索，显示匹配消息 + 所属会话 |
| `session delete <id>` | `cmd_session_delete(args)` | 删除会话及其消息 |

#### argparse 注册

```python
session_show_p = session_sub.add_parser("show", help="显示会话详情")
session_show_p.add_argument("session_id", help="会话 ID")

session_search_p = session_sub.add_parser("search", help="搜索消息")
session_search_p.add_argument("query", help="搜索关键词")
session_search_p.add_argument("--limit", type=int, default=20)

session_delete_p = session_sub.add_parser("delete", help="删除会话")
session_delete_p.add_argument("session_id", help="会话 ID")
```

---

### 1.5 测试

#### 新增文件

| 文件 | 测试数 | 覆盖内容 |
|------|--------|---------|
| `tests/test_session_memory.py` | 8+ | SessionMemoryHook 归档、SessionDB 写入、文件格式 |
| `tests/test_p2_e2e.py` | 8+ | AgentLoop+Hook 集成、跨会话记忆召回、CLI session 命令 |

#### 修改文件

| 文件 | 改动 |
|------|------|
| `tests/test_agent_loop.py` | 新增 hook 集成测试（before_iteration 被调用、after_tool_executed 收到结果） |

---

## 阶段 2：P5-Skills 系统

### 2.1 Skills 加载器

**新建文件：**

| 文件 | 内容 |
|------|------|
| `core/skills/__init__.py` | 导出 |
| `core/skills/models.py` | `Skill` dataclass（name, category, description, tags, path, content） |
| `core/skills/loader.py` | `SkillLoader`：YAML frontmatter + Markdown body 解析 |
| `core/skills/registry.py` | `SkillRegistry`：按 category 索引、搜索、加载 |

#### SkillLoader 解析格式

```yaml
---
name: factor-research
category: strategy
description: 因子研究方法论
tags: [factor, alpha, research]
---
# 因子研究方法论

## 1. 因子构建
...
```

#### SkillRegistry API

```python
class SkillRegistry:
    def load_directory(self, path: Path) -> int  # 加载目录下所有 SKILL.md
    def get(self, name: str) -> Skill | None
    def list_all(self) -> list[Skill]
    def search(self, query: str) -> list[Skill]  # 按 name/description/tag 搜索
    def by_category(self, category: str) -> list[Skill]
```

### 2.2 迁移 templates → skills

- 将 `templates/.skills/*.md` 迁移为标准 SKILL.md 格式（加 YAML frontmatter）
- 保留 `templates/.prompts/*.md` 不动（prompts 不迁移）
- `cmd_init` 改为从 `skills/` 目录复制到 workspace

### 2.3 Skills CLI

| 命令 | 功能 |
|------|------|
| `skills list` | 列出所有 skill（name + category + description） |
| `skills show <name>` | 显示 skill 完整内容 |
| `skills search <query>` | 按 tag/category/description 搜索 |

### 2.4 Agent 集成

- `ContextBuilder`：system prompt 中注入可用 skills 列表摘要
- 新增 `LoadSkillTool`：agent 可按名加载 skill 内容

### 2.5 测试

| 文件 | 测试数 |
|------|--------|
| `tests/test_skills_loader.py` | 10+ |
| `tests/test_skills_registry.py` | 8+ |
| `tests/test_skills_cli.py` | 6+ |

---

## 阶段 3：P5-Swarm 多智能体

### 3.1 Swarm Runtime

**新建文件：**

| 文件 | 内容 |
|------|------|
| `core/swarm/__init__.py` | 导出 |
| `core/swarm/runtime.py` | `SwarmRuntime`：DAG 调度 + ThreadPoolExecutor worker 派发 |
| `core/swarm/worker.py` | `WorkerLoop`：mini-ReAct，接收 pre-fetched market data |
| `core/swarm/grounding.py` | `DuckDBGroundingProvider`：DuckDB-backed 具体实现 |
| `core/swarm/preset_loader.py` | YAML preset 加载器 |

#### SwarmRuntime API

```python
class SwarmRuntime:
    def __init__(self, controller: WorkflowController, grounding: GroundingProvider | None = None)
    def load_preset(self, path: Path) -> SwarmPreset
    def execute(self, preset: SwarmPreset, workspace: Path, task: str) -> SwarmResult
    def cancel(self, run_id: str) -> bool
```

#### SwarmPreset 数据模型

```python
@dataclass
class SwarmPreset:
    name: str
    description: str
    agents: list[AgentCall]  # 复用 workflow.types.AgentCall
    dag: dict[str, list[str]]  # 依赖关系
```

### 3.2 Swarm Presets

**新建目录：** `core/swarm/presets/`

| Preset 文件 | 描述 |
|-------------|------|
| `quant_research_team.yaml` | 量化研究组（researcher → factor_analyst → strategist） |
| `risk_committee.yaml` | 风控委员会（risk_controller → attribution → anti_overfit） |
| `full_pipeline.yaml` | 完整 10-agent pipeline（所有角色） |

#### Preset YAML 格式

```yaml
name: quant_research_team
description: 量化研究团队 — 因子发现到策略优化
agents:
  - id: researcher
    prompt_file: .prompts/researcher.md
    tools: [read_file, compute_factor]
  - id: factor_analyst
    prompt_file: .prompts/factor_analyst.md
    input_from: [researcher]
    tools: [compute_factor, run_backtest]
  - id: strategist
    prompt_file: .prompts/strategist.md
    input_from: [factor_analyst]
    tools: [read_file, write_file, run_backtest]
dag:
  researcher: []
  factor_analyst: [researcher]
  strategist: [factor_analyst]
```

### 3.3 Swarm CLI

| 命令 | 功能 |
|------|------|
| `swarm list` | 列出所有 preset |
| `swarm inspect <name>` | 显示 preset DAG 结构（agent 依赖图） |
| `swarm run <name> --workspace <path>` | 执行 swarm preset |
| `swarm cancel <run_id>` | 取消运行中的 swarm |

### 3.4 集成到 autoresearch

- `cli.py` 的 `cmd_autoresearch` 改为通过 `SwarmRuntime` 调度
- 保留串行 fallback（Swarm 不可用时降级到单 agent loop）

### 3.5 测试

| 文件 | 测试数 |
|------|--------|
| `tests/test_swarm_runtime.py` | 10+ |
| `tests/test_swarm_worker.py` | 6+ |
| `tests/test_swarm_preset_loader.py` | 6+ |
| `tests/test_swarm_cli.py` | 6+ |

---

## 阶段 4：P5-MCP Server

### 4.1 MCP Server 核心

**新建文件：**

| 文件 | 内容 |
|------|------|
| `core/mcp/__init__.py` | 导出 |
| `core/mcp/server.py` | FastMCP server（research-only） |
| `core/mcp/tools.py` | MCP tool wrappers（包装 BaseTool → MCP 协议） |
| `core/mcp/auth.py` | Simple API key auth |

#### MCP Server 配置

- Server name: `"strategy-research"`
- Transport: stdio 默认 / `--transport sse --port 8900`
- 全部 research-only（无下单/撤单工具）

### 4.2 MCP 工具集（20 个 research-only 工具）

| 类别 | 工具名 | 功能 |
|------|--------|------|
| Skill | `list_skills` | 列所有 skill |
| Skill | `load_skill` | 按名加载 skill 内容 |
| Goal | `start_research_goal` | 创建研究目标 |
| Goal | `get_research_goal` | 获取当前目标 |
| Goal | `add_goal_evidence` | 追加 evidence |
| Hypothesis | `list_hypotheses` | 列出假说 |
| Hypothesis | `update_hypothesis` | 更新假说状态 |
| Backtest | `run_backtest` | 执行回测 |
| Backtest | `validate_run` | 验证回测结果 |
| Factor | `compute_factor` | 计算因子值 |
| Factor | `factor_analysis` | 因子 alpha 分析 |
| Memory | `search_memory` | 搜索记忆 |
| Memory | `add_memory` | 添加记忆 |
| Session | `list_sessions` | 列出会话 |
| Session | `search_messages` | 搜索消息 |
| Workflow | `list_swarm_presets` | 列出 swarm preset |
| Workflow | `run_swarm_preset` | 执行 swarm preset |
| Data | `list_data_sources` | 列出可用数据源 |
| Data | `import_data` | 导入数据 |
| System | `preflight_check` | 环境检查 |

### 4.3 CLI 入口

| 命令 | 功能 |
|------|------|
| `mcp serve --transport stdio\|sse --port 8900` | 启动 MCP server |
| `mcp list-tools` | 列出所有 MCP 工具 |

### 4.4 测试

| 文件 | 测试数 |
|------|--------|
| `tests/test_mcp_server.py` | 8+ |
| `tests/test_mcp_tools.py` | 10+ |

---

## 验收矩阵

| 指标 | 当前 | 阶段 1 后 | 阶段 2 后 | 阶段 3 后 | 阶段 4 后 |
|------|------|----------|----------|----------|----------|
| Hook 系统 | 存在但死代码 | **接入 AgentLoop** | **接入 AgentLoop** | **接入 AgentLoop** | **接入 AgentLoop** |
| 跨会话记忆 | 无 | **SessionDB.search 集成** | **SessionDB + Skills** | **SessionDB + Skills** | **SessionDB + Skills** |
| CLI session 命令 | list/stats | **+show/search/delete** | **+show/search/delete** | **+show/search/delete** | **+show/search/delete** |
| Skills 系统 | 无 | 无 | **Loader + Registry + CLI** | **Loader + Registry + CLI** | **Loader + Registry + CLI** |
| Swarm 多智能体 | 无 | 无 | 无 | **Runtime + 3 presets + CLI** | **Runtime + 3 presets + CLI** |
| MCP Server | 无 | 无 | 无 | 无 | **20 tools + CLI** |
| 测试数 | 5050 | ~5075 | ~5100 | ~5130 | ~5150 |
| 新增行数 | — | ~800 | ~600 | ~1200 | ~800 |

---

## 执行顺序

```
阶段 1: P2-b+P2-e (Week 1-2)
    ├── 1.1 AgentLoop Hook 集成
    ├── 1.2 SessionMemoryHook 增强
    ├── 1.3 ContextBuilder 跨会话记忆
    ├── 1.4 CLI session 命令补全
    └── 1.5 测试
    ↓
阶段 2: P5-Skills (Week 3)
    ├── 2.1 Skills 加载器
    ├── 2.2 迁移 templates → skills
    ├── 2.3 Skills CLI
    ├── 2.4 Agent 集成
    └── 2.5 测试
    ↓
阶段 3: P5-Swarm (Week 4-5)
    ├── 3.1 Swarm Runtime
    ├── 3.2 Swarm Presets
    ├── 3.3 Swarm CLI
    ├── 3.4 集成到 autoresearch
    └── 3.5 测试
    ↓
阶段 4: P5-MCP (Week 6)
    ├── 4.1 MCP Server 核心
    ├── 4.2 MCP 工具集
    ├── 4.3 CLI 入口
    └── 4.4 测试
```

每个阶段完成后运行全量测试确认无回归，按阶段 PR 交付。

---

## 风险与回退

| 风险 | 概率 | 对策 | 回退 |
|------|------|------|------|
| async/sync 桥接性能问题 | 中 | 用 `asyncio.new_event_loop()` 隔离 | 降级到 sync hook（重写 CompositeHook） |
| SessionDB FTS5 中文分词差 | 中 | 简单空格分词 | 回退到 token matching |
| Swarm DAG 调度死锁 | 低 | timeout + cycle detection | 降级到串行执行 |
| MCP 工具数量膨胀 | 中 | 仅暴露 research-only 工具 | 砍掉低频工具 |
| Skills 迁移破坏现有 templates | 低 | 保留 templates/ 不动，skills/ 独立目录 | 回退到 templates/ |
