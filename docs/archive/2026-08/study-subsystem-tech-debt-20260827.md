# Study Subsystem — Outstanding Technical Debt — 2026-08-27

**目的**: 记录 2026-08-26/27 study 子系统全面修复（PR-A/B/C/D/E，`92580b4` … `f89085e`）扫尾阶段仍未处理的 5 项技术债。
每一项给出：是什么 / 在哪里 / 影响 / 建议修法 / 工作量，便于后续排期决定。

**适用范围**: study chat / study runner / langgraph engine / SSE event bus / agent execution。
**关联文档**: `legacy-sessiondb-tech-debt.md`、`chat-agent-refactor-architecture.md`、`study-longhorizon-v2-design.md`。

---

## 总览

| # | 债 | 用户感知 | 数据 | 性能 | 修代码量 | 新测试 |
|---|----|---------|-----|------|--------|--------|
| ~~①~~ | ~~v2 身份重构后 chat 互斥代码死等~~ | ~~无~~ | ~~无~~ | ~~无~~ | ~~已归档~~ | ~~已归档~~ |
| ~~②~~ | ~~EventStoreFactory 粘路径 + 双库分叉~~ | ~~多 workspace 丢 SSE~~ | ~~sessions 行两库分裂~~ | ~~隐性~~ | ~~已修~~ | ~~已修~~ |
| ~~③~~ | ~~legacy Temporal 五件套（1837 行死代码）~~ | ~~无~~ | ~~无~~ | ~~无（import 时）~~ | ~~已归档~~ | ~~已归档~~ |
| ~~④~~ | ~~summary ETag 下发缺失~~ | ~~summary 轮询开销大~~ | ~~无~~ | ~~全量返回~~ | ~~已修~~ | ~~已修~~ |
| ~~⑤~~ | ~~plan-dag 同步 LLM 阻塞事件循环~~ | ~~全站冻结 N 秒~~ | ~~无~~ | ~~N 秒事件循环停摆~~ | ~~已修~~ | ~~已修~~ |
| ~~⑥~~ | ~~test_workflow_segment_loop 并行 flaky~~ | ~~CI 偶挂（手动重跑过）~~ | ~~无~~ | ~~CI 耗时重复跑~~ | ~~已修~~ | ~~已修~~ |

**建议优先级**:
1. ~~⑤（2 行，最高价值：消除 N 秒全站冻结）~~ ✅ 已修
2. ~~④（15 行，修一个作者已承认的 bug）~~ ✅ 已修
3. ~~②（35 行 + 测试，消除多 workspace 部署的隐蔽分叉）~~ ✅ 已修（commit dee6d32）
4. ~~③（5 行 git rm 删 2500 行噪音，但需要最终确认生产零调用）~~ ✅ 已归档（git mv 到 _archived/，3246 行）
5. ①（30 行"考古"清理，对功能零影响，最末做）
6. ⑥（5 行加 xdist_group，消除 CI 偶挂）

**历史归档详情**：参见 `src/agentnodes/core/study/_archived/README.md`（2026-09）。

---

## ① v2 身份重构后 chat 互斥代码死等

### 是什么
v1 时代 `study.session_id` 绑定用户 chat 会话，`is_session_processing(study.session_id)` 用于阻塞 chat 反向调用同一 session 的 LLM/agent 槽。
v2 设计文档显式改为 `studies.session_id == study_id`（单身份 §4.2），目的是把 chat 会话键和 study 键解耦，让 chat 页和 study 页可以**并行跑**而互不阻塞。

但 `_run_one_study_locked` 里那段「chat 互斥」wait 循环**没删**，变成：
- `study.session_id == study_id`（v2 §4.2）
- chat 的 `_processing_sessions` 集合键是 chat 会话 id
- 所以 `is_session_processing(study_id)` 永远 False → while 循环 0 次迭代的死等
- 随后 `mark_session_processing(study_id, True)` 是**自己标自己处理中**，finally 清掉 —— 整个路径是 **dead-but-harmless no-op**

### 在哪里
- `src/agentnodes/core/study/scheduler.py:795-810`（"Cooperative mutex with chat"等待段 + mark）
- `src/agentnodes/core/study/store.py:358-364`（`create_study` 注释：v2 single-identity）
- `src/agentnodes/api/session/service.py:651-690`（`is_session_processing` / `mark_session_processing` 注释：*Phase 1 study scheduler uses this*——"Phase 1" 是 v1 措辞）
- v2 设计权威来源：`docs/archive/undated/study-longhorizon-v2-design.md:212-221`（§5.3 与 chat 的协作）和 §4.2「与 chat 互斥天然解耦」
- v2 §5.3 原话：`mark_session_processing(study_id)` **保留调用（兼容代码路径）**，实际不影响任何 chat 会话

### 影响
- **用户感知**：无（功能按 v2 设计正常：chat 与 study 真并行）
- **数据正确性**：无
- **性能**：无负面影响（0 次循环迭代的开销可忽略）
- **可维护性**：**显著拖后腿**。代码自我矛盾；新维护者读 `scheduler.py:802` 会以为 chat/study 仍互斥，违反直觉；调研成本高

### 建议修法（最小可执行版）
- 把 while 循环改成读 `study.owner_session_id`（如果将来想保留对 chat 的软避让）—— 但这又会和 v2 §5.3 "天然解耦" 矛盾
- 更稳的方案是**直接删 14 行代码**：
  - 删 `scheduler.py:795-810`
  - 同步更新 `scheduler.py:69-72` docstring（"Markup on SessionService" 一段删）
  - 同步更新 `api/routers/study.py:7-9` module docstring（"chat/study mutex works out of the box" 删）
  - 同步更新 `service.py:651-690` docstring（"Phase 1 study scheduler uses this" → "保留供 v1 兼容，新代码勿用" 或标 deprecated）

### 工作量
**~30 行**（含删除 + docstring 修订），**0 个新测试**（行为不变）。

---

## ② EventStoreFactory 粘路径 + 双库分叉

### 是什么
两条独立的 EventStore 实例化路径，可能指向不同的 SQLite 文件；`EventStoreFactory.create()` 还"粘住"第一次调用时的 db_path。

### 在哪里
- `src/agentnodes/core/agent/event_store.py:653-672` `EventStoreFactory.create()`：首次调用时构造并缓存到模块级 `_default_instance`，后续所有调用**静默忽略参数**返回同一个实例
- `src/agentnodes/core/agent/event_store.py:680-682` `get_default_event_store()` 返回工厂
- `src/agentnodes/core/study/langgraph_engine.py:327-340` langgraph 路径用 `EventStoreFactory.create(db_path=get_study_session_db_path(workspace), ...)` + `attach_eventstore_to_sse`
- `src/agentnodes/core/study/engine_common.py:186-189` `get_study_session_db_path(workspace) = workspace / SESSION_DB_FILENAME`
- `src/agentnodes/core/study/langgraph_engine.py:342-346` + `engine_common.py:192-225` `ensure_study_session(...)`：往 **workspace 库**写 sessions 行
- `src/agentnodes/api/container.py:95` `EventStore(p, flush_to_messages=True)` —— 直接构造（**不走工厂**），`p` = `resolve_session_db_path()`
- `src/agentnodes/api/dependencies.py:75` 同上
- `src/agentnodes/core/agent/memory_manager.py:71-100` `resolve_session_db_path()`：优先级 `AN_SESSIONS_DB > AN_WORKSPACE_PATH > cwd > ~/.quantnodes`
- `src/agentnodes/api/routers/chat.py:755-771` chat 自动在 server DB 里补建 `study_*` session 行（兜底 404）

### 双库分叉逻辑

| 路径 | 写入哪 | 由谁驱动 |
|---|---|---|
| Server 容器 / chat | `AN_WORKSPACE_PATH/.quantnodes_agentnodes_session.db`（=`resolve_session_db_path()`） | container.py:95、dependencies.py:75 |
| langgraph engine | `study.workspace_path/.quantnodes_agentnodes_session.db`（=`get_study_session_db_path(workspace)`） | langgraph_engine.py:327-346 |
| bootstrap | 同 langgraph engine | bootstrap.py:295-302 |

**何时分叉**：`study.workspace_path != AN_WORKSPACE_PATH` 时。
- 同一个进程里 chat 与 study 写入两个不同的 session.db
- EventStoreFactory 粘路径：如果 Container 先建了一个绑到 server 库的实例（`AN_WORKSPACE_PATH/...`），langgraph_engine 后续的 `EventStoreFactory.create(db_path=workspace/...)` **会复用 Container 实例**，db_path 参数被丢弃
- 反过来：如果 langgraph_engine 先跑（生产路径上 container 先 init），它建出绑到 workspace 库的实例，Container 后建的直接 `EventStore(p)` 是另一个实例，**两个 EventStore 同时活**

**AN_WORKSPACE_PATH 控制服务器 cwd？**
否。AN_WORKSPACE_PATH 只决定 `resolve_session_db_path()` 的解析、`init_workspace_templates()` 的目标目录、DuckDB init 位置。**和进程 cwd 无关**。

### 影响
- **用户感知**：多 workspace 场景下，study round 的 `study:{sid}:round:{N}` 频道事件可能不会出现在 SSE buffer；前端可能看不到流式输出
- **数据正确性**：两个 DB 各有一行 sessions 行，跨 DB 查询拿不到正确用户归属
- **性能**：langgraph_engine.py:336-340 显式 try 桥接 SSE，桥不上的话回退到 projector-materialized messages（"best-effort"）

### 建议修法（最小可执行版）
1. **删 EventStoreFactory**（`event_store.py:636-672 + 680-682`）—— 工厂在生产中**只有 langgraph_engine.py:327 一处调用**，且 Container 用的是直接构造。删工厂后 langgraph_engine 改为 `EventStore(db_path=..., flush_to_messages=True)`，避免粘路径歧义
2. **统一 db_path 来源**：`langgraph_engine.py:328` 改为 `db_path=get_db_path_for_study_session()`，该函数委托给 `resolve_session_db_path()`（即 server 库），保证写入 server 库
3. **保留 `ensure_study_session` 双 DB 写兜底** 或者干脆改成只写 server 库（用 `ensure_study_session(resolve_session_db_path(), ...)`）
4. 给 `EventStoreFactory.reset()` 已存在的 test fixture 加 `DeprecationWarning`

### 工作量
**~35 行 + 2-3 个新测试**：
- 删工厂：~25 行（含注释、_default_instance、reset、get_default_event_store 的 `__all__` 条目）
- `langgraph_engine.py` 改直构造：~5 行
- `ensure_study_session` 改 server 库：~3 行
- 测试：单进程内只有一个 EventStore 实例 + workspace 不一致场景下事件仍走 server 库

---

## ③ legacy Temporal 风格五件套（1837 行死代码）

### 是什么
5 个源文件自述 "inspired by Temporal / CrewAI"：`streaming.py` / `checkpoint.py` / `signals.py` / `activity.py` / `integration.py`（integration 把前 4 个打包）。
合计 **1837 行代码 + 约 660 行测试**。

### 在哪里（精确调用方清单）

| 模块 | 全部调用点 | 生产路径？ |
|---|---|---|
| `core/study/streaming.py` (294 行) | `integration.py:108,169` + tests | ❌ 仅测试 |
| `core/study/checkpoint.py` (408 行) | `integration.py:107,277` + tests | ❌ 仅测试 |
| `core/study/signals.py` (430 行) | `integration.py:276,406-409`（默认 handler 注册）+ tests | ❌ 仅测试 |
| `core/study/activity.py` (365 行) | `integration.py:106` + tests | ❌ 仅测试 |
| `core/study/integration.py` (340 行) | `tests/test_study_integration.py:23-201` | ❌ 仅测试 |

**`integration.py` 的 monkey-patch 是真生产风险点**：
- `integration.py:155 _patch_runner_with_events` → 替换 `runner._run_loop` 和 `runner._run_one_round`（`runner.py:365, 607` 真实生产方法）
- 但 `_patch_runner_with_events` 只通过 `create_enhanced_runner()` 调用，**生产代码从不调** `create_enhanced_runner`
- 所以 production runner 仍是真实 `_run_loop`（已确认，runner.py:244 调 `await self._run_loop()`）

### 死代码自爆 bug 风险
1. `signals.py:404-409` import 副作用：模块加载时立即 `_default_signal_registry.register(SignalType.PAUSE, PauseHandler())` —— `PauseHandler.handle_signal` 调用 `self._scheduler.get_control_token(study_id)`，**scheduler=None 时** `if self._scheduler:` 短路（`signals.py:334`），无崩溃
2. `signals.py:411-412` `_default_timer_registry.register("monitor_check", MonitorCheckCallback())` —— 同上，`MonitorCheckCallback.on_timer_fired` 短路
3. `integration.py:172` `original_run_loop = runner._run_loop` —— 仅在 monkey-patch 函数内被捕获，不影响真实代码路径
4. `streaming.py:274-286` 模块全局 `_global_buffer/_global_emitter` —— 懒初始化，不 import 不创建
5. `activity.py:193` `AgentActivity()` 注册到 `_default_registry` —— 注册了但生产路径从不查询该 registry（实际产线走 `core/agent/registry.py`）

**结论**：**零自爆风险**，全部惰性构造。但所有 `_global_*` / `_default_*` 模式 + `signals.py:405-409` 模块级 register 是**隐性全局副作用**，未来重构集成时会咬人。

### 影响
- **用户感知**：无
- **数据正确性**：无
- **性能**：可忽略的 import 时间
- **可维护性**：1837 行 + 660 行测试 = **~2500 行噪音**。CI 跑这俩测试文件耗时（`_global_*` 初始化 + asyncio Lock 创建），但只验证永远不会触发的路径；ruff 类型检查（207 处 type:ignore 中部分源于此）；给新维护者制造幻觉

### 建议修法（最小可执行版）

**方案 A（推荐）：直接删**
- 删 5 个文件 = 1837 行
- 删 `tests/test_study_integration.py`、`tests/test_study_infrastructure.py` 共 ~660 行
- 同步检查并删 `core/study/dag.py`（也只被测试 import，但**production 走 `core/workflow/dag.py`** —— 那是不同的文件，`core/workflow/__init__.py:2` 是生产入口）
- **零生产代码变更**

**方案 B（保守）**：保留 `activity.py` 中 `WorkflowEngine` 等少量可能有用的 dataclass，把零调用的 `_global_*` + 注册副作用删干净

### 工作量
**方案 A**：`git rm` 7 个文件 + 在 `pyproject.toml` / `__init__.py` 检查 import 链（5 分钟）
- 验证：跑 `pytest tests/test_study_*.py` 看是否有任何 production 测试间接依赖这几个模块（grep 已确认**没有**）
- 新测试：**0**

---

## ④ summary ETag 下发缺失

### 是什么
`/api/study/{study_id}/summary` 实现 ETag 流程时，**只正确处理了 304 路径**，但**没把 ETag 写入 200 响应头**。
作者在 `study.py:645-655` 的注释中**自己承认**这是个未完工的 bug（FastAPI `response_model` 模式下不知道怎么挂 header）。

### 在哪里
- `src/agentnodes/api/routers/study.py:555-657`（endpoint + cache + 304 逻辑）
- `src/agentnodes/api/routers/study.py:67-92`（`_SUMMARY_CACHE` / `_compute_summary_etag` / `_set_cached_summary`）
- `src/agentnodes/api/routers/study.py:728,758,783,797,843,1423,1428,1471`（7 处 mutation handler 调用 `_invalidate_summary_cache`）
- `webui/frontend/src/api/client.ts:231-261` `summaryWithEtag`：发 `if-none-match`、读 `res.headers.get('etag')`
- `webui/frontend/src/components/study/StudyDetailPage.tsx:79` + `StudyTaskSummary.tsx:42` 消费者
- **测试盲点**：`tests/test_study_actions.py`、`test_study_api.py`、`test_study_e2e_api.py` 都**没有**任何 `ETag` / `304` / `if-none-match` 相关断言

### 实际 cache 与 ETag 流程
```python
# study.py:567-580
etag = _compute_summary_etag(study)                       # 算 etag
if_none_match = request.headers.get("if-none-match")
if if_none_match == etag:
    return Response(status_code=304)                      # 304 路径 OK
cached = _get_cached_summary(study_id)
if cached is not None:
    cached_data, cached_etag = cached
    if if_none_match == cached_etag:
        return Response(status_code=304)                  # TTL 内 304 OK
    return cached_data                                    # ← 这里也不带 ETag header！

# ... 后续 DB 查询 ...
return result_dict                                        # ← FastAPI response_model 序列化，header 也不带 ETag
```
```python
# study.py:645-655（作者自述已知 bug）
# NOTE: FastAPI returns a plain dict (serialized by response_model),
# so we cannot set response headers from the endpoint return value.
# ...
# Here we use the `Response` object returned earlier (304) for
# the ETag path, and for full responses we attach via a response
# header trick: FastAPI doesn't expose Response object in the
# endpoint when using response_model, so we use a workaround.
```

### 前端消费链
```typescript
// client.ts:240, 259-260
const res = await fetch(`${API_BASE}/study/${studyId}/summary`, { headers })
// ... 304 路径返回 { data: null, etag: etag ?? null }
const newEtag = res.headers.get('etag')   // ← 200 路径永远拿不到 etag
return { data, etag: newEtag }            // ← newEtag = null
```
**结果**：消费者在 200 路径拿到 `etag: null`，下一次 polling 不再发 `if-none-match`，ETag 链路就此**永远只在第一次 polling + 客户端发空 etag 的场景下 work 一次**，然后退化到全量下载。

### 影响
- **用户感知**：summary 页频繁 polling（前端 SSE 兜底 event 后还要 /summary 轮询）每次都全量下载，浪费带宽 + 解析
- **数据正确性**：无
- **性能**：summary endpoint 是研究页最热的接口，频繁全量返回拖累

### 建议修法（最小可执行版）
把 endpoint 改成接受 `Response` 参数显式设置 header：

```python
@router.get("/{study_id}/summary", response_model=StudySummaryResponse)
async def study_summary(
    request: Request, study_id: str, response: Response
) -> StudySummaryResponse:
    study = _owned_study(request, study_id)
    etag = _compute_summary_etag(study)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})  # 304 路径也补 header
    response.headers["ETag"] = etag                                # ← 修复点
    ... # 原有逻辑
    return result_dict
```

- 删 `_set_cached_summary` 写 dict 的部分（保留读逻辑即可），改在命中 cache 时也设 header
- 删 `study.py:647-655` 的 TODO 注释

### 工作量
**~15 行 + 3 个新测试**：
- 3 处加 `headers={"ETag": etag}`，删 TODO 注释块
- 测试：200 header 含 ETag、304 header 含 ETag、相同 etag 二次返回 304
- 现有 `test_study_actions.py` 的 8 个 summary 调用点中至少 1 个可以加 ETag 断言

---

## ⑤ plan-dag 同步 LLM 阻塞事件循环

### 是什么
`/api/study/plan-dag` 是 `async def` endpoint，但内部调用**完全同步**的 `DAGPlanner.plan()`，后者在 LLM 路径下会同步阻塞整个 asyncio 事件循环。

### 在哪里
- `src/agentnodes/api/routers/study.py:1674-1708` `study_plan_dag` endpoint（`async def`，未 `await` 任何东西）
- `src/agentnodes/core/study/dag_planner.py:77-156` `DAGPlanner.plan` → `_plan_via_llm` → `_call_llm`
- `src/agentnodes/core/study/dag_planner.py:165-176` `_call_llm` 直接调 `run_agent_via_llm(role="planner", ...)`
- `src/agentnodes/core/agent/role_factory.py:164-244` `run_agent_via_llm`：**纯同步 `def`**（不是 `async def`），内部 `result = loop.run(full_task)` 是同步阻塞调用
- `src/agentnodes/core/agent/loop.py` `AgentLoop.run`：包含 1 次到 8 次 LLM HTTP 调用（基于 `max_iterations=1`），单次 deepseek/openai chat completion 通常 3-30s
- 对照：grep `asyncio.to_thread` 在 `api/routers/` 下**零使用** —— 整个 API 层没有处理 sync 阻塞的标准范式

### 调用链精确计时
```
study_plan_dag  endpoint (async)
 └→ planner.plan                 (sync)
       └→ _plan_via_llm           (sync)
            └→ _call_llm          (sync)
                 └→ run_agent_via_llm (sync, def not async)
                       └→ loop.run(full_task)   (sync, blocking)
                              └→ 多次同步 httpx/requests LLM call
```

整个调用期间事件循环被**冻结 N 秒**（N = LLM 端到端时延），期间 FastAPI 任何其他 endpoint（chat、study 其他操作、admin、health）全部不可响应。

### 影响
- **用户感知**：调用 plan-dag 期间所有前端轮询/SSE 全冻；聊天面板响应消失
- **数据正确性**：无（LLM 调通就行）
- **性能**：plan-dag 的 LLM 调用时长（3-30s）= 全事件循环不可用时长

### 建议修法（最小可执行版）
```python
# study.py:1700-1701 改为
plan = await asyncio.to_thread(planner.plan, objective, constraints)
```

或更彻底：把 `DAGPlanner.plan` 改成 `async def`，`_call_llm` 改 `async def`，用 `httpx.AsyncClient` 替换同步 client。

**最小可执行版建议**：**只改 endpoint 一行 + 顶部 `import asyncio`**（如果还没 import）。

### 工作量
**~2 行代码 + 1 个新测试**：
- 1 行 import + 1 行 `await asyncio.to_thread(...)`
- 测试：现有 `test_dag_planner.py` 测的是 `planner.plan()` 直接调用，不覆盖 async 行为；加 1 个 `TestClient.post("/api/study/plan-dag", ...)` 测试：在 plan-dag 期间并发打 `/health` endpoint，验证 health 不被冻结 > 200ms

---

## ⑦ dag_engine + runner_context（2026-09 归档）

### 是什么
两个未被生产代码使用的模块，加上 `runner.py` 上的对应协调方法。

### 在哪里
- `src/agentnodes/core/study/dag_engine.py`（89 行，`run_round_dag` 单函数）
- `src/agentnodes/core/study/runner_context.py`（38 行，`RunnerContext` dataclass）
- `src/agentnodes/core/study/runner.py:801-820` `_run_round_via_dag` 方法
- `src/agentnodes/core/study/runner.py:201-220` `_to_context` 方法
- `src/agentnodes/core/study/runner.py:25` `from .runner_context import RunnerContext`
- 测试：`tests/test_study_dag_engine.py`（356 行，3 个 `TestRebuildPhaseOutputs` 测试断言已移除方法）

### 根因
1. **dag_engine**：`phase_engine.py:139-143` 有显式安全网——`engine='dag'` 被强制重映射到 `'langgraph'`（包括 API 接受的值、DB 恢复值、`AN_STUDY_DAG_ENGINE=1` env var）。注释明确："dag_engine has no production callers"。`_run_round_via_dag` 因此在生产中 0 调用方。
2. **runner_context**：`_to_context()` 在整个代码库（生产 + 测试）0 调用方。`docs/archive/undated/runner-context-and-utils-extraction.md` 描述的计划**从未实现**——`aegis.py` 直接接收 `goal_store`，`phase_engine.py` 接收 runner 实例。

### ⚠️ 不要混淆
**`LangGraphProfile.dag()`**（`langgraph_engine.py:58-60`）是**活跃的 LangGraph 配置预设**（StudyGraph + serial + no checkpoint + no HITL），通过 `get_profile()` 在 `runner.py:858,937` 查找。**DO NOT TOUCH**——它与本归档的 `dag_engine.py` 是完全不同的概念。

### 影响
- **用户感知**：无
- **数据正确性**：无（`engine='dag'` 在 phase_engine 已被安全重映射）
- **性能**：无
- **可维护性**：移除 41 行 runner.py 死代码 + 127 行 `core/study/` 死文件

### 已做归档（2026-09）
- `git mv` 到 `core/study/_archived/`
- `runner.py`：删除 `RunnerContext` import、`_to_context()`、`_run_round_via_dag()`（-41 行）
- `tests/test_study_dag_engine.py` → `tests/attic/attic_test_study_dag_engine.py`（pytest 自动忽略）
- 保留 3 个不依赖被移除方法的测试 → 新文件 `tests/test_study_engine_dispatch.py`（`TestEngineDispatch` × 2 + `TestPlanDagAsync` × 1）
- `_archived/README.md` 更新表项

### 未做（用户决定保留）
- `langgraph_engine.py:493` docstring 中的 `_run_round_via_dag` 引用
- `docs/archive/undated/langgraph-dual-engine-design.md:23` 过期架构图
- `docs/archive/undated/runner-refactoring-plan.md:30,36,65` 过期设计文档

### 工作量
**41 行生产代码净减少** + 356 行测试移到 attic + 127 行归档到 `_archived/`。

---

## 修复决策记录（待用户后续填写）

| 决策项 | 选项 | 选定 |
|--------|------|------|
| ① 是否清理 v2 互斥死代码 | 删除 / 保留 v1 兼容注释 | ✅ 已归档（2026-09，scheduler.py:_run_one_study_locked 内 ~16 行 + test_same_study_key_blocks_until_released skip，service.py:651-690 方法保留给 goal workflow） |
| ② 双库方向 | 全部统一到 server 库 / 保留双库双写 + 不一致保护 / 先调研哪边"错" | ✅ 已修（commit dee6d32，统一到直接 EventStore 构造） |
| ③ legacy 五件套 | 方案 A 直接删 / 方案 B 保守清理 / 保留 | ✅ 已归档（2026-09，git mv 到 _archived/，3246 行） |
| ④ summary ETag | 修（15 行）/ 保留 | ✅ 已修（commit d43169a） |
| ⑤ plan-dag 异步化 | 2 行 to_thread / 重写 async / 保留 | ✅ 已修（asyncio.to_thread） |
| ⑥ workflow 测试 flaky | 加 xdist_group 独占 worker / 调查根因 / 保留 | ✅ 已修（xdist_group('workflow') + dist=loadgroup，3 连测 883/883） |
| ⑦ dag_engine + runner_context | 协调删除 / 归档 / 保留 | ✅ 已归档（2026-09，git mv 2 文件 + runner.py -41 行 + 测试拆分到 attic） |

## 关联文档
- `docs/guides/legacy-sessiondb-tech-debt.md` — 同类技术债文档先例
- `docs/archive/undated/study-longhorizon-v2-design.md` — v2 身份设计权威来源（影响 ①）
- `docs/archive/undated/chat-agent-refactor-architecture.md` — AgentLoop / role_factory 调用链（影响 ⑤）
