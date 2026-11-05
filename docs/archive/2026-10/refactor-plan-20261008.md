# 重构计划 — 2026-10-08

> 输入：`refactor-readiness-20261008.md`（切入点 A-E）· `industry-research-20261008.md`（借鉴 #1-#6）· `architecture-current-20261008.md`（契约红线）
> 原则（业界印证）：**显式契约不魔法抽象 · 最简方案优先 · 评估先行当安全网**
> 阶段独立 commit、每阶段可回滚；护栏 checklist 见 §7。

## 总览

```
Phase 0  轨迹评估安全网（借鉴 #1）          0.5-1 天   ← 重构前置
Phase 1  研究纪律落文（借鉴 #2/#3）        1-2 天
Phase 2  切入点 A：web_session ↔ projector  2-3 天   ← 主战场
Phase 3  切入点 C：双入口收口（插缝）        0.5 天
Phase 4  切入点 B：mixin→composition（渐进） 长期规则
Phase 5  按需：D/E · guardrails · crash repair
```

## Phase 0 — 轨迹评估安全网（0.5-1 天）

**为什么第一**：重构的每一步都需要「行为是否漂移」的检测——golden 分数太粗，轨迹 diff 才是重构护栏（调研 §3 #1）。

| 步 | 动作 | 验收 |
|---|---|---|
| 0.1 | scorecard 扩轨迹字段：`steps`（迭代数）· `tool_sequence`（工具调用序列摘要）· `fallbacks`（回退/重试计数）· `e2e`（终态） | `to_dict()` 含四字段 |
| 0.2 | `check_eval_regression.py` 对轨迹字段做**漂移检测**（steps ±20%、tool 序列 edit-distance） | 脚本 exit 1 能抓住人为注入的轨迹漂移 |
| 0.3 | **重构前 baseline**（70 题或 10 题子集跑一次真 LLM → `--update-baseline`） | `evals/nightly-baseline.json` 含轨迹字段 |

**依赖**：真 LLM key（本地 MiniMax 可用）；子集 10 题也可起步（高区分度题：injection/lookahead/overfit/safety）。

## Phase 1 — 研究纪律落文（1-2 天，借鉴 #2/#3）

| 步 | 动作 | 落点 | 验收 |
|---|---|---|---|
| 1.1 | **单源评分铁律**：`factor_validate` 唯一 `primary_score()`（QuantStudio B1 v2 加权可后议），工具层不暴露近似评分 API | `factor_validate.py` + extension-points 补「评分单源」红线 | 项目内 `rg` 只有一个评分入口；文档铁律 |
| 1.2 | **回测体检 5-gate**：持仓/换手/Sharpe-IC_IR 区间/MDD 区间/全仓时间（B2） | `strategy_acceptance.health_check()` 挂入 study finalization（`_finalize_round` 后） | 植入 look-ahead 样例 → gate 拦截；正常 round 不受影响 |
| 1.3 | **output_contract 雏形**：round 交付物 schema（必备指标+结论枚举 B3） | `round_manifest` 增必填字段（渐进，不 break 现存 manifest） | manifest 校验通过/缺失告警 |

**顺序理由**：#2/#3 是**数据面与验收面的规则**，在 A 切入点动 session 存储前落定，重构时新旧路径都受约束。

## Phase 2 — 切入点 A：web_session ↔ projector（2-3 天，主战场）

现状：`routers/web_session.py`（1503）+ `session/projector.py`（1493）同主题巨文件对；event_store emit 已拆三助手（顺势）。

| 步 | 动作 | 验收 |
|---|---|---|
| 2.1 | **耦合面审计**（0.5 天）：两文件的调用图——SSE 推送 / projector flush / 连接生命周期 / 快照缓存，画边界 | 审计 doc；每块的行数与消费方 |
| 2.2 | **projector 拆分**（1 天）：`ProjectionCore`（事件→消息）· `SnapshotCache` · `Connection`（close 纪律已有先例） | C901/行数双降；`test_projector*` 全绿 |
| 2.3 | **web_session 拆分**（1 天）：SSE 路由面 · 会话查询面 · 挂起任务面 | 同上；`test_session*` + SSE 测试全绿 |
| 2.4 | 每步跑护栏（§7）+ **轨迹 diff**（Phase 0 的检测器） | 轨迹字段无漂移 |

**红线**：event_store 是消息真源（projector 只是物化视图）；flush 保持 best-effort；SSE 事件序不变。

## Phase 3 — 切入点 C：双入口收口（0.5 天，插缝可做）

`chat_loop.build_chat_agent_loop` vs `role_factory` → **config preset 合一**（`AgentConfig.preset("chat")`），chat_loop 退薄封装后删。验收：chat/session 测试全绿、构造入口只剩一个。

## Phase 4 — 切入点 B：mixin → composition（长期规则）

**规则化而非专项**：新代码禁 mixin（extension-points 已立）；每个被触碰的 mixin（改 bug/加字段时）就地换显式 context 对象。**不设完成日期**——预期随 A/C 的触碰渐进消化。

## Phase 5 — 按需（触发条件驱动）

| 项 | 触发 | 来源 |
|---|---|---|
| 切入点 D（扩展机制合并） | 新横切需求撞上机制选择困难 | readiness §5 |
| 切入点 E（store 分仓） | 查询面需求出现 | readiness §5 |
| 输入侧 guardrails | 安全事件或对外暴露面扩大 | 调研 #4 |
| ACI / Poka-yoke 工具审计 | 工具误用率上升或 toolset 重构 | 调研 #5 |
| crash repair | study 长任务崩溃投诉 | 调研 #6 |

## 6. 里程碑与回滚

| 里程碑 | 内容 | 失败判据 |
|---|---|---|
| M0 | 轨迹安全网上线 + baseline | 检测器抓不住注入漂移 → 停，先修检测器 |
| M1 | 铁律 + 体检 gate 上线 | gate 误杀正常 round → 收紧阈值前先停 |
| M2 | A 切入点完成（两文件各降一半） | 任何步轨迹漂移/回归失败 → revert 该步 |
| M3 | C 完成 + B 规则生效 | — |

## 7. 护栏 checklist（每步动手前）

1. 69 文件回归 PR-lane（1162 基线 / 0 flaky）
2. 相关契约测试（RoundOutcome / BranchContext / dispatch / claims）
3. **轨迹 diff（Phase 0 起）**——行为漂移的主检测器
4. A/B 归因法（可疑失败先 stash 对照）
5. API 面改动 → `openapi.json` + `types.gen.ts` 同步
6. 语义红线对照（ARCHIVED 保留 · M2 bank · M1 expire · H2 · H1 · journal A→B 顺序）
7. 每步独立 commit，可单独 revert
