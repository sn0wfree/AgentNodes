# Study 重试 + Round 编号 + Agent 循环防护 设计文档

> 日期: 2026-08-18
> 状态: 待实施
> 关联: `study-archive-objective-flow-redesign.md`、`agent-tools-reference.md`

## 1. 背景与问题

研究任务（Study）在长期运行中存在三类体验痛点：

1. **Round 编号重复** —— 重试后编号从 1 重新开始，导致 journal 中出现 `Round 1, Round 2, Round 3, Round 2` 这种重复
2. **Agent 持续失败** —— `strategist`/`factor_analyst`/`data_quality` 三个 agent 反复 `parse_failed`，整个 round 浪费
3. **无限循环风险** —— LLM 可能卡在重复调用相同工具的死循环里

本文档给出三组修复：

| 编号 | 需求 | 设计 |
|------|------|------|
| A | Round 编号 | `retry(mode="append")` 默认从 `current_round+1` 继续，保留所有历史 |
| B | Agent 失败自动恢复 | `ExplorerStrategy` (max_iter=50) + parse_failed 自动重试 + 指数回退 |
| C | 无限循环防护 | Approval Gate（opencode 风格）：连续 N 次相同工具调用前请求用户批准 |

---

## 2. 状态机扩展（A: Round 编号）

### 2.1 现有问题

```python
# 当前实现
def reset_round_counter(self, study_id, start_round=1):
    DELETE FROM study_rounds WHERE study_id=?   # 清空历史
    UPDATE studies SET current_round=1            # 强制重置
```

效果：journal.md 残留旧行 + DB 重新从 round 1 开始 → 出现重复编号。

### 2.2 新设计：双模式

| mode | 行为 | 适用场景 |
|------|------|----------|
| `append`（默认） | 不重置 `current_round`，继续 `current_round+1` | 想保留所有历史继续实验 |
| `restart` | 清空 `study_rounds`，从 round 1 开始 | 想完全重新做 |

### 2.3 API 改动

```python
# routers/study.py - StudyActionRequest
class StudyActionRequest(BaseModel):
    # ... existing fields ...
    # RETRY-only
    from_round: Optional[int] = None
    mode: Optional[str] = "append"  # "append" | "restart"
```

```python
# scheduler.py
def retry(self, study_id, *, from_round=None, mode="append"):
    if mode == "restart":
        self.store.reset_round_counter(study_id, mode="restart")
    # mode="append" 仅做状态转移，current_round 不变
    self.store.update_execution_status(study_id, StudyINTERRUPTED)
    await self.resume_interrupted(study_id)
```

```python
# store.py
def reset_round_counter(self, study_id, mode="append"):
    """mode="append": 不重置，只更新 heartbeat
    mode="restart": 清空历史，current_round=1"""
    if mode == "restart":
        DELETE FROM study_rounds WHERE study_id=?
        UPDATE studies SET current_round=1
    # else: 不动 current_round
```

### 2.4 前端 UI

"重试" 弹窗改为：

```
┌──────────────────────────────────────┐
│ ⚠ 重试模式选择                          │
│                                      │
│ ● 从下一轮继续（默认 · 保留历史）    │
│   Round N → Round N+1                │
│                                      │
│ ○ 从第 1 轮重试（清空历史）           │
│   删除所有旧 round 从头开始          │
│                                      │
│            [取消]   [确定]            │
└──────────────────────────────────────┘
```

---

## 3. Agent 失败自动恢复（B）

### 3.1 完整失败链路（已定位）

```
researcher agent → ✓ 有效 JSON
data_quality agent → ❌ parse_failed (max_iter=10)
factor_analyst agent → ❌ parse_failed (max_iter=10)
strategist agent → ❌ parse_failed (max_iter=10)
  ↓
backtest_result = {} (无 metrics)
  ↓
trades = 0 < 30 (hard threshold)
  ↓
verdict = "discard"
```

### 3.2 三层防护

#### 层 1：ExplorerStrategy 高迭代模式

`agent/strategy/explorer.py` 已实现 `max_iter=50, no_progress_window=5`：

```python
# runner.py - 替换硬编码 max_iterations=10
from agentnodes.core.agent.strategy.explorer import ExplorerStrategyFactory

loop_strategy = ExplorerStrategyFactory.create()
run_researcher_phase(..., max_iterations=50, loop_strategy=loop_strategy)
run_execution_phase(..., max_iterations=50, loop_strategy=loop_strategy)
run_evaluation_phase(..., max_iterations=50, loop_strategy=loop_strategy)
```

效果：给 LLM 5 倍迭代空间 + 1.67 倍容忍窗口。

#### 层 2：parse_failed 自动重试

```python
# runner.py - _run_one_round_impl
MAX_AGENT_RETRIES = 2
AGENT_BACKOFF_BASE = 5.0

for attempt in range(MAX_AGENT_RETRIES):
    exec_result = run_execution_phase(...)
    failed = _detect_parse_failed(exec_result)  # set of agent names
    if not failed:
        break
    if attempt < MAX_AGENT_RETRIES - 1:
        delay = AGENT_BACKOFF_BASE * (2 ** attempt)
        logger.warning(f"Round {round_num}: {failed} parse_failed, retry in {delay}s")
        time.sleep(delay)
    else:
        # 两次都失败，把这次 round 计入 early_stop_patience
        self._idle_rounds += 1
```

效果：LLM 临时抽风自动重试，不浪费整个 round。

#### 层 3：指数回退

`retry_agent_spawn` 改用 `RetryPolicy`：

```python
# autoresearch.py
from .agent.circuit_breaker import RetryPolicy

def retry_agent_spawn(spawn_fn, agent_name, max_retries=3):
    policy = RetryPolicy(
        max_retries=max_retries,
        base_delay=2.0,
        max_delay=30.0,
        jitter=True,
    )
    for attempt in range(1, policy.max_retries + 1):
        try:
            raw = spawn_fn()
            parsed = parse_agent_output(raw)
            if "error" not in parsed:
                return parsed
        except Exception:
            pass
        if attempt < policy.max_retries:
            time.sleep(policy.get_delay(attempt))
    return {"error": "parse_failed", ...}
```

延迟序列（base=2s, max=30s, ±50% 抖动）：
- attempt 1→2: 2s (1-3s)
- attempt 2→3: 4s (2-6s)
- attempt 3→4: 8s (4-12s)

### 3.3 Prompt 优化

5 个 agent prompt 强化 JSON 输出要求：

```markdown
## Output Format (CRITICAL)

Your response MUST be a single JSON object. Specifically:
- Start with `{` and end with `}` 
- NO markdown code fences (no ```json or ```)
- NO prose before or after the JSON
- All required fields MUST be present (use null for missing)

## Required Schema
```json
{
  "action": "<enum_value>",
  "hypothesis": "<text>",
  ...
}
```

## Self-Validation (MANDATORY)
Before returning:
1. Parse your output as JSON yourself
2. Verify all required fields exist
3. If invalid, FIX IT before returning
```

涉及文件：
- `templates/.prompts/strategist.md`
- `templates/.prompts/data_quality.md`
- `templates/.prompts/factor_analyst.md`
- `templates/.prompts/researcher.md`（一致性）
- `templates/.prompts/_common/rules/json-output.md`

---

## 4. 无限循环防护（C: Approval Gate）

### 4.1 opencode 风格

当 Agent 连续 N 次调用同一工具且输出相同时（不是真"卡住"，只是进度慢），弹窗请求用户批准继续，类似：

```
┌────────────────────────────────────────┐
│ ⚠ Agent 循环检测 │
├────────────────────────────────────────┤
│ Agent "strategist" 已连续调用 5 次       │
│ 相同工具（hash: a3f2...）且输出相同       │
│                                        │
│ 是否继续？ │
│ [批准继续] [中止该 Agent] [跳过本轮]      │
│                                        │
│ ⏱ 30 分钟无响应将默认继续               │
└────────────────────────────────────────┘
```

### 4.2 AgentLoop 新字段

```python
# agent/loop.py
class AgentLoop:
    def __init__(self, ...):
        # 现有
        self._recent_hashes: list[str] = []
        self._circuit_breaker: ToolLoopCircuitBreaker | None
        # 新增
        self._approval_event = threading.Event()  # 同步路径
        self._approval_response: str | None = None
        self._approval_timeout = 1800  # 30 分钟
        self._role: str | None = None  # agent 名
    
    def approve_loop(self, decision: str):
        """Frontend 触发：批准/拒绝 Agent 循环。"""
        self._approval_response = decision
        self._approval_event.set()
```

### 4.3 _check_no_progress 改造

```python
def _check_no_progress(self, tool_hashes, response, result, iteration):
    if not hashes_pre_recorded:
        self._recent_hashes.extend(tool_hashes)
        self._recent_hashes = self._recent_hashes[-self.no_progress_window:]
    
    if not self._detect_no_progress():
        return False
    
    # NEW: 在退出前请求用户批准
    self._emit("agent_approval_requested", {
        "agent": self._role,
        "tool_hash": self._recent_hashes[0],
        "window": self.no_progress_window,
        "iteration": iteration,
        "message": f"Agent 已连续调用相同工具 {self.no_progress_window} 次且输出相同",
    })
    
    self._approval_event.clear()
    self._approval_event.wait(timeout=self._approval_timeout)
    
    decision = self._approval_response
    self._approval_response = None
    
    if decision == "reject":
        result.finished_reason = "user_rejected"
        result.answer = f"User rejected agent loop at iteration {iteration}"
        # Emit loop_end with reason
        return True
    elif decision == "approved":
        # 用户批准 → 清空计数器，继续循环
        self._recent_hashes.clear()
        return False
    else:
        # 超时：默认继续（用户要求）
        logger.warning(f"Agent approval timeout ({self._approval_timeout}s), continuing")
        self._recent_hashes.clear()
        return False
```

### 4.4 后端 API

```python
# routers/study.py
class AgentApprovalRequest(BaseModel):
    decision: str  # "approved" | "reject"

@router.post("/{study_id}/agents/approve")
async def study_approve_loop(request, study_id, req):
    sched = _get_study_scheduler()
    sched.approve_loop(study_id, req.decision)
    return {"status": "ok", "study_id": study_id, "decision": req.decision}
```

```python
# scheduler.py
def approve_loop(self, study_id, decision):
    """Resolve the pending approval gate for an agent loop."""
    for sid, runner in list(self._active_executors.items()):
        if sid == study_id and hasattr(runner, 'agent_loop'):
            runner.agent_loop.approve_loop(decision)
            return True
    return False
```

### 4.5 前端 UI

监听 SSE `agent_approval_requested` 事件，弹出全局对话框：

```tsx
// webui/frontend/src/components/study/AgentApprovalDialog.tsx
export function AgentApprovalDialog() {
  const { data, isOpen, approve, reject } = useAgentApproval()
  if (!isOpen) return null
  return (
    <div role="dialog" className="fixed inset-0 z-50 ...">
      <div className="rounded-2xl border border-amber-600 bg-slate-900 p-6">
        <h2>⚠ Agent 循环检测</h2>
        <p>Agent "{data.agent}" 已连续调用 {data.window} 次相同工具...</p>
        <p className="text-xs">⏱ 30 分钟无响应将默认继续</p>
        <div className="flex gap-2">
          <button onClick={approve}>批准继续</button>
          <button onClick={reject}>中止该 Agent</button>
        </div>
      </div>
    </div>
  )
}
```

---

## 5. 实施计划

| 步骤 | 文件 | 内容 |
|------|------|------|
| A1 | `store.py` | `reset_round_counter(mode)` |
| A2 | `scheduler.py` | `retry(mode)` |
| A3 | `routers/study.py` | StudyActionRequest 加 mode |
| A4 | `StudyDetailPage.tsx` | 重试弹窗：单选切换 |
| B1 | `runner.py` | ExplorerStrategy 接入 |
| B2 | `runner.py` | parse_failed 重试 + 指数回退 |
| B3 | `autoresearch.py` | retry_agent_spawn 改用 RetryPolicy |
| B4 | `templates/.prompts/*.md` | JSON 输出强化 |
| C1 | `agent/loop.py` | Approval Gate 逻辑 |
| C2 | `routers/study.py` | /agents/approve 端点 |
| C3 | `scheduler.py` | approve_loop 转发 |
| C4 | `client.ts` | approveLoop API |
| C5 | `AgentApprovalDialog.tsx` | 新组件 |
| C6 | `StudyPage.tsx` | 全局挂载对话框 |
| T1 | `test_study_actions.py` | round 模式测试 |
| T2 | `test_runner_parse_failed.py` | parse_failed 重试 |
| T3 | `test_agent_approval.py` | approval gate |

**预计时间**：1.5-2 天

---

## 6. 风险点与缓解

| 风险 | 缓解措施 |
|------|----------|
| **Agent 死循环** | Approval Gate + CircuitBreaker + 指数回退三重防护 |
| **Agent 临时抽风浪费整个 round** | parse_failed → 自动重试 round，最多 2 次 |
| **修改 prompt 破坏其他场景** | 增量修改，保留向后兼容的 JSON 解析 |
| **LLM token 消耗增加** | max_iter=50 只在失败时触发；CircuitBreaker 强制停止 |
| **30 分钟默认继续可能掩盖 bug** | 日志记录 + study_journal 写入"agent approval timeout" |
| **多次重试导致 round 耗时翻倍** | 指数回退限制总耗时上限 ~15s |

---

## 7. 测试覆盖

- `tests/test_study_actions.py`：retry(mode=append/restart) 行为
- `tests/test_runner_parse_failed.py`（新）：parse_failed 重试触发
- `tests/test_agent_approval.py`（新）：approval gate 状态机
- `tests/test_retry_backoff.py`（新）：指数回退延迟序列
- 前端：`AgentApprovalDialog` 单元测试

---

## 8. 关联文档

- `docs/archive/undated/study-archive-objective-flow-redesign.md` —— Study 归档/修改目标/流程优化
- `docs/archive/undated/study-ui-improvement.md` —— Study UI 标签页/导航
- `docs/guides/agent-tools-reference.md` —— Agent 工具白名单
- `src/agentnodes/core/agent/circuit_breaker.py` —— CircuitBreaker + RetryPolicy 实现
- `src/agentnodes/core/agent/strategy/explorer.py` —— ExplorerStrategy 实现（已存在）