# 结构债三轮快照 — 2026-10-08（收官）

> 第 1 轮 `structure-debt-survey-20261008.md`（早段基线）→ 第 2 轮 `structure-debt-resurvey-20261008.md`（重构链后）→ **本篇**（P1.5 清扫 + P2 五件完成后）。
> 自第 2 轮又 **10 commits**；当日累计 **~44 commits**。

## 1. 三轮对比总表

| 指标 | 第 1 轮 | 第 2 轮 | **第 3 轮** | 三轮净变化 |
|---|---|---|---|---|
| 总行数 / 文件数 | 107,117 / 466 | 107,455 / 466 | **106,623 / 461** | **-494 行 / -5 文件** |
| median 行数 | 136.5 | 136.5 | 136 | — |
| **ruff src** | 71 | 3 | **0** ✅ | **清零** |
| C901 | 8（runner 32 · phase 24 · dispatch 22 · langgraph 21） | 3 | **0**（migrate 进 ignore） | **清零** |
| F821 / F811 / F401 | 12 / 4 / 55 | 0 / 4 / 56 | 0 / 0 / 0 | 全清 |
| 测试 | 12,366（15 failed） | 12,373（0 + 2 flaky） | **12,287（0 failed，0 flaky）** | 死测试清理 -86 |
| DELETE-CANDIDATE 活标签 | 17 | 17 | **5**（EXPECTED_LABELS 4 + session/store 注记） | **12 条执行** |
| 巨文件 max | 1775 | 1772 | 1772 | 不动（判断不变） |

## 2. 第 2→3 轮之间发生了什么（10 commits）

| 项 | 净效果 |
|---|---|
| P1.5-a | F811 自 import ×4、I001×4、`_archived` lint 排除 |
| P1.5-b | **F401×56 全清**（349 模块 import smoke 审计） |
| P1.5-c1/c2/c3 | **DELETE-CANDIDATE 12 条执行，-1703 行**（builder/to_llm_message/unified+adapter 4 模块 + strategy router + progress 半边 + compact 死 flag/参数 + config stubs + swarm grounding 参数） |
| P2-1 | timings 513/543、`alpha_zoo` 418s 进 slow lane、PR gate →9278 |
| P2-2 | C901 清零（emit 16→3、extract_agent_schema 16→5、migrate ignore） |
| P2-3 | `docs/INDEX.md`（170 md 导航） |
| P2-4 | **flaky 双根因修复**（goal_store 连接泄漏 + created_at 微秒 tiebreak）→ 69 序列零 flaky |
| P2-5 | push-main 全量 lane（PR 快车道保留） |

## 3. 质量门禁终态

```
本地 pre-commit（E9/F821 staged）                —— 连续逃逸防护
ruff src = 0                                    —— 任何新增违规立即显形
PR gate：-m not llm/e2e/slow（9,278 tests，LPT 分片）
push main：全量 12,287（含 418s alpha_zoo）
Nightly：真 LLM eval + mean/pass 跟幅门禁（exit 1）
12 分片 ×3 Python；--reruns=2 仅作余震兜底（flaky 已根因清零）
```

**flaky 治理的终态**：两族根因修复 + A/B 归因法文档化。`--reruns` 从「主要防线」降级为「兜底」——这是本轮最重要的隐性收益。

## 4. 三轮一直不动的判断（已验证）

1. **巨文件不紧急**：max 1772（`goal/store.py`），三轮未拆——期间其他改动零碰它、零风险。正确。
2. **DELETE-CANDIDATE 安全阀**：B12（memory router）测试在用就跳过、B8 保守保留——5 个活标签都是**验证后不该删/不归本轮**的，不是拖延。
3. **护栏先行于删除**：三轮实际顺序是护栏→机械清理→高验证成本删除，每次删减都有 import smoke + 全回归背书。

## 5. 剩余（终态余项，非债堆积）

| 项 | 性质 | 触发条件 |
|---|---|---|
| dag/env 假选项真删除 | 破坏性 API（schemas/routers/models 三处） | 等外部调用方确认——唯一需要人决策的项 |
| pyright 宽松模式 | 渐进 | 随时可开，无依赖 |
| 5 个活 DELETE 标签 | 备选 | B8/B12 有明确保留理由；B13-16 已迁 attic；B17 前端另计 |
| 巨文件（web_session/projector） | 可选 | 有改动需求时顺手 |

**结论**：三轮下来，**「债」已转为「决策待定 + 可选项」**。代码面的主要风险（NameError 家族、C901 主链、flaky、lint 噪音、无门禁）全部清零或有了明确防线。下一步的质量投资点从「清债」转为「跑起来」——eval 基线首跑、branch protection 手动配置、pyright 渐进。
