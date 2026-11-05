# Root Cause: Study 轮次全军覆没于 max_iter 占位符 — Goal 续推缺少 Final-JSON 守卫

**日期**: 2026-08-27
**严重度**: P0 — 所有 study 从建立以来 0% 完整迭代率的第一根因
**状态**: 框架与修复方案已确认，随本文档同 commit 实施

---

## 1. 现象（生产实证 — study_f48295053041）

| 观测 | 数值 |
|---|---|
| researcher 单轮 LLM 响应 | 20 次，其中 **14 次 `finish_reason=stop` 且 content 为完整合法 JSON**（557–994 字符）|
| `iter_end(finish_reason=stop)` | **0 条**（20 iter 只有最后一条 `max_iter`） |
| 最终 agent 输出 | `"Reached max_iterations=20 without a final answer."` |
| 下游连锁 | 占位符字符串 → `_rebuild_phase_outputs` json.loads 失败 → 静默降级 `{}` → hypothesis="" / strategy_changes=[] / metrics={} → verdict=discard |
| 全库横切面 | **10/10 个研究** last_verdict 全部 discard、best_metrics 全空 |

关键反直觉点：**LLM 并没有失败**——它一次次交出了合格的结构化答案，是循环层拒绝承认。

## 2. 根因机制（三元组 + 代码链）

```
executor.execute(context={..., "session_id": "study_xxx", ...})
   └─ context 已知键 forward 白名单含 "session_id"     executor.py:33
        └─ AgentLoop(session_id="study_xxx")           # 非 None！
             └─ enable_goal_injection 默认 True         loop.py:299
                  └─ 每次 non-tool-call 响应后:
                       GoalContinuationInjector.inject_post_response
                         ├─ goal=active ✓               goals.db (bootstrap 自动建)
                         ├─ criteria 有 pending ✓        default_goal_criteria() 3 条
                         └─ return True → 追加续推 user 消息 → continue
                            loop.py:1458
   ⟹ _handle_stop 永不执行 (result.answer 永远为 "")
   ⟹ for 循环耗尽 → _handle_max_iter 塞占位符           loop.py:1529
```

### 设计语义错配（为什么这是 bug 而非 feature）

GoalContinuationInjector 的本意是"目标未完成时推 LLM 继续"——这对
**chat 多轮对话**成立（chat_loop 默认关闭该注入、且对话轮以人类发言续推）。
但在 study 的 AgentLoop 里，criteria 衡量的是**整个研究的完成度**
（"构建 A股低回撤组合"），却被用作**单个 agent 单轮响应**是否可以停止的判据。
一轮参数实验永远无法覆盖这种 criteria → 判定恒真 → 死循环到 max_iter。

## 3. 诊断历程（留档，避免后人重蹈）

| 探测轮 | 结论 | 对错 |
|---|---|---|
| 初版报告 | injector 强制 continue = 根因；role_factory 不传 session_id 所以猜疑 build_agent_loop 链 | 方向对；链路细节错——langgraph 走 `executor.execute` 独立构造路径，session_id 经 forward 白名单注入 |
| 静态复核 | 三处矛盾：goal_continuation trace 零条 / 裸 AgentLoop 复现正常 stop / 分支逻辑两出口都 break | 说明运行时差异在链路注入，逐条逼近 |
| **全链复现** | `session_id="study_x1"` + active goal + pending criteria + fake LLM(恒回 JSON) → **精确复现** `max_iter | iters=20 | 同款占位符` | 定案 |
| **修复验证** | 仅给 injector 加 final-JSON 守卫 → `stop | iters=5`（LLM 前 4 轮正常调工具、第 5 轮交结构化答案） | 收工 |

排查中曾误排除的候选及其裁决依据：
- has_tool_calls() 实现（len>0，正确）
- TodosInjector / GoalContextInjector（order 0 / -100，不进 post-response 分支）
- DefaultContinuationStep / DefaultStopStep（均为 no-op stub）
- WRAP_UP_NUDGE（0.8×max_iter 只提示不强停 — 解释了 strategist 纯工具型失败的另一半，见 §6）

## 4. 修复设计 — Final-JSON 守卫（P0-A 变体）

位置：**组件内部**而非 loop.py 决策分支 —— 影响面精确限定在 goal-injection，
chat_loop（默认关）不受影响；goal-workflow 中"模型答非所问时续推"的原能力保留。

```python
# context_injector.py :: GoalContinuationInjector.inject_post_response 开头
c = (getattr(response, "content", "") or "").strip()
if c.startswith("{") and c.endswith("}"):
    try:
        if isinstance(json.loads(c), dict):
            return False   # 完整 JSON 对象 = 本轮最终结论 → 放行 stop
    except json.JSONDecodeError:
        pass               # 半截 JSON 仍走续推
return _legacy(...)
```

选择组件级守卫而非三个替代方案的理由：
- P0-B（构造时显式 enable_goal_injection=False）：一刀切断 chat/goal-workflow
  依赖该注入的合法场景，影响面失控。
- P0-C（重写 injector 为"仅当 LLM 明确求助时续推"）：需要定义新的提示协议，
  改动语义大、无法一次验证。
- P0-A（本方案）：判定函数纯、可单测、行为可被复现脚本即刻证伪；
  若未来 criteria 缩短为"轮内可完成"的形态，仅需移除守卫即可恢复原语义。

## 5. 覆盖范围与残余风险

| 失败模式 | 占比（历史实证） | 是否修复 |
|---|---|---|
| 想收尾型（researcher/factor_analyst/data_quality）：输出 JSON 被 continue 吞掉 | ~50% | ✅ 本修复 |
| 不停手型（strategist/portfolio/risk/attribution/anti_overfit）：全程 tool_calls 直到耗尽 | ~50% | ⚠️ 部分 — 已有 WRAP_UP_NUDGE(0.8×max) 只是弱提醒；彻底解决需"连续 N 次 tool_calls 强制转文本"（后续 PR）|

预期：单轮完整迭代率 0% → 50–70%（researcher 类复活即足以让 phase_engine
拿到真实 hypothesis/action 走完 execution→backtest→evaluation 全链）。
剩余失败的量化与新机制排期见 §7。

## 6. 与既有工作 / 后续路线的关系

- scenario_router、§4b review 短路：**与本修复正交**，前者省成本后者止损；
  在根因修复后它们的价值才真正显现（不再是对着占位符做编排）。
- adaptive_retry 完整版（跨轮 exclude）：**推迟至本修复稳定后重估** —
  P0 生效后多数 round 可正常 keep/discard，跨轮反馈的目标样本会改变。
- agent 集合精简（dq_check / overfit_eval / factor_analyst 契约）、strategy_meta：
  维持既定后续排序不变。

## 7. 验证清单

- [x] 复现脚本：条件三元组 → 必现占位符（等价生产 event_log 形态）
- [x] 守卫实验：stop @ iters=5，answer 为合法结构化决策
- [x] 新增单测 ×3（JSON 响应 stop / 非 JSON 文本仍续推 / 无 goal 场景零回归）
- [x] test_agent_loop* 既有回归全绿
- [ ] 重启服务后创建实测 study：首轮 manifest.metrics 非空、state.best_metrics
      按 keep/discard 正确演化

---

## 8. 后续核实（2026-08-27 深夜）：显示层灾难的前端根因

P0 修复部署后用户仍看到「只有 ok okok / undefined / 未知工具」的空洞卡片。
两轮深入核实（含对早前"assistant_message payload 无 content"结论的推翻）后，
后端链路被证明是**通的**，真正的问题全部在前端显示层。

### 8.1 一次重要的自我纠错

早前曾断言「assistant_message event payload 历来无 content 字段」。
经查该结论源于**查询方法 bug**：event_log 的 `seq` 是 **per-aggregate 序列**
（每个 aggregate_id 各自从 1 递增），用 `WHERE seq=N` 不带 aggregate 条件
会命中任意频道的同号行（llm_usage / session_total_tokens 等）。
用 `(aggregate_id, type, data_json LIKE)` 复合条件重查后：

- researcher 的 stop assistant_message **携带 1213 字符完整 JSON**
  （"strategy.py 与 MomRsrch baseline 完全一致…尚未产生任何 baseline"——
  内容质量很高）
- `_handle_stop → emit("assistant_message", {content})` 链路**历来通畅**

### 8.2 前端两处渲染根因（用户所见卡片逐一对应）

**路径 A：SSE 实时注入（StudyChat.buildAgentEventMessage）**

| 用户所见 | 根因 |
|---|---|
| `thinking_done` 裸文本卡 | `agent_thinking_done` 等 7+ 种低层事件（text.started/ended、iter_end、llm_usage、session_total_tokens…）无显式分支，落入 else → **事件名字面量被渲染成消息** |
| `📋 \`\` → ok` | 部分 tool_result 事件不带 tool/name 字段 → 空工具名 + status 默认 "ok" |
| 同批卡片整组重复 | SSE 重连后 last_event_id 重放 + 消息 id 含 ms 级 timestamp，重放产生新 timestamp → addMessage 视为新消息 |

**路径 B：Projector 物化历史（loadMessages → message_parts）**

| 用户所见 | 根因 |
|---|---|
| 成片 `undefined` / 空 JSON 卡 | 120 条 `text_delta` 事件被物化为 **120 条空 text part**（delta 本身不含累积文本，content 在流末才合并）|
| 成片 `🔬 正在思考...` | 120 条 thinking part 逐条成卡，无聚合 |

后端物化与 SSE payload 本身无数据丢失——`tool_call` part 带
`tool/name/arguments/result` 全字段。

### 8.3 修复设计（仅前端，~25 行）

1. **事件白名单**：`buildAgentEventMessage` 只渲染
   `assistant_message / tool_call / tool_result / text_delta` 四类；
   其余低层事件（thinking_*/text.*/iter_*/loop_*已知/llm_usage/
   session_total_tokens）直接返回 skip（空 parts 已有丢弃机制）。
2. **tool_result 空名兜底**：`data.tool || data.name || '工具'`。
3. **重放去重**：消息 id 从 `agent:{timestamp}:{type}:{agent}` 改为
   `agent:{timestamp}:{seq或计数}`——核实后若 addMessage 按 id set 语义
   已足够（同 id 覆盖），保持 id 稳定键即可。

Projector 侧空 text part 的物化行为留作低优先后端项（前端 skip 后不再
影响显示）。

### 8.4 修复后的预期 UI

一个 agent loop = 一张结构化卡片（assistant_message 的完整 JSON 经
JsonActionCard 渲染：假设/行动/风控评级），工具调用折叠为简短行，
无低层噪音卡，无重放重复。


