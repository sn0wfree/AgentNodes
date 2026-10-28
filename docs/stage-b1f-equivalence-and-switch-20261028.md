# B1-f 全链路等价与开关 — 调查与结论 — 2026-10-28

> 调查结果：**B1-f 的「开关切换」实际等价于 P5-1c（删除旧 chat 分支）**，
> 而 P5-1c 已被用户挂起。本文记录边界，并把可做的部分（等价性验证）
> 与不可做的部分（删除）分开。

## 1. 开关的真实现状

`new_entry_enabled()`（`entry_adapters.py:22`）：

```python
value = os.environ.get("QNODES_NEW_ENTRY", "")
if value in ("0", "false", "no", "off"):
    return False
return True      # 默认 True — Gateway 路径是默认
```

即：**Gateway 已经是默认路径**，`QNODES_NEW_ENTRY=0` 是**回退到旧
SessionService 路径**的逃生舱。两个调用点：

| 位置 | 作用 |
|---|---|
| `api/routers/chat.py:845-847` | `/api/chat/send` 分流 |
| `cli/tui/session.py:236-238` | CLI 会话分流 |

## 2. 因此 B1-f 的真实内容 = P5-1c

「开关切换」在本项目里不是「把 chat 从 AgentLoop 切到 react」——
**chat 早就在 Gateway/react 路径上了**（`_build_engine` 用 react）。
开关切的是「Gateway 新入口 vs SessionService 旧入口」，而这正是
P5-1c（删旧 chat 分支）要做的事。

用户已明确：**P5-1c 挂起，业务稳定后再做**（`616085ee`）。

## 3. 可做 / 不可做

### 3.1 不可做（需用户解禁）

- 删除 `QNODES_NEW_ENTRY` 门与旧 SessionService 分支
- 删除 `_run_agent_loop_background` 等旧路径

### 3.2 可做（本轮）

**等价性验证**——证明 react 路径在剩余三个维度上与旧路径等价：

| # | 维度 | 验证方式 |
|---|---|---|
| 6 | 工具事件（tool.started/completed） | 已有 P5-1b 测试；补一条「事件序列与旧路径一致」 |
| 7 | 多轮对话（history 注入顺序） | 已有 P5-1 测试；补「历史在前、当前在后」断言 |
| 8 | 错误路径（LLM 失败/工具异常） | 补：LLM 抛错 → run.settled{ok:False}；工具异常 → 结构化 error |

## 4. 结论

**B1-f 拆成两半**：

- **B1-f-1（本轮做）**：补完 6-8 三项等价验证，形成完整证据链——
  证明 react 路径可以安全承载 chat；
- **B1-f-2（等 P5-1c 解禁）**：删开关与旧分支。

这样即使 P5-1c 继续挂起，B1 的价值（chat 全部能力已在 react 上且被
测试钉死）也已经完整交付。

## 5. B1 阶段总结

| 阶段 | 内容 | 状态 |
|---|---|---|
| B1-a | 系统提示装配核 | ✅ `2ad223a0` |
| B1-b | 上下文注入 | ✅ `2ad223a0` |
| B1-c | mid-loop 压缩 | ✅ `068472ce` |
| B1-d | 权限闸门（**修了真实漏洞**） | ✅ `90e306b2` |
| B1-e | inbox/hypothesis（**结论：不需移植**） | ✅ `2d1aa489` |
| B1-f-1 | 等价验证补完 | 本轮 |
| B1-f-2 | 删开关（= P5-1c） | ⏸ 等解禁 |
