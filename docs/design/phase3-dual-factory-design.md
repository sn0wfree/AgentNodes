# Phase 3 设计：双工厂收口（chat_loop ↔ role_factory）

> 来源：`refactor-plan-20261008.md` Phase 3（切入点 C）
> 语义重估：两入口**服务不同领域**——role_factory = 研究角色（9 角色研究链，策略/hypothesis）；chat_loop = 交互式 chat（含 DAG orchestrator 模式、permission 门）。合并为单签名 = 参数面 union（丑且脆弱）。
> **真正漂移源**：AgentLoop 的**装配逻辑**（registry/prompt/strategy 解析双份手工组装）。收口目标改为「**单一装配源**」，不强行统一入口签名。
> 范围：`core/agent/role_factory.py`（抽装配核）· `core/agent/chat_loop.py`（改调装配核）· 公共签名不变

## 1. 现状（差异自述 + 读码补充）

| 差异点 | `role_factory.build_agent_loop` | `chat_loop.build_chat_agent_loop` |
|---|---|---|
| 定位 | 研究角色（9 角色） | 交互式 chat / DAG orchestrator |
| max_iterations | 8 | 1 |
| stream_mode | 默认 | 强制 True |
| 工具白名单 | `_get_tool_whitelist(role)` | 全 registry + `allow_shell_tools` 门 |
| **DAG 模式** | 无 | `session_id` 前缀 `dag:` → 只留 `submit_dag_step` |
| loop_strategy | spec 解析（`resolve_loop_strategy`） | 未暴露 |
| system_prompt | `PromptBuilderFactory.get(role)` | 同 + `extra_context`/`system_prompt_override` |
| 其它 | `strategy_dir/runs_dir/write_roots` 等研究语义 | `on_event/event_bus/permission_*/compact_config` 等 chat 语义 |

**漂移热点**（真问题）：registry 构建（两侧都有 `build_default_registry` try/except）、prompt 渲染（`PromptBuilderFactory` + context 组装）、`AgentLoop(...)` 构造参数装配——3 处双份手工。

## 2. 方案

### 2.1 抽「装配核」：`core/agent/loop_assembly.py`（新）

```python
@dataclass
class LoopAssembly:
    """Resolved pieces shared by both factories (phase 3)."""
    registry: Any
    system_prompt: str | None
    loop_strategy: Any = None

def assemble_loop_parts(
    *, role: str, registry: Any, extra_context: dict | None,
    system_prompt_override: str | None = None, workspace: Path | None = None,
    loop_strategy: Any | None = None,
) -> LoopAssembly:
    """Single source for registry defaulting + prompt rendering + strategy resolve.

    Both build_agent_loop and build_chat_agent_loop call this instead of
    hand-assembling the three pieces (the drift source).
    """
```

**不含**：参数面 union（各工厂的特有参数留在各自签名）——只是把「双份手工」变「单份」。

### 2.2 两工厂改造

- `build_agent_loop`：registry/prompt/strategy 段替换为 `assemble_loop_parts(...)` 调用；签名**完全不变**
- `build_chat_agent_loop`：同上；DAG 模式/shell 门/chat-specific 保留
- **`AgentLoop(...)` 构造的最后一公里也统一**？——两侧构造参数差异大（`stream_mode`/`session_id`/`permission_*`）——**保留各自构造调用**，只统一「三件套解析」

## 3. 验收

| # | 项 | 条件 |
|---|---|---|
| 1 | 双工厂签名 | 不变（调用方零改动） |
| 2 | 装配核单源 | `assemble_loop_parts` 被两工厂引用；prompt/strategy 解析无双份 |
| 3 | 测试 | `test_chat_loop` · `test_role_factory` · `test_role_factory_loop_strategy` · `test_streaming_chat_path` · `test_prompt_builder*` 全绿 |
| 4 | 派生测试面 | `rg -l "build_chat_agent_loop|build_agent_loop|role_factory" tests` 一次跑完 |
| 5 | 69 回归 + ruff src=0 | 保持 |

## 4. 边界

- **不合并签名**（Phase 3 允许的偏离，理由见语义重估）
- **不动调用方**（service.py/tui/chat.py 五处零改）
- DAG 模式 / permission 门 / claim 校验 flag 行为不变
