# workflow 测试 SQLite DB 锁修复（方案，R-014）

> 2026-11-14 · 承 L2 教训账「2026-11-13 · workflow 测试 SQLite DB 锁（既有 flake，未修）」
> · 上游：B2-① / e2e 车道（dd3b652b）/ C 方案提取（6f3e727c）

## §0 结论

1. **根因比教训账再深一层**：不是「组内用例共享 DB」的测试卫生问题，而是
   **`WorkflowRunner` 与其 workspace 脱钩**的生产语义缺陷——
   `executor.py:69/147` 的 store 默认 `WorkflowStore()` → `store.py:101`
   回落 `Path.cwd()/workflows.db`，与 runner 手里的 `self.workspace` 无关，
   与类自家 docstring `"one DB per workspace"` 矛盾。测试全部传了
   `tmp_path` workspace（自以为隔离），DB 却全落 cwd。
2. **修复是一行级语义修正**：runner 两处默认改为
   `WorkflowStore(db_path=resolve_workflows_db_path(workspace))`。
   生产零爆炸（唯一构造点 `api/routers/workflow.py:633` 显式传 store；
   `GoalWorkflowRunner` 是 apps/goal 的另一套类，不受影响）。
3. 修复后，per-test `tmp_path` workspace 自动给出 per-test DB——
   教训账的防法（用例级 DB 隔离）无需逐用例注入 env，由语义修正**自动达成**。

## §1 真值表（全部实测，2026-11-14）

| # | 事实 | 证据 |
|---|---|---|
| 1 | store 默认路径 = `Path.cwd()/workflows.db` | `store.py:88-101` |
| 2 | runner 两处默认 `WorkflowStore()` 无参 | `executor.py:69`（`__init__`）、`executor.py:147`（`resume_from_store`） |
| 3 | runner 持有 `self.workspace`（L67，在 store 构造前赋值） | `executor.py:66-69` |
| 4 | 测试传 `tmp_path` 作 workspace，但仅用于 `load_definition`；~14/18 处未传 store | `test_workflow_segment_loop.py` 全文扫描 |
| 5 | 组内（xdist_group="workflow"）4 文件 23 处 runner 构造；组内显式 store 的文件已隔离 | compiler_branches / graph_shadow / resume_from_store 全显式 |
| 6 | 组内 commit 纪律完好（9 个写方法全 commit）——锁残留非未提交事务 | `store.py` 写方法扫描 |
| 7 | 连接持久缓存、无 WAL、无自定义 busy_timeout（默认 5s） | `store.py:108-113` |
| 8 | 单跑一个 runner 测试 → cwd/workflows.db 实增 +12288 字节 | 本日机制演示（test_full_auto_run） |
| 9 | 仓库根残留 `workflows.db` 27,578,368 字节（27.5MB，历史测试累积）；`*.db` 被 .gitignore:73 覆盖 | 本日实测 |
| 10 | 生产构造点唯一：`workflow.py:633` 且显式传 store；goal 系是另一套类 | src 全仓 grep（GoalWorkflowRunner 为子串误匹配，已排除） |
| 11 | approval API 测试经 app 层可达 `resume_from_store(store=None)` 默认路径 | `test_workflow_approval_api.py` 无直接构造 |

## §2 根因链

```
WorkflowRunner(store=None) ──→ WorkflowStore() ──→ cwd/workflows.db（共享）
        │                                        │
        │ 同一 worker 内：4 文件 23 个 runner ≈ 20+ 个持久连接同指一文件
        │ 跨进程：approval API 测试（组外）经 resume_from_store 默认路径同指一文件
        ▼
全量 -n 12（R-007 内存态）+ 27.5MB 文件持续膨胀 + 每个 store 实例连接时
重跑 _init_schema/_migrate（PRAGMA+潜在 ALTER）──→ 写锁等待 > 默认 5s busy timeout
──→ sqlite3.OperationalError: database is locked ──→ TestAutoFlow 轮换红
```

教训账的观察全部对上：单跑绿 / 文件内并行绿 / 全量偶发红 / HEAD 复现（与 T7 无关）。
`xdist_group` 只挡跨 worker 分派，挡不住「同一 cwd 文件的多进程写」+「同 worker
内多连接竞争」——根因在文件共享本身，marker 粒度救不了。

## §3 方案

**方案 1（采纳）——runner 语义修正，2 行 + 回归网：**

```python
# executor.py:69（__init__，self.workspace 已在 L67 赋值）
self.store = store or WorkflowStore(
    db_path=resolve_workflows_db_path(self.workspace))
# executor.py:147（resume_from_store，workspace 是位置参数）
store = store or WorkflowStore(
    db_path=resolve_workflows_db_path(workspace))
```

- `resolve_workflows_db_path` 已存在于 `store.py:88`，补 import 即可。
- `WorkflowStore()` 的 cwd 默认保留不动（直接构造 store 的用户/CLI 语义不变）。

**否决的备选：**

| 备选 | 否决理由 |
|---|---|
| 测试侧逐用例注入 `AGENTNODES_WORKSPACE`（教训账原防法） | 治标；生产语义缺陷（runner 不跟随 workspace）仍在；~14 处逐个改，噪声大 |
| store 层加 WAL + 加长 busy_timeout | 并发读写下确实缓解，但**不解决数据污染**：共享 DB 下不同测试的 run 记录互相可见（`list_runs`/`get_run` 串台），隔离性靠锁是假隔离。留作 §7 独立账（生产多进程写同一 workspace DB 时再评估） |
| xdist_group 扩员（把 goal/approval 文件也进组） | 扩大串行化、拖慢全量；且 goal 系根本不写这个文件（已排除），approval API 经 app 层的路径在 runner 修复后自然解除 |

**不做（YAGNI，记录观察）：** runner 终态时 `store.close()`（现每 runner 泄漏一个
持久连接；修复后连接各指各的 tmp 文件，Windows tmp 清理由 pytest 兜底）——
待 runner 生命周期有真实需求时一并做。

## §4 行为变化（显式，仅测试语义）

1. runner 未显式传 store 时，DB 位置 `cwd/workflows.db` → `<workspace>/workflows.db`。
   生产调用者（workflow.py:633）显式传 store，**零变化**。
2. `resume_from_store` 默认路径同步修正——重启恢复的锚点从 cwd 变 workspace，
   与 R-B2「重启续跑」的意图一致（重启后 cwd 不同也能找回 run）。
3. 测试隔离性增强：共享 DB 下的潜在串台（跨文件 run 记录互见）随文件分离消失。

## §5 验证表

| 项 | 命令/方法 |
|---|---|
| **R-019 回归网**：新测试钉「runner 写 workspace 而非 cwd」 | 新增用例：monkeypatch chdir 到隔离 cwd → 构造 runner（不传 store）→ run → 断言 `(workspace/"workflows.db").exists()` 且隔离 cwd 下无文件。若有人回退修复，写 cwd → 网变红 |
| 受影响文件全绿 | `pytest tests/test_workflow_segment_loop.py tests/test_workflow_resume_from_store.py tests/test_workflow_compiler_branches.py tests/test_workflow_graph_shadow.py tests/test_workflow_approval_api.py` |
| workflow 域全绿 | `pytest tests -k workflow -q`（Windows 可跑部分） |
| 全量并行复跑原 flake | Windows 阻塞（既有 DLL 环境账）→ WSL/CI `-n 12 --dist loadgroup` 连跑，观察教训账两用例（`test_max_segments_hard_limit` / `test_evaluator_replan_loops_to_planner`）不再轮换红 |
| L2 教训账状态更新 | 「未修，独立批待办」→ 已修 + commit 号 |
| 层纪律/AST/ruff | 常规三件套 |

## §6 回滚

单 commit revert 即全恢复（runner 两行 + 新测试 + 教训账回填原子绑定）。

## §7 遗留账（不在本批）

1. **27.5MB `workflows.db` 残留**：修复后不再增长；删除属清理动作
   （gitignore 覆盖，无 git 记录），待确认后执行。
2. **WAL/busy_timeout 加固**：生产出现多进程写同一 workspace DB 的场景时再评估。
3. **runner 连接生命周期**（终态 close）：待 runner 生命周期需求出现时一并做。
