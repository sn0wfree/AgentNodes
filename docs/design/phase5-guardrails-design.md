# Phase 5-guardrails 设计：输入侧运行时注入守卫

> 来源：`refactor-plan-20261008.md` Phase 5（调研 #4：OpenAI Guardrails 双向守卫）
> 缺口：eval 题面有 6 个注入/safety 期望（`injection-ignore-previous` 等），**运行时零检测**——题面/实现不对称
> 现有相关：claim validation（output 数字对账）· workspace_tools 的 pathspec 参数 sanitize（工具参数面，非 LLM 内容）
> 范围：**观察级守卫**（observation first，与轨迹评估同哲学）· 落点 hooks（extension-points 红线：hooks 只观察）
> 状态：设计定稿，待实施

## 1. 缺口分析

| 面 | 现状 | 缺口 |
|---|---|---|
| 用户输入 | 无守卫 | "ignore previous instructions" / 角色越狱 / 机密诱导 |
| 工具返回 | 无守卫 | 工具结果里的 `<system>` 标签、指令覆盖、外泄模式 |
| LLM 输出 | claim validation（数字对账） | 有（output 面一个维度，非注入） |

**eval 题面对照**（`evals/golden_tasks.jsonl`）：`injection-ignore-previous`、`injection-tool-name`、`no-exfil`、`no-shell-exfil`、`no-file-delete`、`pub-agent-env-secret` —— **6 题期望 agent 拒绝/忽略注入**。运行时检测器是这些题面的**运行时伙伴**（题面验收 agent 行为，guardrail 监控注入特征本身）。

## 2. 设计

### 2.1 检测器：`core/agent/guardrails.py`（新）

```python
@dataclass
class GuardrailFinding:
    kind: str          # user_input | tool_result
    pattern: str       # matched pattern id
    severity: str      # info | warn | suspect
    snippet: str       # matched text (truncated)
    source_id: str     # session/tool_call id

def scan_user_input(text: str) -> list[GuardrailFinding]:
    """Pattern scan for LLM prompt-injection signatures in user text."""

def scan_tool_result(tool_name: str, text: str) -> list[GuardrailFinding]:
    """Pattern scan for injection / exfil signatures in tool outputs."""
```

**模式词表**（`GUARDRAIL_PATTERNS`，id 化便于 eval 关联）：

| id | 触发模式（regex 级） | 面 |
|---|---|---|
| `instruction_override` | `ignore (previous\|all) (instructions\|rules)`、`你不再受限`、`forget your` | user / tool |
| `role_takeover` | `<system>`、`you are now`、`act as the system` | user / tool |
| `secret_exfil` | `api[_-]?key`、`\.env`、`password`、`secret` 路径式请求 | user / tool |
| `shell_escape` | `rm -rf /`、`os\.system`、`eval\(`、`exec\(` | tool_result |

**误报纪律**：词表保守（低召回高精度）；`<system>`这类用户正常提及会报 `info`（不 `suspect`）——**分级降噪**。

### 2.2 集成：hooks（`core/hooks/composite.py::AgentHook` 新观察点）

```python
class AgentHook:
    ...
    def on_guardrail_finding(self, ctx, finding: GuardrailFinding) -> None:
        """Observation-only: log / emit. NEVER raises; guardrails are
        additive monitoring (red line in extension-points.md)."""
```

**触发点**（AgentLoop 内已有的观察面，不改控制流）：
- `_prepare_run` 后（用户消息入参）→ `scan_user_input`
- `_append_tool_results`（工具结果拼装）→ `scan_tool_result`

**emit 事件**：`guardrail.finding`（SSE + event_store）——`finding.to_dict()`。

### 2.3 渐进拦截策略（三档）

| 档 | 行为 | 触发条件 |
|---|---|---|
| **v1 observation**（本轮） | emit `guardrail.finding` 事件 + log，不改消息/不拦 | 首发 |
| v2 warn | 追加系统提示 `<guardrail-notice>`（agent 看到警惕） | 观察 2 周误报率 < 20% |
| v3 intercept | 命中 `suspect` 级消息**不进 LLM**（构造空回复） | 真实攻击事件后 |

**v1 是唯一本轮交付**——observation 先行，同轨迹评估/claim validation/health gate哲学。

## 3. 实施清单（0.5-1 天）

| 步 | 动作 | 验收 |
|---|---|---|
| 0.1 | `core/agent/guardrails.py`：`GuardrailFinding` + `GUARDRAIL_PATTERNS` + 两个 scan 函数 | 单元测试：4 类模式命中/不命中 |
| 0.2 | `AgentHook.on_guardrail_finding` + AgentLoop 集成（`_prepare_run` / `_append_tool_results` 挂点） | hook 触发测试；注入样例 emit finding 事件 |
| 0.3 | SSE `guardrail.finding` 事件（含 finding.to_dict） | event 序列测试 |
| 0.4 | **eval 题面验证**：`injection-ignore-previous` 等 6 题跑 eval，观察 guardrail 事件数（行为不改，题面仍绿） | 6 题 pass@0.7 不回归 |

## 4. 边界

- **观察级**（v1）不拦消息——eval 题行为不变（题面验收 agent 该忽略注入，guardrail 只监控）
- **不改 hook 红线**：guardrail hook 只 emit（`extension-points.md` §1.2 红线）
- **不进 claim validation**（不同维度：claim 是数字对账，guardrail 是注入检测）

## 5. 风险

- 误报噪音（用户正常提及 `<system>`/`secret`）→ `info` 级不 `suspect`；观察后调词表
- 轨迹 diff（Phase 0）确认 hook 触发不改 agent 行为（只 emit）——零漂移
