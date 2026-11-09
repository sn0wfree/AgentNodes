# 批次 13 附录 · 同一类问题（迁移后能力静默失效）的系统排查

> 触发：用户注意到 B3-S3 发现的「翻默认后能力静默消失而测试全绿」
> 可能不是孤例，要求先停下确认此前操作是否有同类问题。
> 结论：**是。共 5 个同类问题，其中 2 个是安全漏洞。**

---

## §0 判定标准（为什么这些能一直藏着）

四条共同特征，缺一不可地解释了「测试全绿但功能缺失」：

1. **实现只在一条路径上**（sync / async、或 loop / registry），另一条路径没有。
2. **缺失是静默的**：不抛异常，或异常被 `try/except` 吞掉。
3. **测试打的是有实现的那条路径**（或直接调裸方法），所以断言成立。
4. **没有覆盖「两条路径行为一致」的测试**——这正是批次 13 之前
   `test_skeleton_equivalence.py` 的处境（只比 5 个字段）。

以下每条都给出：实测证据、根因行号、影响面、修复建议。

---

## §1 【安全】sync 路径完全绕过权限闸

**实测**（`.tmp_s3/sync_permission_probe.py`，evaluator 明确 `shell=DENY`）：

| 路径 | 结果 |
|---|---|
| `_run_loop_core(async_mode=False)`（即 `run()`） | **`echo hi` 实际执行**，`exit_code: 0` |
| `_run_loop_core(async_mode=True)`（即 `arun()`） | 正确拒绝：`PermissionDeniedError: Permission denied: shell pattern=*` |

**根因**：
- `loop_tools.py:147` —`async_mode` 为真时走 `tool.ainvoke(kwargs, ctx)`，
  内部 `tools.py:651` 调 `_enforce_permission`。
- `loop_tools.py:149` —`async_mode` 为假时走 `tool.invoke(kwargs)`，
  **直调 `_dispatch`，无任何权限检查**（`tools.py:588-616`）。
- `BaseTool.enforce_permission_sync()`（`tools.py:731`）**存在**，
  但唯一的调用方是 `ToolRegistry.execute()`（`tools.py:909`），
  不是 AgentLoop 的 sync 工具路径。

**影响面**：`AgentLoop.run()` 全仓 **118 个调用点**
（`apps/goal/workflow_hook.py`、`apps/scheduled_research/*`、`evals/*`、
CLI/TUI、大量测试）。生产 web 走 `arun`（`service.py:993`）暂未中招，
但 CLI / scheduled_research / evals 全部裸奔。

**为什么 hard_deny 也没兜住**（`hard_deny_sync.py` 实测）：

| 用例 | sync | async |
|---|---|---|
| `sudo echo pwned`（H2） | **EXECUTED**（系统返回"已在此计算机上禁用 Sudo"） | BLOCKED |
| `cat ~/.ssh/id_rsa`（H3） | EXECUTED | EXECUTED（两条都漏） |

第二行是**独立缺陷**：`evaluator._extract_target()`（`evaluator.py:305-307`）
对 shell 只取命令首词 `cat`，H3 的 `**/.ssh/**` 模式永远匹配不上路径。
即「hard_deny 对 shell 内的路径词无感知」。

**修复**（两条独立）：
1. `_execute_tool_call_core` 的 sync 分支改为
   `tool.enforce_permission_sync(kwargs, kwargs.get("ctx"))` 后再 `tool.invoke`。
2. `_extract_target` 对 shell 增加「整条命令串」作为候选 target
   （hard_deny 侧对命令串做子串/分词匹配，而不是只比首词）。

---

## §2 【安全/权限】E1–E5 验收测试打的是另一条路径

`tests/test_capability_smoke.py:57` 的 `_run()` 用
`loop.registry.execute(name, params, ctx=ctx)`——内部走
`enforce_permission_sync`（`tools.py:909`）。

**它验证的不是生产路径**。生产是
`_execute_tool_call_core → tool.invoke/ainvoke`。因此：
- E1–E5 全绿 **不能** 证明「权限闸在工作」；
- §1 的漏洞在这个测试下**完全不可见**。

**修复**：E 系列改为经 `loop._execute_tool_call_core(tc, result, async_mode=...)`
执行，并把 sync/async 两个值都参数化。已有一个正确的先例：
`tests/test_kernel_standalone.py`（E6）走的是真实 `loop.run()`。

---

## §3 构造参数 `retry_policy` 完全无消费点

**实测**（`.tmp_s3/dead_fields2.py` + `spec_consumers.py`）：

- `loop.py:439` `self._retry_policy = _folded.capability.retry_policy or RetryPolicy()`
- 全仓 **0 个读取点**（属性访问与 `getattr` 字符串两种形式都查了）。
- 真正的重试用硬编码常量：`loop_tools.py:19-20`
  `_TOOL_MAX_RETRIES = 2`、`_TOOL_RETRY_DELAY = 2.0`。

**影响**：调用方传 `retry_policy=RetryPolicy(max_retries=5)` 完全无效，
且无警告。`RetryPolicy.should_retry()`（`circuit_breaker.py:186`）是死方法。

**修复**：`loop_tools._execute_tool_call_core` 读
`getattr(self, "_retry_policy", None)`，后退回模块常量。

---

## §4 `hypothesis.*` 两个 capability 已归档但仍有 resolve 调用点

**实测**（`.tmp_s3/hypothesis_probe.py`，导入 `agentnodes.apps.capabilities` 后）：

```
OK     goal.store
OK     goal.context
FAIL   hypothesis.registry        CapabilityNotFound
FAIL   hypothesis.auto_creator    CapabilityNotFound
FAIL   evolution.read_journal     CapabilityNotFound
```

调用点仍在：
- `context_injector.py:355` `resolve("hypothesis.registry")`
- `loop.py:1316` `resolve("hypothesis.auto_creator")`

**两处都包在 try/except 里**（:`352` / `:1313`），异常被吞
→ 静默失效。`enable_hypothesis_auto_create`（默认 **True**）
这个开关因此是**永不生效的**：它的消费点在
`_maybe_auto_create_hypothesis` 里，而该函数每次都走到 resolve 就抛。

`apps/capabilities.py:64-65` 的注释确认：「hypothesis 两个 kind 仍归档，
本轮不在范围内」——即已知的阶段性缺口，但**开关默认 True 且有消费点**
让调用方以为它在工作。

**修复**（择一）：
- (a) 恢复两个 capability 的注册（若 hypothesis 子系统仍要保留）；
- (b) 把 `enable_hypothesis_auto_create` 默认改为 `False`，
  并在文档里写明「hypothesis 归档期间该开关无效」。

---

## §5 `_approval_thread` 只赋值不使用

`loop.py:524` `self._approval_thread: threading.Thread | None = None`
——全仓 0 读取点。属清理项（无行为影响），可能是一次改动留下的残骸。

---

## §6 已确认**无**问题的项（避免重复排查）

| 检查项 | 结论 |
|---|---|
| `readonly` / `system_prompt` / `allowed_tools` | 仅构造期使用，正确（过滤 registry / 建 ContextBuilder） |
| 其余 35 个 LoopSpec 字段 | 均有真实消费点（`spec_consumers.py` 逐字段核对） |
| `_maybe_compact` / `_amaybe_compact` | 同一核心（`make_compactor`），语义一致 |
| `_stream_chat` / `_astream_chat` | 都调 `_effective_tools`，`force_final_text` 在两路都生效 |
| 工具结果 guardrail | 在 `_append_tool_results`，两引擎共用 |
| 权限闸接线（`chat_loop.py:160-188`） | 正常，E1 有断言 |

**已知的、公开的语义取舍**（不算缺陷）：
- `_with_iteration_timeout_sync` 只能事后测墙钟（blocking `client.chat`
  无法从同线程安全取消），async 可真正中断。注释已写明。
- 流式路径 `_trace_llm_request(..., tools=None)` 只影响 trace 可观测性，
  不影响实际传给 LLM 的 tools（`_stream_chat` 内部自己取
  `_effective_tools`）。

---

## §7 修复排序建议

| 优先级 | 问题 | 理由 |
|---|---|---|
| **P0** | §1 sync 绕过权限 + H3 shell 路径不匹配 | 安全，118 个调用点 |
| **P0** | §2 E1–E5 换成真实路径 | 否则 §1 修完也无法被回归测试守住 |
| P1 | §3 `retry_policy` 接线 | 配置项谎报 |
| P1 | §4 `hypothesis` 开关 | 开关谎报（默认 True） |
| P2 | §5 `_approval_thread` 清理 | 无行为影响 |

**共同教训**：这批问题的根因不是"某次改动写错了"，而是
**验收测试与被验对象不是同一条路径**。建议把「两条路径行为一致」
上升为可检查的门禁：新增 `tests/test_dual_path_parity.py` 的骨架
（sync vs async 执行同一工具调用，断言权限/事件/结果一致），
以及一个静态门禁：**构造参数必须有消费点**
（`scripts/arch/check_dead_params.py`）。
