# Goal Workflow Phase 5 — Real Execution + Web UI + API + Auth

> 版本：v0.6.0 – v0.6.6
> 周期：W1 – W14
> 状态：W0（设计稿）

## 1. 背景

Phase 4（v0.5.1-v0.5.5）完成了 Goal Workflow 子系统的**端到端功能**：

- ✅ CLI: `/goal start --workflow` + `/goal workflows` + `/goal checkpoint`
- ✅ TUI: WorkflowWorker + Ctrl+G + GoalPanel subscription
- ✅ P1 fixes: 7 项让自定义真正生效
- ✅ 4 个 preset + cookbook + demo
- ✅ ASCII DAG 可视化编辑器 + save_goal_workflow

但代码审计发现**从 mocked tests 到 real execution 还有差距**：

| 维度 | 现状 | 用户视角 |
|---|---|---|
| Prompt 模板 | ❌ workflow agents 引用的 `.md` 文件不存在 | real execution 会 FileNotFoundError |
| 集成测试 | ❌ 6,972 个测试全部 mock LLM | 不知道真实链路能不能跑 |
| Web UI workflow | ❌ 无 workflow 页面 | 只能 CLI/TUI 操作 |
| HTTP API | ❌ 无 agent chat / swarm run endpoint | HTTP 无法调用 agent |
| Dashboard | ❌ stats.goals=0 永远 | 数据不准 |
| Backtest 可视化 | ❌ 无图表 | 只有数字 |
| MCP transport | ❌ 仅 stdio | 无法远程调用 |
| Auth | ❌ 所有接口无认证 | 安全风险 |

## 2. 范围

```
P0  Prompt 模板 + 集成测试      v0.6.0 + v0.6.1    2 周    real execution 前置条件
P1  Web UI workflow + HTTP API   v0.6.2 + v0.6.3    4 周    浏览器操作 workflow
P2  Web UI 完善 + MCP HTTP       v0.6.4 + v0.6.5    4 周    完整体验
P3  Auth 认证                   v0.6.6              2 周    安全
```

## 3. 设计原则

1. **TDD**：先写测试，再写实现。每个 PR 必须新增 ≥5 测试。
2. **向后兼容**：所有新 API 是 additive，不破坏现有 CLI/TUI/MCP。
3. **可选 real LLM**：集成测试用 `OPENAI_API_KEY` gate，CI 环境可跳过。
4. **Prompt-first**：先有 prompt 模板，再验证 real execution。
5. **渐进增强**：Web UI 是 CLI/TUI 的补充，不是替代。

## 4. P0 — Prompt 模板 + 集成测试（v0.6.0 + v0.6.1）

### 4.1 v0.6.0 Prompt 模板

#### 4.1.1 需要的模板

5 个 preset 的 agents 引用的 prompt 文件：

| Preset | Agent | prompt_file | 状态 |
|---|---|---|---|
| `goal_factor_research` | researcher | `.prompts/researcher.md` | 需创建 |
| `goal_factor_research` | data_quality | `.prompts/data_quality.md` | 需创建 |
| `goal_factor_research` | factor_analyst | `.prompts/factor_analyst.md` | 已有 |
| `goal_factor_research` | risk_controller | `.prompts/risk_controller.md` | 已有 |
| `goal_market_analysis` | market_scanner | `.prompts/market_scanner.md` | 需创建 |
| `goal_market_analysis` | regime_classifier | `.prompts/regime_classifier.md` | 需创建 |
| `goal_market_analysis` | report_writer | `.prompts/report_writer.md` | 需创建 |
| `goal_risk_assessment` | position_auditor | `.prompts/position_auditor.md` | 需创建 |
| `goal_risk_assessment` | risk_controller | `.prompts/risk_controller.md` | 已有 |
| `goal_risk_assessment` | stress_tester | `.prompts/stress_tester.md` | 需创建 |
| `goal_risk_assessment` | report_writer | `.prompts/report_writer.md` | 需创建 |
| `goal_strategy_review` | pnl_attribution | `.prompts/attribution_analyst.md` | 已有 |
| `goal_strategy_review` | icarus_review | `.prompts/icarus_review.md` | 需创建 |
| `goal_strategy_review` | factor_analyst | `.prompts/factor_analyst.md` | 已有 |
| `goal_strategy_review` | benchmark_compare | `.prompts/benchmark_compare.md` | 需创建 |
| `goal_strategy_review` | report_writer | `.prompts/report_writer.md` | 需创建 |
| `goal_portfolio_review` | portfolio_construction | `.prompts/portfolio_construction.md` | 需创建 |
| `goal_portfolio_review` | concentration_check | `.prompts/concentration_check.md` | 需创建 |
| `goal_portfolio_review` | risk_controller | `.prompts/risk_controller.md` | 已有 |
| `goal_portfolio_review` | report_writer | `.prompts/report_writer.md` | 需创建 |

**需创建**：12 个 prompt 模板
**已有**：8 个（从 autoresearch 复用）

#### 4.1.2 Prompt 模板格式

每个 `.md` 文件遵循统一结构：

```markdown
# {Agent Name}

## 角色
你是一个{角色描述}。

## 任务
{具体任务描述}

## 输入
{期望的输入格式}

## 输出格式
{期望的输出结构}

## 约束
{限制条件}
```

#### 4.1.3 Prompt 查找路径

`WorkflowController._resolve_prompt()` 搜索顺序（`controller.py:255`）：
1. `workspace / prompt_field`
2. `_TEMPLATES_DIR / prompt_field`（`templates/.prompts/`）

**决策**：将所有 prompt 模板放在 `templates/.prompts/` 下。

#### 4.1.4 测试

| 文件 | case |
|---|---|
| `tests/test_goal_workflow_prompts.py` | 每个 preset 的每个 agent prompt 可加载 / 格式正确 / 非空 / 包含角色+任务+输出 |

### 4.2 v0.6.1 集成测试

#### 4.2.1 架构

```
tests/test_integration_live.py
  ├── @pytest.mark.skipif(not OPENAI_API_KEY) 守卫
  ├── TestGoalWorkflowE2E — 完整 workflow 执行
  ├── TestAgentLoopE2E — 单 agent 执行
  ├── TestSwarmWorkerE2E — swarm worker 执行
  └── TestSwarmRuntimeE2E — DAG 执行
```

#### 4.2.2 测试矩阵

| 测试 | 预计耗时 | 验证点 |
|---|---|---|
| `test_agent_loop_e2e` | ~30s | agent 返回非空结果 |
| `test_swarm_worker_e2e` | ~60s | worker 完成 + 工具调用 |
| `test_swarm_runtime_e2e` | ~120s | DAG 逐层执行 |
| `test_goal_workflow_e2e` | ~300s | workflow 完成 + evidence 收集 |
| `test_goal_workflow_checkpoint_resume` | ~180s | checkpoint 保存 + resume 续跑 |

## 5. P1 — Web UI workflow + HTTP API（v0.6.2 + v0.6.3）

### 5.1 v0.6.2 Web UI workflow 页面

#### 5.1.1 新增路由

```
GET  /webui/workflows              — workflow 列表页
GET  /webui/workflows/<name>       — workflow 详情 + DAG 可视化
POST /webui/workflows/<name>/run   — 启动 workflow (HTMX)
GET  /webui/workflows/<name>/events — SSE 推送进度 (EventSource)
```

#### 5.1.2 新增模板

```
webui/templates/workflows/
  list.html      — 5 个 preset 卡片 + 用户自定义列表
  detail.html    — DAG ASCII 渲染 + agent 列表 + criteria + 运行按钮
  progress.html  — HTMX partial: 进度条 + agent 状态 + evidence 计数
```

#### 5.1.3 SSE 进度推送

```python
@router.get("/workflows/{name}/events")
async def workflow_events(name: str):
    """SSE endpoint for workflow progress."""
    async def event_generator():
        config = load_goal_workflow(name)
        runner = GoalWorkflowRunner(config, session_id="webui", store=GoalStore())
        queue = asyncio.Queue()
        class WebUIObserver:
            def on_event(self, event, data):
                queue.put_nowait({"event": event, **data})
        runner.subscribe(WebUIObserver())
        task = asyncio.create_task(runner.start(f"WebUI: {name}"))
        while not task.done():
            try:
                data = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield f"data: {json.dumps(data)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
        yield f"data: {json.dumps({'event': 'done'})}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### 5.2 v0.6.3 HTTP API 扩展

#### 5.2.1 新增 endpoints

```
POST /api/agent/chat          — 调用 agent loop (可选 streaming)
POST /api/agent/chat/stream   — SSE streaming chat
POST /api/swarm/run           — 执行 swarm preset
GET  /api/swarm/runs          — 列出所有 swarm runs
GET  /api/swarm/runs/{id}     — 查询 run 状态
POST /api/swarm/runs/{id}/cancel — 取消 run
POST /api/workflow/run        — 执行 goal workflow
GET  /api/workflow/runs       — 列出 workflow runs
GET  /api/workflow/runs/{id}  — 查询 workflow 状态
```

## 6. P2 — Web UI 完善 + MCP HTTP（v0.6.4 + v0.6.5）

### 6.1 v0.6.4 Web UI 完善

- Dashboard stats 修复（goals/hypotheses 真实计数）
- Backtest 可视化（NAV 曲线 + 回撤 + 指标卡片，Chart.js CDN）
- Dark mode（CSS media query）

### 6.2 v0.6.5 MCP HTTP transport

```
POST /mcp/call          — 调用 MCP tool
GET  /mcp/tools         — 列出所有 tools
SSE  /mcp/events        — 事件流
```

## 7. P3 — Auth 认证（v0.6.6）

- HTTP API: Bearer token（`STRATEGY_RESEARCH_API_KEYS` env）
- WebUI: Session cookie + login page
- CLI/TUI 不受影响

## 8. 发布日历

| 版本 | 周次 | 内容 | 新增测试 |
|---|---|---|---|
| **v0.6.0** | W1 | P0 Prompt 模板 | +15 |
| **v0.6.1** | W2 | P0 集成测试 | +10 |
| **v0.6.2** | W3-W5 | P1 Web UI workflow | +20 |
| **v0.6.3** | W6-W7 | P1 HTTP API 扩展 | +15 |
| **v0.6.4** | W8-W10 | P2 Web UI 完善 | +15 |
| **v0.6.5** | W11-W12 | P2 MCP HTTP transport | +10 |
| **v0.6.6** | W13-W14 | P3 Auth | +10 |
| **合计** | 14 周 | — | **+95 测试** |

## 9. 参考

- `docs/archive/undated/goal-workflow-design.md`：Phase 3 完整设计（735 行）
- `docs/archive/undated/phase-4-plan.md`：Phase 4 完整设计（709 行）
- `docs/archive/undated/enhancement.md`：P0-P2 执行计划（1081 行）
- `docs/archive/undated/PLAN-phase1-4.md`：MCP 工具对比（320 行）