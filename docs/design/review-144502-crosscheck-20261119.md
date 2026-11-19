# 14:45 架构复审交叉核对（send_async 编排边界）

> **日期**：2026-11-19 · **对象**：`%TEMP%/architecture-review-20261119-144502.html`
> **基线**：报告 HEAD `2ee1533a`（= 当前 HEAD）· **性质**：只读核对，零代码改动

## 0. 结论

报告的 Top recommendation「先核对 `/send_async` 是否漏在 send-turn 迁移之外」
**部分成立**：核对 3a 方案与代码后，`/send_async` 的入口侧 Attempt 编排是
**有意保留的入口职责**（方案 §5 行为矩阵：Attempt ID 区分 / 创建 / 失败清理 /
reload 状态均列为入口不变量；决策 5 实现为「预检前置」而非整段下沉），
**不是漏迁**。但报告指出的「两个 send coordinators」中有**一处真实重复**
值得单独裁决：历史预检跑两次。

## 1. 事实核对（逐行）

| 报告主张 | 实际代码 | 判定 |
|---|---|---|
| `/send` 走 apps send_turn | `chat.py::_send_via_gateway` 薄 wrapper → `usecase.send_turn`（`8bd0561f`） | ✅ 属实 |
| `/send_async` 仍持有预检/Attempt/事件顺序 | `chat.py:305` precheck → `:332` create_attempt → `:336` attempt.created → `:344` `_send_via_gateway` → `:370/:385` message_received/queue_state | ✅ 属实 |
| 「再走旧 helper」 | `_send_via_gateway` 已是调用 send_turn 的薄 wrapper（非旧完整实现） | ⚠️ 措辞过时——走的是 wrapper，不是旧实现 |
| 「两个 send coordinators」 | Attempt 生命周期跨入口侧（创建/删除/status）与用例侧（mark_*） | ⚠️ 部分是设计边界，部分是重复（见 §2） |
| 文档数字 94,622 vs 当前 94,635 | 3a 执行记录 §10.4 写 94,622；send_async 后续提交（3a.4 +13 行）已到 94,635 | ✅ 属实，待校准 |

## 2. 真实重复项（待裁决，不擅自改）

**重复 A（低风险、纯只读）**：历史预检跑两次——
`send_async:305` 调 `usecase.precheck_history()`，随后 `_send_via_gateway` →
`send_turn()` 内部 `:426` 又调 `_precheck_history()`。两次都是只读、幂等，
但 `get_messages` + `_convert_messages_to_history` 成本翻倍（100 条历史）。
可选修法：给 `SendTurnInput` 加可选 `prevalidated_history` 字段，send_async
预检后把结果传入，send_turn 跳过二次预检；失败语义不变。需 R-014 方案。

**重复 B（边界问题）**：Attempt 创建/删除/status 在入口侧（`chat.py:332,358,361`），
Attempt 状态迁移在用例侧（`send_turn._update_attempt`）。这是「入口专属身份
生成 + 用例侧执行状态」的分工，与方案 §5 一致——**不建议**为消除表面重复而
整段下沉（会丢 `/send_async` 的 attempt_id 外部可见性与 reload 语义）。
若裁决要统一，需先重开方案 §5 的行为矩阵裁决。

## 3. 报告其余候选核对

- 候选 2（HTTP 资源组装/生命周期）：与已落库的 M12 方案（`2ee1533a`）一致，
  已列四项待裁决，不重复处理。
- 「send-turn 执行记录 §10.4 数字过期」：属实，待校准（低优先级文档批）。
- 门禁状态（上行 0 / SCC 0 / semantics 17/17 / 数字同步通过）：与当前 batch_gate
  12 步 ALL GREEN 一致。

## 4. 执行建议

1. 本核对文档不触发任何代码改动；`send_async` 入口编排按方案 §5 保持。
2. 若用户裁决重复 A 值得修：单独 R-014 子批（`prevalidated_history` 入参），
   保住严格历史失败零半提交 + 事件顺序 + 响应形状 + STEER。
3. 重复 B 不动，除非重开方案行为矩阵裁决。
4. 数字校准（§10.4 94,622→94,635）并入低风险文档批。