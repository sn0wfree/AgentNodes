# 架构复审 2026-11-20 第二轮 · 候选处置记录

> 报告：`C:\Users\1\AppData\Local\Temp\architecture-review-20261120-150939.html`
> （OS temp，不入库）。本文件记录报告 7 个候选的最终处置，供后续复审不重复建议。

| # | 候选 | 处置 | 提交 |
|---|---|---|---|
| 1 | 准入 policy 抽 module | ✅ 已实施（两提交） | `e70dd348` / `6d03c9a0` |
| 2 | 准入缓存 seam | ✅ 已实施（随 1，删缓存+纯函数化） | `6d03c9a0` |
| 3 | 宿主 seam 收口 | ✅ 已实施（_ExecHost 4 能力适配器） | `e428f125` |
| 4 | Router 服务取用 seam | ✅ 第一步实施（session_service_from 单点，过渡 re-export 下版删） | `81744fe2` |
| 5 | Session 垫片测试面 | ✅ 第一步实施（kernel.session 直测 6 例，删 14 垫片留独立批） | `a831b79b` |
| 6 | 僵尸 feature / 死分支 | **冻结不动**——用户 2026-11-20 裁决：属于「删除批延后」门槛，继续冻结，等完整正式版本周期后另批 | — |
| 7 | run_cycle 第二注入口 | ✅ 已实施（删 run_cycle 参数，测试经 RsiHost 覆盖生产分支） | `64a1a103` |

## 冻结项说明（#6）

报告 #6 所列 dangling try/except（monitor/hypothesis/claims）、`prune()` 零调用、
空 registry hook、两处失效 docstring——虽按 deletion test 属"删掉零行为变化"的
死代码，但用户明确裁决归入删除批门槛（与 Chat 3a.4 / legacy Chat / seq_generator /
preset_path 同批纪律）。**后续复审不要重新建议此项**，除非用户在删除批窗口明确解除冻结。

## 下版独立批（本轮只做第一步的收尾）

- 候选 4：chat.py 的 `_get_session_service` / `_session_service_cache` 过渡 re-export 删除。
- 候选 5：90+ 测试锚迁到 `kernel.session.*` + 删 14 个 api/session 垫片。
- 原有删除批：Chat 3a.4 / legacy Chat / seq_generator / `prune()` / `preset_path`。
