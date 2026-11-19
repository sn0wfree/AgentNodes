# WP-8 · apps/evolution 迁移实施清单与执行记录（2026-11-20）

> 依据：l1-three-frontends-one-core-design-20261119.md 批 3b（M4）、
> roadmap WP-8；只读审计（多 agent）产出的实施清单 + 子批执行记录。
> 分批纪律：3b.1-3b.4 各自独立提交/回滚，不合并。

## 1. 归属判定（审计结论）

**整体迁（rsi_cli.py 617 行，领域层）**：预算常量（:46-:60）、
`CycleOutcome`（:66-:86）、状态三件 `state_path`/`load_state`/`save_state`
（:92-:150）、预算闸 `check_budget`/`record_cycle` 等（:156-:215）、
证据门 `collect_reports`/`select_signature`/`resolve_objective`
（:221-:287）、驱动辅助 `_GwProxy`/`_session_emitter`/`_resolve_gateway`
（:293-:337）、`_select_evidence`（:352-:379）、`run_once`（:498-:597）。

**留 interfaces（装配根）**：`rsi_wiring.py` 的 `RsiLoop`（:30-:149）、
`build_rsi_loop`（:159-:252）、`_resolve_seed`/`_heldout_kwargs`/
`_build_replay`/`_make_applier`。CLI 薄命令 `cli/commands/rsi.py` 与
`goal_settled_consumer.py` 留 L1。

**策略件（3b.1 已迁）**：`_baseline_metric`/`_aggregate_baseline`/
`_median`/`_genome_version_id` → `apps/evolution/baseline_policy.py`。

**带病件裁决（不静默搬家）**：`rsi_wiring._baseline_from_source`
（:546-:564）零调用死代码——留原地，列删除批候选（R-020）；
`_version_of`（:152-:156）读错字段恒空串（framework-review F9）——
3b.3 顺手核对修正。

## 2. 硬约束

1. **halt 改 callable 注入**（L2→L1 上行禁止）：`rsi_cli._gate_halt`
   （:343 import `interfaces.cli.halt`）与 `rsi_wiring._heldout_kwargs`
   （:294 import `trip_halt`）迁入 apps 后必须由 L1 调用方注入
   callable——签名链 `cli/commands/rsi.py` / `goal_settled_consumer.py`
   → `run_once`/`build_rsi_loop` 跨 3 层，**3b.2 一个批内改完**。
2. **懒 import 形态**：`run_golden_eval` 保持函数内 import——
   `test_golden_eval.py:270` 的模块属性 patch 依赖它（已写入
   baseline_policy.py 模块 docstring 作为约束）。
3. **`Path(__file__).parents[1]` 敏感**（`_build_replay` :336-:339 定位
   evals）：文件不迁所以 3b.1 无影响；3b.3 若动 `_build_replay` 须改
   `parents[2]` 或用 `agentnodes.evals.__file__` 定位，否则静默降级。
4. **零垫片**（设计原则 3）：`rsi_cli.__all__` 15 符号、消费方仅 3 处
   懒 import（commands/rsi.py:38,49-54、goal_settled_consumer.py:131、
   rsi_cli.py:385）——直接改指向，不留 re-export。
5. **无外部进程面**：全量核实无 subprocess/Popen（审计确认）。

## 3. 小批序列与状态

| 子批 | 内容 | 状态 |
|---|---|---|
| 3b.1 | BaselinePolicy 抽类（纯策略零行为变化）+ 新包 + 直测 | ✅ 2026-11-20 |
| 3b.2 | 预算闸/状态/证据门/`run_once` 下沉 + halt 注入 + 删 rsi_cli.py + 补模块内单测（现状零覆盖） | 待做 |
| 3b.3 | wiring 收敛（只剩构造）+ `parents` 路径核对 + 锚点终审 | 待做 |
| 3b.4 | 架构清单与文档收尾（semantics_placement「循环驱动」条目核对、docstring 文案、体量数字） | 待做 |

## 4. 3b.1 执行记录（2026-11-20 · 已实施）

- 新包 `apps/evolution/`（`__init__.py` 纯 docstring，零 re-export）+
  `baseline_policy.py`：`BaselinePolicy` 可测类——滚动中位数窗口
  （WINDOW_SIZE=5）/ genome_version 分区失效 / `ReplayOutcome` 各维度
  独立中位数汇总 / skipped 轮不入窗，**自 rsi_wiring.py:425-:543 逐行
  等价迁入**；`run_golden_eval` 保持函数内懒 import（约束 2）。
- `rsi_wiring.py`：顶部加 `from agentnodes.apps.evolution.baseline_policy
  import BaselinePolicy`（L1→L2 下行合法）；`_baseline_metric` 改薄
  委托（签名不变，调用点 :237 与旧测试锚 :250/:271 零改动）；
  `_baseline_from_source` 死代码留原地（§1 裁决）。
- `scripts/arch/layer_matrix.py`：加显式条目 `"agentnodes.apps.evolution":
  "L2"`（对齐 capabilities 等既有显式风格）。
- 新测试 `tests/test_evolution_baseline_policy.py` 8 用例：镜像
  TestBaselineRollingMedian 6 例打新路径 + 窗口有界序列断言（修正过
  一版断言：窗口保留最近 5 观察，第 7 轮中位数 0.5 非 0.9；浮点用
  approx）+ 薄委托等价断言（isinstance + 首轮值）。

验证：RSI 回归 5 文件 118 passed（含 golden_sets halt 注入样板回归）；
全量 `-n 12` 与 batch_gate 见提交前最终记录；数字同步 L2 +2 文件。

## 5. 3b.2-3b.4 前置清单（审计要点备查）

- 测试锚点现状：rsi_cli 全部符号 **零 patch/零 import**（三道门无单测，
  3b.2 必须补：halt/预算/证据三道门判定表、check_budget/record_cycle、
  state_path env 覆盖、_make_skipper 落盘计数）；wiring 锚仅
  test_production_wiring.py:167/:175（build_rsi_loop 留 L1 不变）与
  test_golden_eval.py:250/:310（3b.3 终审）。
- env 登记面：`AGENTNODES_RSI_STATE_PATH` 已登记（settings.py:258）；
  `AGENTNODES_RSI_PER_SESSION_LIMIT`/`_DAILY_LIMIT` 走裸 env + 模块常量
  形态（check_env_access 豁免模式），迁移时保持 `_NAME_ENV = "..."`
  常量形态。
- logger 名随包变（agentnodes.interfaces.* → agentnodes.apps.evolution.*）：
  排查口径变化，门禁不受影响。
- gateway 私有耦合 `gw._combined_sink`（rsi_wiring:174、rsi_cli:315-324
  `_GwProxy` object.__setattr__ 伪装）：登记为已知耦合，不扩大。
- semantics_placement.yaml:146-153「循环驱动」条目含
  `interfaces/rsi_wiring.py`（留 L1 不变）；apps/evolution 新文件不在
  表内，无需登记（判定器只断言已列文件）。
