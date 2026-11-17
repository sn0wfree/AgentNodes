# SessionService 补测方案（覆盖率 53.5% → 目标 ≥85%）

> **性质**：实施设计（**未执行**）· **日期**：2026-11-17
> **目标**：`kernel/session/service.py`（497 语句 / 缺 219 行 / 53.5%）——全仓缺口最大的单体模块
> **上游**：R-014 收官后的覆盖补测批次（265f79c7 → d22c5677 两轮之后的第三批）
> **本文档只出方案，不动代码。**

---

## §0 结论摘要

219 缺失行归属 **16 个方法**，全部缺口可用**三层夹具**覆盖，不需要真实 LLM：

| 缺口带 | 方法（缺行）| 需要的夹具 |
|---|---|---|
| A 队列与生命周期 | `send_message` 尾(6) `mark/is_processing`(4) `get_available_actions`(8) `pause/resume`(5+5) `wait_for_attempt`(11) `cancel`(5) `cancel_session`(10) | **L0 真件组合**（tmp SQLite + 真 SessionStore + env 路径）|
| B 队列消费器 | `_process_session_queue`(16) | L0 + asyncio 任务编排 |
| C 执行链 | `_run_attempt`(35) `_run_with_agent`(5) `_run_with_guard`(6) `_run_test_script`(22) | **L1 fake loop** + 官方 `AGENTNODES_TEST_CHAT` 短路 |
| D LLM 链 | `compact_history`(29) `_extract_summary`(22) `_build_fallback_summary`(26) `emit`(5) `_signal_attempt_done`(1) | L2 config/client 注入 + 纯逻辑直测 |

合计 219 行。目标：**53.5% → ≥85%**（恢复 ~135 行；剩余为深度异常分支）。

---

## §1 勘察结论（全部实测可追溯）

1. **存在官方测试短路**：`_run_with_agent` L901——`AGENTNODES_TEST_CHAT=1` 时
   直接走 `_run_test_script`（脚本化 SSE 流、不调 LLM）。这是迁移时保留的
   官方钩子，**C 带的主力入口**。
2. `_run_attempt` 的 `cfg = LLMConfig.load()`（L614）是第一道分叉：
   `None → attempt.failed("LLM config unavailable")`（L619-626）。
   monkeypatch `LLMConfig.load` 即可覆盖该分支与正常分支。
3. `_run_with_guard`（L990+）是 DAG continuation guard：
   fake agent 的 `arun` 可编程「前 N 次以问句结束、第 N+1 次正常」→ 覆盖
   重试逻辑与上限；抛 RuntimeError → 上层转 failed dict（L974-975）。
4. `_process_session_queue`（L483-580）五分支：vanilla / attempt 消失 continue /
   CancelledError→queue_paused→resume / 异常吞噬 / **exit-vs-enqueue handoff**
   （finally 里重启消费者，L570-576）。
5. `store` 用**真 SessionStore**（tmp SQLite——前两轮补测已验证的模式），
   不 mock：SQL 语义保持真实；`event_bus` 用 MagicMock（只 emit 无断言需求时）
   或 list-collector（需要断言事件序列时）。
6. `_CHAT_ATTEMPT_TIMEOUT` 是模块级常量（env 驱动，L43）——超时测试用
   env + `importlib.reload`（`test_chat_timeout` 已有先例）或直接注入短值。

---

## §2 夹具设计（三层）

```
conftest 级（本文件内，不进全局 conftest）：

svc(tmp_path, monkeypatch)          → SessionService
  · AGENTNODES_SESSION_DB=tmp       （db 层生产解析链）
  · store = 真 SessionStore(tmp db)  event_bus = EventCollector(list)
  · permission_gateway=None          _QUEUE_LIMIT 不动

fake_loop(monkeypatch)              → 可编程 AgentLoop 替身
  · monkeypatch build_chat_agent_loop → lambda **kw: FakeAgent(script)
  · FakeAgent.arun(history, ...) 按脚本 yield/return
    （LoopResult 形状：answer/iterations/tool_calls_made/finished_reason/error）
  · FakeAgent.name = "fake"

llm_env(monkeypatch, *, cfg|None)   → LLMConfig.load 两态注入
test_chat(monkeypatch)              → AGENTNODES_TEST_CHAT=1（官方短路）
```

**事件断言器**：`EventCollector` 包装 `event_bus.emit`——按序记录
`(session_id, kind, data)`，用于断言 `attempt.started → attempt.failed /
agent_done / queue_paused / session_meta_updated` 的发射序列。

**asyncio**：pytest-asyncio `asyncio_mode=auto`（已是项目配置）；
队列消费者用 `asyncio.create_task` + `await queue.join()` 驱动。

---

## §3 分批用例（每批一个 commit，可独立回滚）

### 批 A · 队列与生命周期状态机（~45 行，约 18 用例）

| 用例 | 覆盖 |
|---|---|
| send_message 首条消息：`session_meta_updated` 事件 + 标题落库 | L183-199（send_message 缺口 6 行）|
| send_message 队列满（预填 `_QUEUE_LIMIT` 项）→ `queue_full` 错误 dict | L204-210 |
| mark_session_processing(True/False) × is_session_processing | L357/386-389 |
| get_available_actions 三态：processing→cancel 项 / 有 queued→? / 空闲 | L364-374 |
| pause_session → resume_queue(True)；resume 未知会话 False | L342-346 |
| cancel(attempt_id)：无任务 False / 有任务且未结束 → task.cancel + True | L307-311 |
| cancel_session：批量取消该会话全部 in-flight | L323-335 |
| wait_for_attempt：结果已就绪即返 / 等事件 set / 超时路径 | L466-479 |

### 批 B · 队列消费器（16 行 + 链路，约 6 用例）

| 用例 | 覆盖 |
|---|---|
| vanilla：入队 2 条 → 依次执行 → 队列退出（processing 清理）| L490-560 主循环 |
| attempt 在 DB 中消失 → warning + continue（不卡队列）| L503-511 |
| _run_attempt 抛异常 → 吞噬 + 继续下一条 | L548-553 |
| cancel 在飞 attempt → `queue_paused` 事件 → resume 后继续 | L530-547 |
| exit-vs-enqueue：消费者退出瞬间新条目入队 → 重启消费者（handoff）| L570-580 |

> 批 B 的驱动方式：直接调 `send_message`（真入口）+ `await queue.join()`，
> `_run_attempt` 用 TEST_CHAT 短路或 fake loop，保证消费者逻辑是唯一变量。

### 批 C · 执行链四出口（61 行，约 10 用例）

| 用例 | 覆盖 |
|---|---|
| LLMConfig.load → None：attempt.failed("LLM config unavailable") + 事件序列 | L619-626 |
| TEST_CHAT=1 正常流：attempt.running → agent_done → `_signal_attempt_done` 结果 | L627-831 |
| TEST_CHAT=1 + `_run_attempt` 内异常 → attempt.failed + agent_done(error) | L849-865 |
| cancel 在飞：CancelledError → attempt.failed("cancelled") + 双事件 + raise | L832-848 |
| `_run_with_agent` 真路径：fake agent 成功 → success dict（含 metrics）| L907-988 |
| fake agent 空回答 → status="empty" | L978 |
| `_run_with_guard`：问句结束 → 注入 continuation → 重试计数；上限触发即停 | L990-1015 |
| `_run_with_guard`：agent.arun 抛 RuntimeError → 上层 `{"status":"failed"}` | L974-975 |
| model override：cfg.model 被改写 | L910-911 |
| thinking/persona：final_prompt 拼接（fake agent 捕获 prompt 断言）| L936-944 |

### 批 D · LLM 链与纯逻辑（~100 行，约 12 用例）

| 用例 | 覆盖 |
|---|---|
| `_extract_summary`：LLM 回复解析（各 marker 形态）/ 解析失败 → None | L1130-1158 |
| `_build_fallback_summary`：user/assistant 片段拼接 + 截断 | L1167-1223 |
| compact_history：LLM ok → 摘要落库（attempt/compaction 消息）| L1255-1318 |
| compact_history：LLM 失败 → fallback 摘要路径 | L1290-1333 |
| compact_history：消息为空 / 无 LLM 配置的守卫分支 | L1255-1264 |
| `_run_test_script` 直测：脚本化 SSE 序列与返回 | L1065-1106 |
| `emit` 闭包：message_id 自动注入 data | L1080-1085 |

---

## §4 验收判据

| # | 判据 | 方向 |
|---|---|---|
| 1 | `service.py` 行覆盖 53.5% → **≥85%**（子集带 cov 实测）| 红→绿 |
| 2 | 全量 pytest 保持 **0 failed**（新增用例按 §3 分批合入）| 绿→绿 |
| 3 | batch_gate ALL GREEN；体量门禁双侧 [OK] | 绿→绿 |
| 4 | 不新增任何生产依赖；`SessionStore` 保持真件（不 mock）| 约束 |
| 5 | 新测试文件 ≤3 个（`test_session_service_units.py` / `test_session_service_queue.py` / `test_session_service_llm.py`），归组清晰 | 约束 |

---

## §5 风险与明确不做

1. **不 mock SessionStore / db 层**——SQL 语义真实（前两轮模式延续）。
2. **不测 SSE 网关细节**（gateway 域已另有覆盖）——事件断言到 EventBus 层为止。
3. **超时测试不真等**——env 注入 + reload，或直接构造已超时状态。
4. `AGENTNODES_TEST_CHAT` 短路与 fake loop 两条路**都要测**：前者钉官方钩子
   不被误删，后者钉 `_run_with_agent` 真路径的组装逻辑。
5. `compact_history` 的 client 注入点需在实施时二次确认（monkeypatch
   `OpenAICompatClient` 或其调用点——勘察时尚未逐行确认注入面，实施批 D
   时先做 30 分钟注入面核查再写用例）。

## §6 工作量估计

| 批 | 用例 | 预估 |
|---|---|---|
| A 队列与生命周期 | ~18 | 0.5 天 |
| B 消费器 | ~6 | 0.5 天（asyncio 编排调试为主）|
| C 执行链 | ~10 | 0.5 天 |
| D LLM 链 | ~12 | 1 天（含注入面核查）|
| 合计 | ~46 | ~2.5 天（含验证与提交）|
