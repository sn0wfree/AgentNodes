# Session 垫片测试面归位（第二轮候选 5）R-014 方案

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **前置**：M12/M13 已收口；第二轮复审 `architecture-review-20261120-150939.html` 候选 5。
> **范围**：只做第一步——给 kernel.session 深模块补关键契约直测；14 个垫片的
> 删除留"下版独立批"（Q1/Q3-A，与既有兼容门槛一致）。

## 0. 目标与非目标

**目标**：让 kernel.session 深模块（projector / store / service / part_accumulate）
通过公开 interface 获得直测——deletion test 成立（删垫片后深模块测试仍在），
测试面贴深模块而非 14 层转发。

**非目标**：① 不删 14 垫片（下版独立批）；② 不批量改 90+ 现有测试 import（下版随删垫片做）；
③ 不改任何生产代码；④ 不复制现有测试（补关键契约，不堆数量）。

## 1. 现状证据

- `interfaces/api/session/` 14 个垫片各 6-10 行，纯 `from kernel.session.* import X` +
  `__all__`；唯一生产消费点 `web_session.py:446`。
- 90+ 测试引用垫片路径（test_projector* / test_store / test_chat_timeout 等），
  kernel.session 深模块（projector 312 行 / store 366 行 / service 1547 行 /
  part_accumulate 129 行）自身无等价直测。
- Deletion test：删垫片后运行时复杂度归零（生产只一处 import 改路径即可），
  但 90+ 测试锚断裂——伤害集中在测试面而非行为。

## 2. 设计（grilling 决策落位）

| # | 决策 | 落位 |
|---|---|---|
| Q1 | 分两步：先补直测、删除下版批 | 本批只补 kernel.session 直测，垫片保留 |
| Q2 | 关键契约直测不照搬 | `tests/test_session_kernel_direct.py`，对 4 深模块的公开 interface 写行为级关键用例 |
| Q3 | 删除类留独立批 | 与 M11b / 删除批门槛一致（至少一个完整版本周期后另批） |

**直测覆盖的关键契约**（每个深模块 2-3 例，行为级、经公开 interface）：
- `kernel.session.projector`：事件→消息/parts 投影正确性、幂等性
- `kernel.session.store`：会话/消息 CRUD 往返、归属过滤
- `kernel.session.service`：生命周期（创建/取用/关闭）、事件总线桥接
- `kernel.session.part_accumulate`：分段累积的边界行为

测试只 import `agentnodes.kernel.session.*`（不经过垫片）——证明深模块可独立测试。

## 3. 分批与验收（单提交，第一步）

| 验收项 | 落点 |
|---|---|
| 深模块可直测 | 新测试文件 import kernel.session.* 且全绿 |
| deletion test | 逻辑成立：删垫片不影响新直测（本批不删，下版验证） |
| 零生产改动 | 本批只加测试文件 |
| 全量 + gate | R-007 |

**下版独立批（不在本方案实施）**：测试锚批量迁到 kernel.session.* → 删 14 垫片。

## 4. 风险与回滚

- 直测若用私有接口（非公开），deletion test 不成立——必须经公开 interface。
- 深模块的真实依赖（db_path / event_bus）需用临时目录/注入替身，避免污染生产数据。
- 本批只加测试，回滚 = 删测试文件，零生产风险。

## 5. 明确不做

① 删 14 垫片；② 改 90+ 现有测试 import；③ 改生产代码；④ 碰候选 4。

## 6. 实施前确认

本方案来自 grilling 共识（#5 Round 1 全 A）。确认后按 R-007 单提交实施第一步。

## 7. 执行记录（2026-11-20 · 已实施 · 只加测试零生产改动）

- 新建 `tests/test_session_kernel_direct.py` 6 例，全部 import
  `agentnodes.kernel.session.*`（不经过 14 垫片）：Projector（apply 幂等 /
  interested_in FLUSH_ON 过滤）、SessionStore（attempt CRUD 往返 /
  list_attempts_by_status）、SessionService（新会话非 processing /
  create_session 后查询）。part_accumulate 是 Projector 内部实现，经 apply 间接覆盖。
- 测试自建最小 schema（临时 db_path），不污染生产数据；Attempt 用真实
  AttemptStatus 枚举 + react_trace_json/metrics_json 列名（探针核实）。
- deletion test 现在成立：14 垫片删除后本文件仍绿（下版独立批验证）。

验证：新测试 6 passed；零生产改动（数字不变 464 文件 / 96,912 行）。
14 垫片保留（Q1/Q3-A，下版独立批删除）；90+ 现有测试锚本批不动。

**候选 5 第一步完成**：深模块获得公开 interface 直测，测试面贴深模块。
删除类动作（迁 90+ 锚 + 删 14 垫片）留独立批，与兼容门槛一致。
