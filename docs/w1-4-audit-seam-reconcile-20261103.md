# W1-4 审计报告 — 接缝契约「平台」口径 vs 层表「kernel」口径核对（2026-11-03）

审计类 · 不改代码。脚本 `tmp/w14_audit.py`（AST 全量扫研究域五成员的出向 import）。

## 0. 结论先行

**两套口径不冲突，是不同切面**：

- **接缝契约**的「平台」= 研究域**向外**可达的对方（9→11 触点白名单）
- **层表**的「kernel」= 平台内部的分层归属（L3）

研究域→平台的真实跨层边只有 **8 条**（agent 2 · utils 2 · gateway 2 · runtimes 1 ·
orchestration 1），**全部在白名单内**。其余 34 条所谓「out-of-contract」是
**研究域内部互引**（study↔goal↔autoresearch↔backtest），根本不跨层——我的脚本
把 L2 内部边误归类了。

## 1. 研究域→平台真实边（8 条，全合法）

| 文件 | 目标子层 | 契约状态 |
|---|---|---|
| `study/phase_engine.py` | `agent` | ✅ 白名单（build_agent_loop 能力工厂） |
| `study/runner_knowledge.py` | `agent` | ✅ 白名单 |
| `study/runner.py` | `utils` ×2 | ✅ 白名单 |
| `study/scheduler.py` | `gateway` ×2 | ✅ 白名单 |
| `study/scheduler.py` | `orchestration` ×1 | ✅ 白名单 |
| `study/graph_preset.py` | `runtimes` ×1 | ✅ 白名单 |

## 2. 研究域内部边（34 条，非跨层）

`study` ← 19 条（内部）· `autoresearch` ← 8 · `goal` ← 3 · `backtest` ← 2 ·
`strategy_acceptance` ← 1。

这些是 L2 内部合法互引（同层自由），与接缝契约无关。**唯一值得注意**：
`phase_engine.py` 对 `study` 有 10 条自包内引用 + `autoresearch` 3 条 —— 印证
此前「phase_engine 是引擎的方法适配器」的判定（D1 归位时保留在原地）。

## 3. 与接缝白名单的一致性核对

`tests/test_architecture_gates.py::STUDY_PLATFORM_TOUCHPOINTS`（11 处，含 11-03 新增
`monitor.py`/`store.py` 的 observability）与本审计的 8 条跨层边：

| 白名单文件 | 声明子层 | 实测边数 | 一致 |
|---|---|---:|---|
| `graph_preset.py` | runtimes | 1 | ✅ |
| `phase_engine.py` | agent | 1 | ✅ |
| `runner.py` | agent, observability, utils | utils 2 | ✅（agent/observability 边由 graphify 口径单列） |
| `runner_knowledge.py` | agent | 1 | ✅ |
| `scheduler.py` | gateway, orchestration, utils, observability | gateway 2 + orchestration 1 | ✅ |
| `state_utils.py` | observability | 0（lazy） | ✅ |
| `store.py` | storage, observability | 0（lazy） | ✅ |
| `monitor.py` | observability | 0（lazy） | ✅ |
| `dag_planner.py` | agent | 0（lazy） | ✅ |
| `scenario_router.py` | llm | 0（lazy） | ✅ |

**无冲突、无未登记触点。** 接缝门禁当前绿（B0 已修）。

## 4. 建议（供 W4-1）

1. **接缝契约与层表口径在文档中各写一句「对方视角」**，避免后续审计再混淆
   （本报告即为这次混淆的实例）。
2. 白名单 11 处中有 6 处是 lazy import（0 静态边）——建议门禁白名单区分
   「静态边」与「函数级引用」，前者才需要严格审批。
3. 无需修改任何代码或契约。

## 5. 状态

- 审计完成，零代码改动。
- 接缝契约与层表**口径自洽**，无需修订契约正文（只补 §4.1 的视角说明）。
