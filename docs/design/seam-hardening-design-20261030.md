# 接缝加固设计：平台/研究域分而不拆（2026-10-30）

> 背景：用户提出「底层 agent 与上层研究是否拆成两个项目」。评估结论
> （2026-10-30 会话）：**现在不拆**——平台还没有第二个消费者、单人项目、
> 数据面刻意共享；但接缝经 R1–B3 后已事实上单向（实测：平台→研究域
> 零引用；研究域→平台仅 5/31 文件），应该**用门禁锁死**，使其在触发
> 拆分条件出现时依然成立。本刀 = 方案 A「接缝加固」，1-2d。
> 三道门沿用。

---

## 1. 实测基线（2026-10-30，决策依据）

| 测量 | 结果 |
|---|---|
| 平台子层（gateway/orchestration/runtimes/context/llm/storage/memory）→ 研究域（study/goal/hypothesis/backtest/autoresearch） | **零引用**（唯一命中 `agent/dag_config.py:176` 为 docstring 提及） |
| 研究域 → 平台 | **5/31** 文件：graph_preset→runtimes · phase_engine→agent · runner→utils · runner_knowledge→agent · scheduler→gateway+orchestration |

## 2. 既有门禁的缺口

`tests/test_architecture_gates.py`：

| 缺口 | 现状 | 后果 |
|---|---|---|
| `PLATFORM_PKGS` 缺 `llm`、`memory` | 只有 gateway/orchestration/runtimes/storage/context | 这两个子层反向依赖研究域**不会被抓住** |
| 只禁 `core.study` | goal/hypothesis/backtest/autoresearch 不在禁区 | 研究域其他成员被平台引用不报警 |
| 无**触点白名单** | 研究域→平台的耦合面可无声扩大 | 第 6 个触点出现时无人知晓——拆分成本悄悄回升 |

## 3. 设计

### 3.1 门禁一：平台→研究域全禁（扩面）

```python
PLATFORM_PKGS += ("llm", "memory")          # 7 个严格子层
BANNED_DOMAINS = study, goal, hypothesis, backtest, autoresearch
```

- **agent 不进严格集**：`agent/builtin_tools/` 是能力适配器，合法包装域
  工具（如 run_backtest→core.backtest）——这是组合根职责不是反向依赖。
  agent 的边界由既有 `test_domain_layers_use_the_agent_factory_not_raw_agentloop`
  等门禁另行看守；
- 现状全绿（实测为零），本刀只锁不修。

### 3.2 门禁二：研究域→平台触点白名单（新）

研究域**顶层**文件中允许 import 平台子层的清单锁定为当前 5 个：

```python
ALLOWED_TOUCHPOINTS = {
    "graph_preset.py":      {"runtimes"},                     # GraphEngine 公共 API
    "phase_engine.py":      {"agent"},                        # build_agent_loop 能力工厂
    "runner.py":            {"utils"},                        # bg_proc
    "runner_knowledge.py":  {"agent"},
    "scheduler.py":         {"gateway", "orchestration"},     # 统一入口适配器
}
```

- `_archived/` 豁免（隔离区）；
- 白名单语义：**新增触点 = 有意决策**——改测试 + 在接缝契约登记理由，
  不是禁止扩展，是禁止无声扩展。

### 3.3 接缝契约一页（文档刀）

`docs/design/seam-contract-platform-research-20261030.md`：

- 平台各子层的**公共面**（研究域允许使用什么）；
- **禁触内部**（loop mixins / compaction internals / mailbox 内部结构 /
  controller 私有属性）；
- 5 触点清单 + 各自理由；
- 拆分触发条件 4 条 + 触发后的预期工期（3-5d，因接缝已被看守）；
- 与 v6.3 图接缝规则的对应（P-1 事件 / P-3 登记 / P-4 血统）。

## 4. 明确不做

- 不做 B 包拆分 / C 双仓库（触发条件见评估）；
- 不收窄 agent 的能力适配器（那是组合根职责）；
- 不解 `phase_engine↔runner` 循环导入（独立一刀，方法论归位的前置）；
- 不动 5 个既有触点的实现（已是最窄形态）。

## 5. 测试计划

| # | 用例 | 断言 |
|---|---|---|
| 1 | 平台 7 子层 × 研究域 5 成员全禁 | 违反给出文件:行号 |
| 2 | 触点白名单 | 白名单外的研究域顶层文件 import 平台即失败 |
| 3 | `_archived` 豁免 | 隔离区不参与 |
| 4 | 白名单子层精确 | 触点文件 import 白名单外的子层也失败 |
| 5 | 既有门禁不回退 | 原 15 个架构测试全绿 |

## 6. 验收对照

| 门 | 判定 |
|---|---|
| 行为等价 | 纯测试+文档刀，零产品代码改动 |
| 测试不减 | inventory 0 removed |
| 失败集不扩大 | c1_gate 对基线 |
