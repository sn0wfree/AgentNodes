# 档 C · 待 R2 恢复接口层（10 个文件）

> 批次 2「R1 泳道」B 流处置记录，2026-11-06。
> 本文件列的测试**不搬回 `tests/`**：它们 import 的 L1 接口层模块随 chat 接线
> 一起归档，要到 **R2（chat 接线：slash / GoalPanel / SSE / API 路由）** 才恢复。
> 处置原则：**随 R2 同生共死** —— R2 恢复对应模块时，把这些文件连同本文件的
> 依赖行一起改回 `agentnodes.` 前缀搬进 `tests/`。

## 判定方法

1. AST 抽取每个文件的全部 import（含函数体内延迟 import）；
2. 把 `strategy_research.` 换成 `agentnodes.` 后逐个检查
   `src/agentnodes/` 下是否存在对应 `.py` / `__init__.py`；
3. 对通过检查的再**实跑** `pytest <file> --collect-only`，collect 成功不等于
   运行期可达（延迟 import 会在用例体内炸），所以只有「搬回后实跑全绿」才算
   可达（见下方 `test_goal_panel` 的重分类）。

## 清单

| 文件 | 行数 | 阻塞模块（`agentnodes.` 前缀） | 状态 |
|---|---:|---|---|
| `test_api_goal_router.py` | 574 | `interfaces/api/routers/goal.py` | 待 R2 |
| `test_cli_goal_cmd.py` | 171 | `interfaces/cli/commands/slash_goal.py` | 待 R2 |
| `test_dag_view_widget.py` | 141 | `interfaces/cli/tui/widgets/dag_view.py` | 待 R2 |
| `test_goal_workflow_v051.py` | 222 | `interfaces/cli/commands/slash_goal.py`（`_checkpoint_base_dir` / `_user_workflows_dir` / `cmd_help`） | 待 R2 |
| `test_goal_workflow_v052.py` | 306 | `interfaces/cli/tui/workers/workflow_worker.py`、`interfaces/cli/tui/app.py` 的 workflow worker 接线 | 待 R2 |
| `test_workflow_approval_api.py` | 346 | `interfaces/api/routers/workflow.py`（另用 `interfaces/api/session/web_session_db.py`、`kernel/workflow/{builtin,executor}.py` —— 后三者已存在） | 待 R2 |
| `test_workflow_control_api.py` | 215 | `interfaces/api/routers/workflow.py` | 待 R2 |
| `test_workflow_graph_api.py` | 87 | `interfaces/api/routers/workflow.py` | 待 R2 |
| `test_workflow_ownership.py` | 262 | `interfaces/api/routers/workflow.py`（模块级 import `auth_tokens` 可用，但 fixture 里 `import agentnodes.interfaces.api.routers.workflow as wf` 硬依赖） | 待 R2 |
| `test_workflow_sse_stream.py` | 199 | `interfaces/api/routers/workflow.py`（`workflow_event_stream`；`apps/goal/event_bus.py` 已存在） | 待 R2 |
| ~~`test_goal_panel.py`~~ | ~~243~~ | ~~无~~ —— `interfaces/cli/tui/widgets/goal_panel.py` 与 `tui/session.py::ChatSession` **均已存在** | **已按 A 档搬回** |

合计 **10 文件 / 2,523 行**仍留在归档目录（不含已移出的 `test_goal_panel.py`）。

### 与任务清单的差异（实测修正）

任务给的档 C 清单是 10 个（含 `test_goal_panel`、不含 `test_workflow_ownership`）。
AST + 改名副本实跑后的结论：

- `test_goal_panel.py` → **移出档 C**（模块都在，28 用例全绿，已搬回）；
- `test_workflow_ownership.py` → **补入档 C**（模块级 import 不炸，但 fixture
  体内 `import agentnodes.interfaces.api.routers.workflow` 硬依赖，8 个用例全
  ERROR）。它本来不在 10 个的枚举里，属清单遗漏。

`test_goal_panel.py` 重分类的实测依据：

- 它 import 的 `agentnodes.interfaces.cli.tui.widgets.goal_panel`、
  `agentnodes.apps.goal.templates`、`agentnodes.interfaces.cli.tui.session.ChatSession`
  在**当前树上全部存在**；
- 改名后搬回 `tests/`，**28 个用例全通过**（0 skip / 0 fail）；
- 同批搬回的 `test_goal_workflow_phase3b.py` 也 import `GoalPanel`，15 个用例全通过。

结论：`goal_panel.py` 本身没有随 L1 归档（只做了包名重命名），所以这个文件
不属于「随 R2 同生共死」。**已搬回**，按 `collect 通过数只许涨` 的方向性锚，
这是纯增量覆盖。若 R2 重写 GoalPanel 的对外 API，届时按普通测试维护即可，
不需要再走本文件。

## 搬回步骤（R2 完成时照抄）

```bat
REM 1) 前置自检：上表「阻塞模块」全部存在
REM 2) 逐文件改名 + 搬运（strategy_research. -> agentnodes.）
python -m pytest tests/<file>.py -q --no-header
```

注意 R2 有两个本批未触发的已知前置（见 `docs/design/goal-capability-merge-design-20261105.md`
与 `framework-and-principles-20261105.md` §2.5）：

- **A5 双头环境变量**：`AN_GOAL_DB_PATH` vs `AGENTNODES_GOAL_DB_PATH` 未统一，
  `tests/conftest.py::_isolate_workspace_env` 目前只设后者；R2 的 API 测试若读
  `AN_GOAL_DB_PATH` 会写真实 DB。档 C 里 `test_workflow_ownership.py` 另用
  `SR_WORKSPACE_PATH` / `SR_ADMIN_TOKEN`（批次 9b 去研究化的旧前缀），搬回时
  需一并改成 `AN_*`。
- **GoalPanel / SSE 的 L1 依赖**：R2 恢复时按上表逐个补模块，不要一次性搬全部
  10 个文件（历史教训：一次搬回整批接口测试会把「模块缺失」和「行为回归」两种
  失败混在一起，无法定位）。
