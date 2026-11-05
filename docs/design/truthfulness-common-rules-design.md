# Truthfulness Common Layer Design

> **背景**: 量化研究助手在回测任务中出现"幻觉回测"——模型未调用 `run_backtest` 工具却声称已有结果。
> **目标**: 通过分层 prompt 架构 + 业界最佳实践，让 agent 在工具调用前/中/后都有明确的真实性约束。
> **范围**: L1 (prompt 抽象) 实施，L2 (claim_validator) 与 L3 (badge) 留待后续。

---

## 一、问题与根因

### 1.1 翻车案例

用户反馈："接着回测" → 模型编造 V1/V2 的回测指标（包括 sharpe、年化收益等具体数字），未实际调用 `run_backtest`。

### 1.2 根因分析

不是"工具不够"，是对话模式的激励错配：

| 层面 | 错误激励 | 应该有的激励 |
|---|---|---|
| 节奏 | "应该出结果" | 先确认工具调用，再出结果 |
| 数据观 | 编好看数字 | 数字必须来自工具返回值 |
| 专业感 | 编造更"专业" | 承认没跑更专业 |
| 默认假设 | 默认"已做" | 明确区分"已做" vs "将做" |

### 1.3 为什么纯 prompt 约束不够

业界共识（Anthropic、LangChain、Prompt Engineering Guide）：

- **System prompt 内的"禁止"是软约束**——压力下被流畅性压制
- **LLM 训练目标是"helpful"**——倾向于补全合理答案
- **结构性约束 > 文本约束**——需要 schema 验证、middleware 拦截等机制

→ 必须**多层防护**：L1 prompt + L2 验证 + L3 反馈。

本设计**只实施 L1**，L2/L3 留接口。

---

## 二、业界调研结论

调研 4 个核心来源，提炼 5 条共识：

### 2.1 Anthropic "think tool" 实验结论

> τ-bench airline 域：think tool + 优化提示 = **pass^1 0.584**，相比 baseline (0.332) 提升 **76%**。

**关键建议**：

> "When instructions were long and/or complex, including them in the system prompt was more effective than placing them in the tool description itself."

→ **复杂规则放 system prompt**（不是 tool description）。

→ **带示例比裸规则提升 76%**——必须配示例。

### 2.2 LangChain Middleware 架构

`create_agent` 五大 middleware，其中 Context management 直接对应我们的设计：

| LangChain | 我们的对应 |
|---|---|
| `MemoryMiddleware(sources=["./AGENTS.md"])` | `_common/principles.md` 常驻注入 |
| `SkillsMiddleware(sources=["./skills/"])` | `_common/rules/` 按需 `read_file` 读取 |

→ **业界已标准化"按需加载知识"模式**，我们的设计与之一致。

### 2.3 Chain-of-Thought Prompting

> "Let's think step by step" → 让模型在给出答案前显式推理。

→ **执行前自检 + 思考模式示例** 是 CoT 的具体应用。

### 2.4 Claude Sonnet 4.5 alignment 演进

> Sonnet 4.5 specifically reduces sycophancy, deception, power-seeking.

→ **诚实约束已是行业标准**，但需要 prompt + 模型能力共同支撑。

### 2.5 我们的创新点

- **可验证性约定**（`[来源: run_id=xxx]`）—— 业界无对应模式
- **强制回测流程**（4 步强制）—— 业界更松散
- **诚实模板**（具体句式）—— 比"be honest"更可操作

---

## 三、架构设计

### 3.1 三层 prompt 架构

```
┌─────────────────────────────────────────────────┐
│  principles.md（常驻，~400 tokens）               │
│  ─────────────────────────                       │
│  • 5 大原则（诚实/真实性/探究/简洁/受众区分）      │
│  • 红线（4 条，违反任何一条都是严重错误）          │
│  • 思考模式 + 诚实示例                            │
│  • 引导：完整方法见 _common/rules/INDEX.md        │
│  ─────────────────────────                       │
│  chat.md 或 <role>.md（角色层，常驻）             │
│  • chat.md：角色专属 + 高频规则内联（~600 tokens）│
│  • role.md：角色专属 + JSON 约定内联              │
│  ─────────────────────────                       │
│  _common/rules/*.md（按需读取，0 常驻）           │
│  • INDEX.md：元文件（何时读哪个 rule）            │
│  • backtest.md：回测流程详细版 + 示例             │
│  • tools.md：工具方法详细版                       │
│  • json-output.md：JSON 输出约定                 │
│  • iteration.md：小步迭代 + 自检示例             │
└─────────────────────────────────────────────────┘
```

### 3.2 注入策略

| 文件 | 注入策略 | Token 成本 |
|---|---|---|
| `principles.md` | **常驻**（所有角色顶层） | ~400 |
| `chat.md` | **常驻**（chat 模式） | ~600（含高频规则） |
| `<role>.md` | **常驻**（role 模式） | 角色相关（+JSON 内联） |
| `_common/rules/*.md` | **不注入**，按需 `read_file` 读取 | 0（按需） |

### 3.3 高频 vs 低频规则

| 规则 | 使用频率 | 放哪里 |
|---|---|---|
| 诚实模板 | **每轮** | chat.md 内联 |
| 强制回测流程 | **每轮**（涉及回测） | chat.md 内联 |
| 可验证性约定 | **每轮** | chat.md 内联 |
| 通用工具使用（精简版） | **每轮** | chat.md 内联 |
| 小步迭代原则 | **偶尔** | rules/iteration.md |
| 执行前自检 | **偶尔** | rules/iteration.md |
| 详细工具方法（算子/参数拆解） | **偶尔** | rules/tools.md |
| JSON 输出约定详细版 | **偶尔** | rules/json-output.md |

### 3.4 为什么 `rules/` 而非 `skills/`

未来会有独立的 `skills/` 功能（可能用于工具/能力描述），为避免概念冲突，现在的执行细则目录用 `rules/`。

---

## 四、文件清单

### 4.1 新建文件

```
src/strategy_research/templates/.prompts/_common/
├── principles.md               # 常驻（5 原则 + 红线 + 思考模式 + 示例）
└── rules/
    ├── INDEX.md                # 元文件：何时读哪个 rule
    ├── backtest.md             # 强制回测 4 步流程详细版
    ├── tools.md                # 通用工具使用详细方法
    ├── json-output.md          # JSON 输出约定详细版
    └── iteration.md            # 小步迭代 + 自检清单 + 自检示例

tests/
├── test_common_prompts.py      # principles/rules 注入验证
└── test_rules_injection.py     # rules/ 不注入、按需读取验证
```

### 4.2 修改文件

```
src/strategy_research/templates/.prompts/chat.md
# 顶部加指针 + 关键执行规则段（高频内联）+ rules 引用

src/strategy_research/templates/.prompts/{researcher,strategist,backtest_diagnostics,orchestrator,critic}.md
# 5 个 JSON role 顶部加指针 + JSON 约定内联 + rules 引用

src/strategy_research/templates/.prompts/{其余 14 个 role}.md
# 顶部加指针 + rules 引用

src/strategy_research/core/agent/prompt_builder.py
# _load_common 只注入 principles，rule 不注入
```

---

## 五、关键内容设计

### 5.1 `principles.md` 结构

```markdown
# 共识原则（所有角色必须遵守的底层价值观）

## 诚实
## 数据真实性
## 探究 > 假设
## 简洁
## 受众区分（按模式决定输出形态）
## ⚠️ 红线（违反任何一条都是严重错误）
## 思考模式（处理复杂工具调用前）
  → 含 2-3 个示例（来自翻车案例）
## 引导：执行方法去哪里找
  → 指明 _common/rules/INDEX.md
```

### 5.2 `rules/INDEX.md` 结构

```markdown
# Rule Index（何时读哪个 rule）

| 触发情况 | 读哪个 rule |
|---|---|
| 准备做回测 | backtest.md |
| 准备调用不熟悉的 tool | tools.md |
| 处理 role agent 的 JSON 输出细节 | json-output.md |
| 修复/优化迭代 | iteration.md |
| 不知道该读哪个 | 先读本文件 |
```

### 5.3 `chat.md` 关键段

```markdown
## 关键执行规则（高频，内联）

### 诚实模板（直接复制用）
### 强制回测流程
### 可验证性约定
### 工具使用（精简版）

> 低频规则（小步迭代 / 执行前自检 / 详细工具方法）见 `_common/rules/`。
```

### 5.4 5 个 JSON role 顶部指针

```markdown
> 遵守 `_common/principles.md`（价值观 + 红线，常驻上下文）。
> 本角色输出 JSON：**必须返回纯 JSON**，不包含任何其他文本、解释或 markdown 代码块标记，以 `{` 开头 `}` 结尾。字段缺失留 `null` 或 `"未测"`，不编造数字。
> 其他执行方法见 `_common/rules/`（按需 `read_file` 读取）。
```

### 5.5 14 个普通 role 顶部指针

```markdown
> 遵守 `_common/principles.md`（价值观 + 红线，常驻上下文）。
> 本角色专属工作流见下方。详细执行方法见 `_common/rules/`（按需 `read_file` 读取）。
```

---

## 六、prompt_builder.py 改动

### 6.1 现有结构（Phase 5）

```python
# prompt_builder.py:96 ChatPromptBuilder
# prompt_builder.py:168 StaticFilePromptBuilder
# prompt_builder.py:241 PromptBuilderFactory

def _load_common(role): ...  # 当前不存在，需新增
def _compose(...): ...        # 当前不存在，需新增
```

### 6.2 新增/修改

```python
# 新增路径常量
_COMMON_DIR = _PROMPTS_DIR / "_common"
_PRINCIPLES_FILE = _COMMON_DIR / "principles.md"

def _load_common(role: str) -> tuple[str, str, str]:
    """Load (principles, rule_injected, future).
    
    principle 常驻注入；rule 不注入，留给角色文件内联或 read_file 按需读取。
    """
    if role in PromptBuilderFactory._COMMON_OPT_OUT:
        return "", "", ""
    principles = _PRINCIPLES_FILE.read_text(encoding="utf-8") \
        if _PRINCIPLES_FILE.exists() else ""
    return principles, "", ""

def _compose(principles: str, rule: str, role_specific: str, future: str = "") -> str:
    parts = [p for p in (principles, rule, role_specific, future) if p]
    return "\n\n---\n\n".join(parts)
```

修改 `ChatPromptBuilder.build_system_prompt` 和 `StaticFilePromptBuilder.build_system_prompt`：

```python
def build_system_prompt(self, role, context):
    if not self._path.exists():
        return self.FALLBACK_PROMPT  # 或 ""
    text = self._path.read_text(encoding="utf-8")
    # ... 现有 placeholder 替换 ...
    top, middle, future = _load_common(role)
    return _compose(top, middle, text, future)
```

### 6.3 PromptBuilderFactory 改动

```python
class PromptBuilderFactory:
    _BUILDERS = {...}  # 现有
    _COMMON_OPT_OUT: set[str] = set()  # 新增，预留 opt-out 机制
```

---

## 七、测试设计

### 7.1 新增 `tests/test_common_prompts.py`

```python
def test_principles_file_exists():
    text = (_COMMON_DIR / "principles.md").read_text()
    assert "诚实" in text
    assert "数据真实性" in text
    assert "探究" in text
    assert "简洁" in text
    assert "受众区分" in text
    assert "⚠️ 红线" in text
    assert "思考模式" in text

def test_principles_injected_into_all_roles():
    for role in PromptBuilderFactory.list_roles():
        sp = PromptBuilderFactory.get(role).build_system_prompt(role, {})
        assert "共识原则" in sp, f"{role} 缺 principles"
        assert "⚠️ 红线" in sp, f"{role} 缺红线"
        assert "思考模式" in sp, f"{role} 缺思考模式"

def test_rules_directory_not_injected():
    """rules/ 不进 system prompt（按需读取）。"""
    sp = ChatPromptBuilder().build_system_prompt("chat", {})
    assert "Rule Index" not in sp
    assert "何时读哪个 rule" not in sp

def test_all_20_roles_point_to_rules_directory():
    role_files = [f for f in (_PROMPTS_DIR).glob("*.md") if not f.stem.startswith("_")]
    for f in role_files:
        text = f.read_text()
        assert "_common/rules/" in text or "_common/principles.md" in text

def test_chat_inlines_high_frequency_rules():
    chat_text = (_PROMPTS_DIR / "chat.md").read_text()
    assert "诚实模板" in chat_text
    assert "强制回测流程" in chat_text
    assert "可验证性约定" in chat_text
    assert "工具使用（精简版）" in chat_text

def test_chat_does_not_inline_low_frequency_rules():
    chat_text = (_PROMPTS_DIR / "chat.md").read_text()
    assert "小步迭代原则" not in chat_text
    assert "执行前自检" not in chat_text

def test_json_roles_have_inline_json_convention():
    json_roles = ["researcher", "strategist", "backtest_diagnostics", "orchestrator", "critic"]
    for role in json_roles:
        text = (_PROMPTS_DIR / f"{role}.md").read_text()
        assert "必须返回纯 JSON" in text, f"{role} 缺 JSON 约定"
```

### 7.2 新增 `tests/test_rules_injection.py`

```python
def test_rules_directory_exists():
    rules_dir = _COMMON_DIR / "rules"
    assert rules_dir.is_dir()
    assert (rules_dir / "INDEX.md").exists()
    assert (rules_dir / "backtest.md").exists()
    assert (rules_dir / "tools.md").exists()
    assert (rules_dir / "json-output.md").exists()
    assert (rules_dir / "iteration.md").exists()

def test_rules_index_has_trigger_table():
    text = (_COMMON_DIR / "rules" / "INDEX.md").read_text()
    assert "何时读哪个 rule" in text or "触发情况" in text
    assert "backtest.md" in text
    assert "tools.md" in text
    assert "json-output.md" in text
    assert "iteration.md" in text

def test_no_skills_directory_conflict():
    """未来 skills/ 目录不与 rules/ 冲突。"""
    skills_dir = _COMMON_DIR / "skills"
    assert not skills_dir.exists(), "skills/ 目录不应存在（保留命名空间）"

def test_each_rule_md_has_examples():
    """Anthropic 实验证明：带示例的规则比裸规则提升 76%。"""
    for rule_file in ["backtest.md", "tools.md", "json-output.md", "iteration.md"]:
        text = (_COMMON_DIR / "rules" / rule_file).read_text()
        # 至少一个示例（"例" 字符出现）
        assert "例" in text, f"{rule_file} 缺示例"
```

### 7.3 现有测试更新

- `tests/test_chat_loop.py:91/101/217/229/241` — system prompt 断言需要更新
  - 现状：检查 4 个 fake tools 名字出现
  - 影响：principles 注入后 system prompt 变长，但 fake tools 仍在 chat.md 中 → 断言可能仍过
  - 行动：先跑测试看影响，再决定是否更新

- `tests/test_role_factory.py:27` — 9 个 role 加载
  - 影响：所有 role 现在注入 principles 头部 → 内容更长
  - 行动：先跑测试看影响

---

## 八、迁移策略（灰度上线）

### 8.1 4 步独立可回滚

| 步 | 动作 | 验证 |
|---|---|---|
| **M1** | 新建 `_common/principles.md` + `_common/rules/*.md` + `prompt_builder.py` 改动 + 测试 | 跑通测试，所有 role system prompt 都含 principles，rules/ 不注入 |
| **M2** | chat.md：精简原重复段（6 行）+ 顶部指针 + 关键执行规则段内联 | chat 测试通过，骨架不变 |
| **M3** | 19 个 role：顶部加指针（含 5 个 JSON role 的 JSON 约定） | 全 role 验证 |
| **M4** | 手工跑 chat + role 端到端，看输出无回归 | 抽样 1-2 个对话确认 |

每步独立 commit，可回滚。

### 8.2 不破坏现有行为

- chat.md 骨架不变（用户明确要求）
- 现有 4 个 fake tools 引用保留
- principles 注入是**新增**，不是替换
- rules/ 不注入，对现有对话**零影响**

---

## 九、风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| principles.md 注入后 system prompt 超 token 预算 | 长对话上下文爆炸 | principles ~400 tokens，可控；后续可监控 |
| 模型忽略 rules/ 引用从不 read_file | rules/ 失效 | 高频规则已内联到 chat.md，rules/ 是补充 |
| role 顶部指针措辞不当 | 模型误解 | 测试 + 抽样验证，必要时调整 |
| 现有测试断言破坏 | 集成回归 | 跑测试看影响，必要时更新断言 |
| principles.md 内容不准确 | 误导所有 role | 多人 review + 灰度上线 |

---

## 十、未实施项（L2/L3 留接口）

### 10.1 L2 claim_validator（未实施）

- 在 `core/agent/agent_loop.py` 加 hook
- 提取 assistant 消息数字 vs 本轮 tool_results 数字
- 不匹配 → 标记 `_claim_validation` metadata
- UI 读取 metadata 显示 verifiability badge

### 10.2 L3 verifiability badge（未实施）

- 在 `AssistantMessage.tsx` 加小圆点
- 读 `_claim_validation` 决定 🟢/🟡/🔴
- 在 `ContextPanel.tsx` 加 `StrategyFileSection` 显示实际策略文件 vs 声称的改动

### 10.3 L2/L3 后续 PR 计划

- **PR 1 (本设计)**: L1 prompt 抽象
- **PR 2 (后续)**: L2 claim_validator
- **PR 3 (后续)**: L3 badge + ContextPanel 增强

---

## 十一、参考文档

- Anthropic "think tool": https://www.anthropic.com/engineering/claude-think-tool
- LangChain agents docs: https://docs.langchain.com/oss/python/langchain/agents
- Chain-of-Thought Prompting: https://www.promptingguide.ai/techniques/cot
- Claude Sonnet 4.5 alignment: https://www.anthropic.com/news/claude-sonnet-4-5
- 现有 chat-agent-refactor-phase5-integration.md（系统背景）