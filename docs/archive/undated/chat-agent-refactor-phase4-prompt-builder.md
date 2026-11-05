# Phase 4: PromptBuilder 重构设计与实施

> 首个落地模块：`PromptBuilder` 三合一（chat.py / session.py / role_factory 三套并行 system_prompt 加载路径合一）。

## 1. 为什么选 PromptBuilder

| 理由 | 说明 |
|---|---|
| **DRY 违背最严重** | 3 套并行 system_prompt 加载路径（chat.py / cli/tui / role_factory） |
| **依赖最少** | 只依赖 `AgentState` + `Message` 值对象 |
| **价值高** | 所有 chat/goal 路径都要用它 |
| **风险低** | 纯函数，无副作用，无 I/O |
| **可独立交付** | 不依赖其他接口实施 |

## 2. 接口契约（来自 Phase 2）

```python
class PromptBuilder(Protocol):
    def build_system_prompt(self, role: str, context: dict) -> str: ...
    def build_messages(
        self, user_query: str, history: list[Message], context: dict
    ) -> list[Message]: ...
    def estimate_tokens(self, messages: list[Message]) -> int: ...
    def validate(self, messages: list[Message]) -> ValidationResult: ...
```

## 3. 实施策略：策略模式 + Jinja2 模板

### 3.1 实现类

| 类 | 角色 | 输出格式 |
|---|---|---|
| `ChatPromptBuilder` | 对话模式 | 自然语言 |
| `ResearcherPromptBuilder` | 研究者（Goal） | 结构化 JSON |
| `StrategistPromptBuilder` | 策略师（Goal） | 结构化 JSON |
| `DataQualityPromptBuilder` | 数据质量（Goal） | 结构化 JSON |
| `FactorAnalystPromptBuilder` | 因子分析（Goal） | 结构化 JSON |
| `PromptBuilderFactory` | 工厂分发 | — |

### 3.2 模板文件

```
core/agent/templates/
├── chat.md.j2          # 对话 prompt
├── researcher.md.j2    # 研究者 prompt
├── strategist.md.j2    # 策略师 prompt
├── data_quality.md.j2  # 数据质量 prompt
└── factor_analyst.md.j2 # 因子分析 prompt
```

### 3.3 设计要点

1. **从硬编码字符串 → Jinja2 模板**：每个角色独立模板，便于维护和 A/B 测试
2. **`estimate_tokens` 统一实现**：4 chars ≈ 1 token 的估算（与现有 `_estimate_chars` 一致）
3. **`validate` 检查 token 上限**：默认 128K（可配置）
4. **`build_messages` 统一组装**：system + history + current query

## 4. 现有调用点替换

| 现有路径 | 替换为 |
|---|---|
| `chat.py:_get_system_prompt()` | `ChatPromptBuilder().build_system_prompt("chat", context)` |
| `session.py:_run_agent_loop` (硬编码 prompt 读取) | `ChatPromptBuilder().build_system_prompt("chat", context)` |
| `role_factory._load_role_system_prompt(role)` | `PromptBuilderFactory.get(role).build_system_prompt(role, context)` |

## 5. 实施步骤（7 步）

| 步骤 | 内容 | 改动量 |
|---|---|---|
| 1 | 新建 `core/agent/prompt_builder.py` | ~150 行 |
| 2 | 新建 `core/agent/templates/chat.md.j2` | ~10 行 |
| 3 | 新建 `core/agent/templates/researcher.md.j2` | ~15 行 |
| 4 | 改 `chat.py:_get_system_prompt` → 用 `ChatPromptBuilder` | ~20 行 |
| 5 | 改 `session.py:_run_agent_loop` system_prompt 解析 | ~15 行 |
| 6 | 新建 `tests/test_prompt_builder.py` | ~100 行 |
| 7 | 旧 `_get_system_prompt` 标注 deprecated（门面模式） | ~5 行 |

## 6. 单元测试边界点（10 个）

| # | 测试 | 边界条件 |
|---|---|---|
| 1 | `test_chat_builder_empty_history` | history=[]，user_query="hi" → 系统+用户 2 条 |
| 2 | `test_chat_builder_long_history` | history 1000 条 → messages 数量正确 |
| 3 | `test_chat_builder_special_chars` | 含 `"`, `\n`, `\t` → jinja2 正确转义 |
| 4 | `test_chat_builder_unicode` | 含中文/emoji → 正确渲染 |
| 5 | `test_chat_builder_template_missing` | 模板文件不存在 → 抛 FileNotFoundError |
| 6 | `test_chat_builder_validate_ok` | tokens < 128K → ok=True |
| 7 | `test_chat_builder_validate_overflow` | tokens > 128K → ok=False + error 消息 |
| 8 | `test_researcher_builder_includes_criteria` | criteria=[a,b,c] → 模板渲染包含 |
| 9 | `test_factory_unknown_role` | role="unknown" → ValueError |
| 10 | `test_factory_returns_correct_builder` | role="chat" → ChatPromptBuilder 实例 |

## 7. 风险表

| 风险 | 影响 | 概率 | 缓解 |
|---|---|---|---|
| 旧 `_get_system_prompt` 的 fallback 行为丢失 | 低 | 中 | 保留 fallback 逻辑在 ChatPromptBuilder 内部 |
| Jinja2 模板与原硬编码 prompt 不一致 | 中 | 低 | 复制现有 prompt 文本到模板，对照测试 |
| 角色名与 PromptBuilderFactory key 不一致 | 低 | 低 | 用 role_factory 已有的 9 角色名 |
| ChatPromptBuilder 引入新依赖（jinja2） | 低 | 低 | 检查 pyproject.toml 是否已有 jinja2 |

## 8. 提交策略

| Commit | 范围 | 信息 |
|---|---|---|
| 1/2 | 4 个 docs | `docs(chat-agent): 4 阶段重构框架文档 — 架构总览 + Phase 1-4 设计` |
| 2/2 | prompt_builder.py + 2 模板 + test | `feat(chat): PromptBuilder 落地 — 三合一 system_prompt 加载路径` |

## 9. 后续阶段

| 阶段 | 任务 |
|---|---|
| Phase 5 | ChatAgentRunner 实施（基于 AgentRunner 协议） |
| Phase 6 | MemoryManager 三合一（chat.py / session.py / SQLite） |
| Phase 7 | BaseEventBus 实施（双 API：emit + astream） |
| Phase 8 | 鲁棒性机制落地（CircuitBreaker / ContextWindowGuard / StructuredOutputParser / ThreadSafeMemoryManager） |
