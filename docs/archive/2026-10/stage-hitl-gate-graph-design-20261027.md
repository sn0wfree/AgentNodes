# HITL 审批门（图路径暂停段）设计 — 2026-10-27

> 把 langgraph 分支独有的 study 级 HITL（novelty 审批门）吸收进主流程
> （phases → GraphEngine 图路径）。**本轮只做暂停路径**；resume 机制
> （持久化重建 + `start=` vs SqliteSaver）后续单独讨论（P5-2 尾项）。
> 调查结论见会话记录：`paused_for_approval` 全库仅 `langgraph_engine.py`
> 产生；两代引擎中断机制同源（state 写 `__interrupt__`）。

---

## 1. 目标与非目标

**目标**
1. `study.hitl=True` 的 study 在图路径跑到 novelty 之后、claims/回测之前暂停，
   人工审批后再决定继续/跳过。
2. 前端契约**零改动**：`study_paused` 事件（带真实 `interrupt_id` + hypothesis，
   F2 修复成果）+ `POST /study/{id}/interrupts/{iid}/respond` + 刷新恢复
   `GET /study/{id}/interrupts/pending` 全部照旧。
3. 默认路径（`hitl=False`）行为与今天逐字节一致（B1-b 等价性不受扰动）。

**非目标（本轮不做）**
- resume（approve 后从断点续跑）——走既有旧引擎兜底，见 §5；
- reject / 超时路径改造——runner 的 M1/M2/H2 保护本来就是分支无关的，直接复用；
- WebUI start 表单加 hitl 开关（API 已可传，UI 后续）。

## 2. 门的位置与形态

```
researcher → novelty ──(dup)──→ END
                 └─(proceed)→ [gate]（仅 hitl=True 时注册）→ claims → execution → evaluation
```

- 门节点放 **novelty 去重之后、claims/执行之前**——审批发生在花钱（回测）前。
- **与旧引擎的次序差异（如实记录）**：旧 langgraph 门接在
  `researcher → novelty_gate → 各下游目标`（`langgraph_engine.py:386`），
  即审批发生在**去重之前**（门内自带 no_hypothesis 检查，完整去重
  `_check_novelty` 不在旧引擎流内）；新路径先去重后审批——重复假设
  由去重门自动归档拒绝，不再消耗人工审批。语义更优而非等价；hitl
  此前处于休眠（默认 phases、WebUI 不传 engine），无存量行为可破坏。
- 空假设不需要门内守卫：上游 `_novelty_gate → runner._check_novelty` 已把
  空假设路由到 dup/END（旧引擎门自带 no_hypothesis 检查是因为它的门在去重之前）。
- **构建期条件注册**（`build_research_engine` 里 `getattr(ctx.study, "hitl", False)`）：
  关门时图里根本没有 gate 节点，默认路径图形状不变——等价性由构造保证，
  不是运行时 if。

## 3. 契约对照（照抄源：langgraph_engine.py:230-285, 621-657）

| 环节 | 旧引擎（langgraph） | 新图路径（本轮） |
|---|---|---|
| 门触发 | `interrupt({...})`（langgraph 内建） | `raise GraphInterrupt("novelty_gate", payload)` |
| 中断标记 | state `__interrupt__` = Interrupt 对象数组 | state `__interrupt__` = `{node, reason, data}`（引擎 `graph_engine.py:298-303`） |
| payload | `{type, study_id, round_num, hypothesis, predicted_affected, message}` | **逐字段相同** |
| 门前后事件 | `study_phase(novelty_gate, interrupted, hypothesis[:200])` | **相同** |
| 落库 | `StudyStore.create_interrupt(sid, round, type, payload_json)` | **相同**（走 `ctx.runner.study_store`，不另开连接） |
| awaiting_approval | `study_phase(phase="langgraph_exec", status="awaiting_approval", interrupt_id, hypothesis, gate_message)` | **同形**，phase 改 `"novelty_gate"`（前端零消费此事件——webui 全库无 awaiting_approval 引用，卡片由 study_paused 驱动） |
| 返回 dict | `{"round", "run_name", "paused_for_approval", "study_id", "interrupt_id", "hypothesis"}` | **逐字段相同** |
| runner 消费 | `_handle_hitl_pause`（runner.py:399-540，分支无关） | **不动，直接复用** |

`_run_langgraph_branch` 对 paused dict 早退（`phase_engine.py:445-446`）；
图路径在 `run_phases_via_graph` 里做同构早退：`reason == "novelty_gate"`
落库暂停；**其他中断（当前唯一来源 `max_hops`）转 abort dict**
（`reason=interrupt_<原因>`，runner 预算入账 + continue 跳轮）——
不落库、不伪装成审批暂停，也绝不落到 RoundOutcome 构造崩 KeyError。

## 4. 改动清单

| # | 文件 | 改动 |
|---|---|---|
| 1 | `core/study/models.py` | `StudyRecord.hitl: bool = False`（engine 字段后） |
| 2 | `core/study/store.py` | DDL 加列 `hitl INTEGER NOT NULL DEFAULT 0`；迁移（照 v3 engine 模式 `study_cols` 探测 + ALTER）；`create_study(hitl=)` 参数 + INSERT；`_study_from_row` 行映射 |
| 3 | `core/study/bootstrap.py` | `create_study_record(..., hitl=False)` 透传（全 kwargs 调用点，加参安全） |
| 4 | `api/routers/study.py` | `StudyStartRequest.hitl: bool = False`；`study_start` 透传 + 响应回显 |
| 5 | `core/study/graph_preset.py` | 门节点 `_novelty_gate_node` + 构建期条件接线 + `run_phases_via_graph` 的 paused 早退（落库 + 事件 + dict） |
| 6 | `tests/test_hitl_gate_graph.py` | 新增：门开/关/payload 契约/落库/事件/runner 消费 |

不动：`phase_engine.py`（dispatch 表无新分支——门活在 phases 分支内部）、
`runner.py`、前端、`langgraph_engine.py`（它还在服务 `engine='langgraph'` 的
存量路径，P5-2 删除时统一处理）。

## 5. 中间态语义（resume 未建时的 approve 行为）

hitl study 在图路径暂停后，用户 approve：
`_handle_hitl_pause` → `_resume_round_langgraph` → `get_profile("phases")`
→ `checkpoint=False` → `resume_round_langgraph` 返回
`{"error": "no_checkpointer"}`（`langgraph_engine.py:729-737`）→
runner.py:505-517 现成兜底：**过期 interrupt + `study_round_rejected`
(reason=no_checkpointer) + 跳过该轮 + 循环继续新一轮**。

即中间态下 approve = 「该轮作废、研究继续」（下一轮重新跑 researcher），
reject / 超时 = 现有完整语义（M1 过期 + M2 预算入账 + H2 防幽灵轮）。
不崩溃、不留 PAUSED 僵尸状态、不写空 metrics 轮。resume 建成后此兜底
自然失效（不再走到）。

## 6. 验收

1. 新测试 `test_hitl_gate_graph.py`：关门图形状不变 / 开门中断 payload
   五字段 / `create_interrupt` 落库 / awaiting_approval 事件 / paused dict
   六字段 / runner `_handle_hitl_pause` 消费后发 `study_paused` 带真实 id。
2. 既有回归：`test_research_preset.py`（11）+ `test_phase_engine_*` +
   hitl 相关存量（`test_study_hitl_respond` 等）全绿。
3. 全量分道：与阶段 4 基线同集合（无新增失败）。
4. lint 干净。

## 7. 后续（不在本轮）

- **resume 机制**：决策记录与业界调研见
  `hitl-resume-industry-research-20261027.md`（2026-10-27）——倾向
  方案 A（持久化重建 + `start=`），实施要点清单见该文 §5；
  检查点能力的吸收形态定为「phase 渐进落盘」，SqliteSaver 出局。
- P5-2 删 langgraph 分支（本门建成前置即满足：HITL 能力已在主流程落地）。
- WebUI start 表单暴露 hitl 开关（可选）。
