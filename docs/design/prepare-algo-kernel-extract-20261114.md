# prepare.py 评估算法提取到 kernel（方案，R-014）

> 2026-11-14 · 维护者拍板 C 方案（提取到 kernel）· 上游会话：B2-① / e2e 车道修复（dd3b652b）

## §0 结论

1. **提取对象**：`templates/prepare.py`（本地未跟踪副本）里的**评估算法闭包**
   → `src/agentnodes/kernel/algo/backtest.py`（新子包，领域中立算法带）。
2. **唯一幸存副本**：tracked 版（根 `templates/`）已随 W1 `9140f6c3` 从 git 移除；
   `src/agentnodes/templates/` 只有 `.prompts/.skills/workflows`，从无 prepare.py。
   本地副本相对最后 tracked 祖先漂移 +164/−125 行——**漂移里含 2026-11-05
   无前视修复本身**，祖先反而是带 bug 旧版。⇒ 本地副本不可从 git 历史恢复，
   提取必须先行、副本删除必须最后（R-020）。
3. **测试**：`tests/test_prepare_no_lookahead.py` 是该算法的唯一消费者，
   当前**全绿**（pandas 3.0.6，2 passed）。随提取迁移为
   `tests/test_backtest_no_lookahead.py`：importlib 文件加载 → 正常 import，
   `compute_factors` 模块桩 → `factor_frames` 参数注入；**两条时序断言一字不动**。
4. **冻结基线**：改名会替换 2 个 nodeid（`pr-lane-nodeids.json`）并孤儿化 1 条
   已知失败条目（`pr-lane-failures.json`）。CI 不调用这两道闸门（手动工具），
   但 `c1_inventory verify` 对任何 REMOVED 返回 1，其工具规约要求
   「refreeze 在同一 commit」——本批对树的测试改动恰好只有这个改名，
   **等量置换的手工更新与真实 refreeze 数学等价**，由两道元测试兜底结构校验。
   注意：改名后的测试现在是**通过**的，其失败基线条目应**删除**而非搬运。

## §1 现状真值（全部实测）

| 项 | 值 | 证据 |
|---|---|---|
| 本地副本 | 17099 bytes / 482 行 / 16 个顶层函数 | `Read templates/prepare.py` |
| 最后 tracked 祖先 | `9140f6c3^` = 15996 bytes | `git show` + sha256 对比 |
| 漂移 | +164/−125（含无前视修复） | `git diff --no-index --stat` |
| 测试状态 | 2 passed（0.99s） | `pytest tests/test_prepare_no_lookahead.py` |
| pandas | 3.0.6（核心依赖 `pandas>=2.1.0`） | pyproject + 实测 |
| kernel pandas 现状 | 仅 `memory/persistent.py` 惰性 import numpy | findstr 精确扫描 |
| metrics_raw.json / AN_RUN_DIR 契约 | src+tests **零读者**（死契约） | findstr |
| prepare.py 运行时消费者 | 无（file_tools.py:89 只提 templates/strategy.py） | findstr |
| test_e2e_comprehensive 的 rebalance 引用 | 无（早前 grep 误报） | findstr 复核 |
| 闸门调用方 | CI 零调用；run_test_matrix 注释引导手动用 | findstr `.github` + `scripts` |

## §2 提取闭包

**提取**（→ `kernel/algo/backtest.py`）：

| 函数 | 处置 |
|---|---|
| `evaluate` | 公开；签名 +`factor_frames: dict | None = None`（注入点） |
| `_compute_factor_scores` | 私有原样；NOTE 单一来源打分铁律 docstring 保留 |
| `_simulate_backtest` | 私有原样；**时序约定 + 2026-11-05 修复记录 docstring 原样保留** |
| `_empty_metrics` | 私有原样；GOAL_METRIC 占位键改为固定键集 |
| `_ann_return/_max_drawdown/_sharpe/_ann_vol/_sortino` | 私有原样 |
| `_compute_simple_factors` | 升公开 `compute_simple_factors`（纯路径，不再退化链） |

**不提取**（各自理由）：

| 函数 | 理由 |
|---|---|
| `load_data` / `_generate_sample_data` | I/O + workspace 路径耦合；kernel 无 I/O |
| `compute_factors`（真实版） | 依赖已删的 `agentnodes.apps.compute_factor`（死依赖） |
| `_persist_factor_failures` | 文件副作用；失败信息改放返回 dict（行为变化 ②） |
| `save_metrics` | metrics_raw.json / AN_RUN_DIR 契约零读者（死契约） |
| `_turnover` | 模板内从未被调用的死函数（evaluate 内联计算 turnover） |

## §3 行为变化（相对模板版，逐条显式）

1. **因子来源注入化**：`factor_frames=None` 时用 `compute_simple_factors`
   （纯 pandas 路径）；不再尝试已删的 apps.compute_factor。
2. **factor_failures 不写文件**：放进返回 dict（调用方可见）；注入的
   frames dict 先拷贝再 pop（不污染调用方输入——模板版拥有自己的 dict）。
3. **指标键固定**：`calmar/sharpe/max_dd/ann_return/ann_vol/sortino/turnover/trades`
   （+可选 `factor_failures`）；GOAL_METRIC 占位符机制留在模板时代。

## §4 放置与层纪律

- 新子包 `kernel/algo/`（领域中立算法）。kernel 现有子包均为域名词
  （agent/context/events/…），evolution 是 agent 自进化域，不放组合数学。
- 层纪律：纯 pandas/numpy，零跨层 import，`tests/test_layer_matrix.py`
  方向检查天然合规（pandas/numpy 已是核心依赖，零新增依赖成本）。
- `kernel/__init__.py` 三带清单补 `algo`（呈现维度）。

## §5 测试与基线处置

- `git mv tests/test_prepare_no_lookahead.py tests/test_backtest_no_lookahead.py`；
  加载机制改写，断言不动。
- `pr-lane-nodeids.json`：删 2 旧 nodeid、加 2 新 nodeid、排序保持、count 不变。
- `pr-lane-failures.json`：**删除**孤儿条目（该测试现在通过，搬运会谎造已知失败）；
  `counts.total` 与 per-file 计数同步 −1。
- `.shard-timings.json`：timing 键改名（0.05s，pytest-split 对未知文件优雅回退，
  同步只为分片准确性）。
- 结构校验网：`test_committed_inventory_is_consistent`（count==len、去重、格式）
  + `test_baseline_file_parses`（known_failures 非空、total==len、格式）。

## §6 验证表

| 项 | 命令 |
|---|---|
| 时序回归 | `pytest tests/test_backtest_no_lookahead.py -v` |
| 基线结构 | `pytest tests/test_c1_inventory_gate.py tests/test_c1_gate_tools.py -v` |
| 层纪律 | `pytest tests/test_layer_matrix.py` |
| AST 死码扫描（含新 kernel 文件） | `pytest tests/test_dead_code_no_imports.py` |
| 循环契约回归 | `pytest tests/test_step_contract.py` |

Windows 全量套件既有 DLL 环境账（与本批无关，A/B 已判）不重跑。

## §7 回滚

单 commit 回滚即全恢复（新子包 + 测试改名 + 基线置换原子绑定）。
`templates/prepare.py` 本地副本的删除在验证全绿+用户确认后执行（R-020 最后工序）；
删除前 sha256 已记录（272b50ddd834fe3a），且提取后的 kernel 模块+测试
构成其全部独有内容的归宿。

## §8 遗留账（不在本批）

- `src/agentnodes/__init__.py:9` 注释仍称 templates 根含 strategy.py/prepare.py——
  W1 之前的历史描述，已失真（现仅 .prompts/.skills/workflows），随后续文档批修正。
- `pr-lane-failures.json` 其余条目的现势性（pandas 3.0.6 下可能大量过时）——
  需 WSL 环境全量 PR-lane 真实 refreeze，非本批。
