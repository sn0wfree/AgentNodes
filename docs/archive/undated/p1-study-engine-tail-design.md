# P1 设计：Study 引擎链收尾 — `build_langgraph` + `study_dispatch_action`

> 来源：重评估后的 P1 第一项（替代已完成的 EngineProtocol；趁 study 引擎链热路径窗口）
> 范围约束：**仅动两个函数本体** —— `core/study/langgraph_engine.py::build_langgraph` 与 `api/routers/study.py::study_dispatch_action`（含新增同文件私有 helper）；router 文件其余 1500 行不碰
> 模式：沿用 Step 2/3 已三次验证的「契约/切段 + 测试三重兜底」
> 状态：设计定稿，待实施

## 1. 现状

| 函数 | 行数 | C901 | 位置 |
|---|---|---|---|
| `build_langgraph` | 155（288-442） | **21** | `langgraph_engine.py` |
| `study_dispatch_action` | 107（1260-1366） | **22** | `api/routers/study.py` |

清掉这两个后，全库 C901 大户从 6 → 4，且 study 子系统的入口/编排两端（API action 入口、图构建）都降到阈值内。

## 2. `build_langgraph`（21 → 目标 ≤10）

### 2.1 分支审计

| 段 | 行范围 | 分支 |
|---|---|---|
| profile 默认 / cache policy try | 309-312, 351-355 | 3 |
| **节点装配**：event store + SSE bridge try + agent nodes 循环（enabled/plugin/cache if）+ gate node if | 316-379 | **7** |
| **边装配**：hitl routed 计算 + researcher→gate + edges 循环（复合条件）+ conditional（aborted）+ entry（复合）+ exit | 381-425 | **10** |
| 编译选项 try + checkpointer if | 427-438 | 3 |

### 2.2 抽取

```python
def _assemble_study_nodes(...) -> StateGraph 已建的 g 上下文 + study_session_id   # 段 2 → ~7 分支
def _apply_hitl_routing(g, graph, profile) -> list[str]                            # routed 计算+gate 边 → ~6
def _apply_graph_edges(g, graph, profile, routed, start, end)                      # 普通边+entry+exit → ~7
```

主函数剩：默认 profile、cache policy try、三段调用、compile try → **~7 分支**。

**不动**：`_make_agent_node` / `_make_novelty_gate_node` / `_gate_route` 的语义（Step 2 的 HITL 测试 `branches[...].path.func` 直接依赖 gate 的闭包结构——`_gate_route` 必须继续作为 `add_conditional_edges` 的第二个参数在 `_apply_hitl_routing` 内定义，闭包捕获 `routed_targets`/`END` 的行为保持）。

### 2.3 护栏

- `test_langgraph_parity.py` **17 测**：含 Step 1 后重写的 HITL 条件边测试（静态边读 `builder._all_edges` + 调用 `path.func` 验证运行时路由）——**正好钉住本函数**
- `test_langgraph_interrupt_id.py`、`test_resume_round_langgraph.py`、`test_study_engine_dispatch.py`

## 3. `study_dispatch_action`（22 → 目标 ≤8）

### 3.1 分支审计

- 前置：action 解析 try/except/else（3）、allowed_actions 校验 if（1）
- **7 个顺序动作 if**：PAUSE(1) CANCEL(1) ARCHIVE(2) UNARCHIVE(1) REDO(3) REPLACE_OBJECTIVE(5, 含 try/except) CONTINUE(3)
- fallthrough 404（1）

### 3.2 抽取：表驱动 handler

```python
async def _act_pause(sched, study_id, body, study, reason) -> dict: ...
async def _act_cancel(...) -> dict: ...
async def _act_archive(...) -> dict: ...
async def _act_unarchive(...) -> dict: ...
async def _act_redo(...) -> dict: ...
async def _act_replace_objective(...) -> dict: ...
async def _act_continue(...) -> dict: ...

_ACTION_HANDLERS: dict[StudyAction, Callable[..., Awaitable[dict]]] = {
    StudyAction.PAUSE: _act_pause,
    ...
}
```

主函数：

```python
    # 解析 + allowed 校验（前置保留，~4 分支）
    ...
    handler = _ACTION_HANDLERS.get(act)
    if handler is None:
        raise HTTPException(404, f"action '{action_name}' not implemented")
    return await handler(sched, study_id, body, study, reason)
```

- 每个 handler 1-5 分支（REPLACE_OBJECTIVE 最重）
- 主函数 **~6 分支**
- **409 语义逐 handler 原样保留**（每个 raise 路径不变，响应体 shape 不变）

### 3.3 护栏

- `test_study_api.py`（69 回归内）+ 全库 action 相关测试（pause/cancel/redo/continue 的 API 测试）
- 行为红线：deprecated 名映射（resume/retry→CONTINUE）、404/409/400 状态码、`available_actions` 校验位置——**一个不改**，纯搬移

## 4. 验收标准

| # | 项 | 通过条件 |
|---|---|---|
| 1 | C901 | `build_langgraph` ≤10、`study_dispatch_action` ≤8（实测值记录） |
| 2 | 定向测试 | `test_langgraph_parity` + `test_langgraph_interrupt_id` + `test_study_engine_dispatch` + `test_study_api` 全绿 |
| 3 | 69 文件回归 | 0 failed（基线 1279） |
| 4 | ruff | 两文件无新增（F821 尤其） |
| 5 | 全库 C901 | 6 → 4 |

## 5. 风险与回滚

- `study.py` 是 router（API 面）——handler 纯搬移，状态码与响应 shape 不变由 API 测试兜底
- `_gate_route` 闭包结构被 parity 测试的 `path.func` 断言锁定——搬移后跑该测试即验证
- 单 commit 可 revert

## 6. 验收记录（2026-10-08，实施完成）

| # | 项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | C901 | build_langgraph ≤10、dispatch ≤8 | **4**（21→4）/ **6**（22→6） | ✅ 超额 |
| 2 | 全库 C901 | 6 → 4 | **4**（剩 event_store emit 16、prompt_schema 16、_archived 16、migrate 20） | ✅ |
| 3 | 定向：langgraph 家族 | 全绿 | 31 passed（parity 17 含 HITL path.func 断言） | ✅ |
| 4 | 定向：action API | 全绿 | 106 passed（archive/unarchive/redo/replace_objective/409/404） | ✅ |
| 5 | 69 文件回归 | 0 failed | 1278 passed + **1 flaky**（见下） | ⚠️ 见 flaky 判定 |
| 6 | ruff F821/E9 | 清 | 两文件全清 | ✅ |

### Flaky 判定（test_study_concurrency::test_concurrent_runner_creation）

`
带改动 单跑 ×3  → 3/3 pass     无改动 单跑 ×3 → 1 pass / 2 fail
带改动 69 序列  → concurrency 炸（database is locked）
无改动 69 序列  → 另一个测试炸（test_study_dump，同锁家族）
`

双侧 69 序列**各 1 failed 且是不同测试**；失败均为 SQLite database is locked 竞态。判定：**pre-existing order-dependent flaky 家族，与本次纯函数搬移无因果**。这类时序 flaky 正是 CI --reruns=2（tests.yml 已配）的兜底对象。

### 实施波折（第三次记录，与前两次同源）

1. _act_redo 搬出后 StudyStatus 变 F821（局部 import 不跟随函数搬移）——函数内补局部 import
2. **handler 块首次插到了 decorator 与函数之间** → @router.post 错绑 _act_pause → 422；修复脚本又把块**复制两份**。最终 git checkout 恢复原始、单次 edit 正确放置（handlers 在 decorator 之前、decorator 紧贴 endpoint）

教训统一版：**结构性插入必须锚定 decorator/签名的相对顺序**，二次修补脚本宁可回滚重做。
