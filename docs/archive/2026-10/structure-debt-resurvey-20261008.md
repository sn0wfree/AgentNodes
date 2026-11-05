# 结构债再梳理 — 2026-10-08（第二轮）

> 承接 `docs/archive/2026-10/structure-debt-survey-20261008.md`（第一轮，会话早段）。同日两轮快照，本文档只做**对比 + 现状 + 余债**。
> 触发：完成 Step 0-3 重构链 + P0 防线 + P1 三项 + P1.5-a/b 清扫后，重新过一遍代码面。
> 当日累计：**30 commits**。

## 1. 两轮对比总表

| 指标 | 第一轮 | 第二轮 | 变化 |
|---|---|---|---|
| 总行数 / 文件数 | 107,117 / 466 | 107,455 / 466 | +338 行（handler 样板 - 自import 删除 + 方法抽取重排） |
| median / p90 | 136.5 / 492 | 136.5 / 492 | 不变 |
| **ruff src** | **71**（F401×55, C901×8, F811×4, I001×4） | **3**（C901×3） | **-68** ✅ |
| F821 undefined-name | 0（已修 12） | 0 | 保持 |
| C901 大户 | runner `_run_loop` 32 · phase_engine 24 · study_dispatch 22 · build_langgraph 21 | event_store `emit` 16 · prompt_schema 16 · migrate 20 | **study 引擎链全清** |
| 测试 | 12,366 | **12,373** | +7 契约测试 |
| 测试失败 | 15 pre-existing + flaky | **0**（69 回归口径；2 个已知 flaky 家族 A/B 判定） | ✅ |
| DELETE-CANDIDATE 挂账 | 17 标签 | **17 标签**（未动，P1.5-c） | 不变 |
| 巨文件 max | 1775 `goal/store.py` | 1772（同文件） | 不变 |

## 2. 文件行数变化（拆分的净效果）

| 文件 | 前 | 后 | Δ | 说明 |
|---|---|---|---|---|
| `core/study/runner.py` | 1291 | **1152** | **-139** | `_run_loop` 329→114（抽 3 个方法） |
| `core/autoresearch.py` | 1413 | 1393 | -20 | 删 3 个自 import 块 |
| `core/goal/store.py` | 1775 | 1772 | -3 | — |
| `api/routers/study.py` | 1604 | **1642** | **+38** | 7 个 `_act_*` handler 样板（C901 22→6，行数升是预期） |
| `api/session/projector.py` | 1480 | 1493 | +13 | 新增 `close()` |

**结论**：复杂度下降 ≠ 行数下降——表驱动/方法抽取增加样板行，换来分支数和认知负担下降。评估拆分要看 C901 与调用面，不是文件大小。

## 3. 防线现状（本轮新增资产）

```
本地 pre-commit（E9/F821，staged）            ← 2b652f2，两次逃逸实证后补
GitHub CI（ruff=3 现状 + 12 分片 + eval-sim）   ← 既有，ruff 噪音清零后门禁可信
PR gate lanes（-m not llm and not e2e and not slow） ← bdbf922，核心集 61s
Nightly（真 LLM + mean/pass 跟幅 exit 1）       ← 5072cb3，基线待首跑自举
```

**flaky 资产**（A/B 判定法，写进验收记录的可复用流程）：
- `test_study_concurrency::test_concurrent_runner_creation`（SQLite 锁竞态）
- `test_study_dump::test_dump_with_studies`（同家族，order-dependent）
- 处置：CI `--reruns=2` 兜底；根因（连接生命周期）有 `Projector.close` 先例可循，未专项修

## 4. 余债清单（按当前 ROI）

| 级别 | 项 | 量 | 备注 |
|---|---|---|---|
| **P1.5-c** | DELETE-CANDIDATE B1-B12 执行 | ~1 天，~2000 行 | 设计已就绪（`p1.5-cleanup-batch-design.md`），**最大剩余项** |
| P1 补遗 | timings 缺口 153 文件增量补跑 | 15 分钟 | chunked 脚本现成，非紧急 |
| P2 | C901×3 收尾（event_store 16 · prompt_schema 16 · migrate 20） | 半天-1 天 | 已不在主链，改到再顺手 |
| P2 | push main 无过滤全量 job（marker lanes follow-up） | 半天 | doc §6 记过 |
| P2 | flaky 根因（SQLite 连接生命周期） | 半天 | 有 Projector.close 模式 |
| P2 | dag/env 假选项**删除**（现仅 warning 收口） | 需 API 协调 | Step 2 边界外 |
| P2 | pyright 宽松模式 | 渐进 | — |
| P3 | 巨文件（web_session/projector 合并等） | 大 | 与第一轮判断一致：**不紧急** |

## 5. 新发现（本轮扫描）

1. **`study.py` 是唯一「拆分后变大」的文件**（+38）——表驱动样板的代价。后续同类改造接受这个模式即可。
2. **C901 残余 3 个已全部脱离主链**（event_store 元件层、prompt_schema 提取器、migrate 脚本）——study/agent 执行链的复杂度债**清零**，这是与第一轮最大的差别。
3. **ruff 门禁现在是「真门禁」**：3 个错误全是目标内的 C901，任何新增违规立刻显形（第一轮 71 个错误里 80% 是噪音的状态结束）。
4. docs 目录 168 个 md——文档跟随每个阶段性任务的纪律已形成，但也到了**索引缺失**的程度（新读者无法导航）。可选项：补一页 `docs/INDEX.md`（半小时）。

## 6. 与第一轮 ROI 表的差异

第一轮推荐序：`B（F811）→ A（DELETE 清账）→ D（F401）`。
实际完成序：**D（F401×56）→ B（F811×4）→ …A（DELETE）仍未做**。

原因：P0/P1 的防线与测试加固让删减类操作先变安全，于是 F401 这类机械清理提前做了；DELETE-CANDIDATE 因逐条验证成本高而留到 P1.5-c。**顺序差异不代表计划失误——护栏先行的判断被事后证明正确**（349 模块 import smoke + 1279 回归正是删 56 个 import 的底气）。
