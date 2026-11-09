# 批次 12 设计 · B3 阶段 2（减法：翻默认 + LoopSpec + mixin 解体）

> **上位设计**：`unified-loop-adoption-eval-20261107.md` §3 B3 阶段 2。
> **性质**：与阶段 1（加法）相反，本批全为**减法/改造**——风险最高。
> **拆分**：三小步，每步全量回归 + 可独立回滚。

---

## §0 现状实测

```
loop.py             1,343 行   ← 目标 ≤400
  └ __init__ 参数   87 行     ← 目标：LoopSpec dataclass
loop_compaction.py    284
loop_lifecycle.py     123
loop_progress.py      280
loop_stream.py        261
loop_telemetry.py      93
loop_tools.py         389
                      ─────
mixin 合计          1,430 行
```

阶段 1 新建：`skeleton.py` 319 / `skeleton_adapters.py` 176 / `deciders.py` 227 /
`modes.py` 107 / `_core_types.py` 162 / `step_adapters.py` 85。

---

## §1 三个小步（顺序不可换）

### S1 · 翻默认（最小风险，最高价值）

| 动作 | 细节 |
|---|---|
| `arun(engine=None)` 默认改 `"skeleton"` | 环境变量 `AGENTNODES_ENGINE=legacy` 可回退；`AGENTNODES_SKELETON=0` 保留为兼容别名 |
| 等价场景扩到 6 个 | 现有 3 个 + **tool 失败**（H2 deny 路径）+ **budget 耗尽**（`budget_max_seconds=0`）+ **history 多轮** |
| 验收 | 6 场景双引擎一致 + 全量 0 failed + **capability_smoke + E6 全过**（骨架下 E1–E5 仍成立）|

**为什么先做 S1**：骨架只有在默认路径上跑，才会被所有既有测试真实覆盖。
影子模式下它只在 3 个专门测试里被跑到——**这就是"有管道没水"的翻版**。

### S2 · LoopSpec 收拢构造参数

| 动作 | 细节 |
|---|---|
| 新增 `kernel/agent/loop_spec.py` | `LoopSpec` dataclass，87 行构造参数分 6 组：**预算**（token/cost/seconds/价格）/ **行为**（max_iterations/stream_mode/wrap_up/session_id）/ **能力**（registry/workspace/strategy/compact_config）/ **权限**（evaluator/gateway）/**钩子**（on_event/event_bus/injectors）/**开关**（claim_validation/goal_injection/hypothesis）|
| 改造 `AgentLoop.__init__` | 签名变 `__init__(self, spec: LoopSpec | None = None, **legacy_kwargs)`——**保留全部旧参数**（`**legacy_kwargs` 转 spec），现有 200+ 调用点零改动 |
| 验收 | 全量 0 failed（**证明向后兼容**）+ 新增 `test_loop_spec.py`（6 组字段映射 + 旧参数等价性）|

**关键约束**：这一步**不删任何旧参数**——只加新入口。删旧参数是阶段 3
（等所有调用点迁移完）。

### S3 · mixin 解体（最高风险，最后做）

| mixin | 去向 |
|---|---|
| `loop_tools.py`（389）| → `ToolActor`（已存在于 skeleton_adapters）+ 工具执行逻辑内联到 adapter |
| `loop_compaction.py`（284）| → `CompactionHandler`（新 StepHandler）+ `CompactionHook` |
| `loop_stream.py`（261）| → `StreamingLLMReasoner(StreamingHandler)`（用 `FnStreamingHandler` 包）|
| `loop_progress.py`（280）| → `ProgressHook`（hash 窗口 + approval gate——**阶段 1 记录的差异在此收编**）|
| `loop_lifecycle.py`（123）| → `LifecycleHook`（超时/上限/停止/指标）|
| `loop_telemetry.py`（93）| → `TelemetryHook` |

**验收**：loop.py ≤400 行 + 全量 0 failed + 6 场景双引擎一致 +
E1–E5/E6 全过。**mixins 保留为薄兼容层一批**（阶段 3 删）。

---

## §2 阶段 1 遗留差异的收编点

| 差异 | 收编位置 |
|---|---|
| no-progress approval gate（legacy 有、skeleton 无）| **S3** `ProgressHook`——把 `_check_no_progress` 的 approval 交互搬进 hook，`NoProgressDecider` 消费其结论 |
| 迭代超时（per-step 包装器，非检查点）| **S3** `LifecycleHook` |
| 文本终答的 `_handle_stop` 尾语义 | **S2** `LoopSpec` 落 finished_reason 策略；**S3** 收进 FinalizeHook |
| maybe_await 三处同型 | **S3** 统一引 `_core_types.maybe_await` |

---

## §3 回滚策略

| 步 | 回滚方式 |
|---|---|
| S1 | `AGENTNODES_ENGINE=legacy` 环境变量（无需改码）|
| S2 | LoopSpec 是**新增入口**，旧参数路径保持——不想要就不传 spec |
| S3 | mixins 保留薄兼容层一批，出问题 revert 该 commit 即可恢复 |

---

## §4 本批执行顺序

1. **S1**：翻默认 + 扩等价到 6 场景 → 全量 → 提交
2. **S2**：LoopSpec + 兼容层 + 测试 → 全量 → 提交
3. **S3**：mixin 解体 → 全量 → 提交

每步独立 commit，独立可回滚。

## §5 明确不做

- 删旧构造参数（阶段 3）
- 删 mixin 文件（阶段 3，先留薄兼容层）
- llmwikify 反向依赖接入（单独批次，它有生产流量）
- 骨架的 sync 路径（`run()`）——保持 legacy（sync 桥在阶段 3）
