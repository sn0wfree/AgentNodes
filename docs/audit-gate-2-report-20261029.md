# 审计门② 报告 — 2026-10-29

> 依据：`docs/stage-4-cleanup-checklist-20261027.md` §6 的五项检查。
> 执行时机：C1 主体（阶段 0 / 选项 B / B3 / B4 止血 / B5）完成之后。

---

## 结论

| # | 检查项 | 结果 |
|---|---|---|
| 1 | 删除兑现 | ✅ 3/3 已删（第 4 项按决议挂起） |
| 2 | 第一次真实全量绿（分道后） | ⚠️ **见 §2：不是「全绿」，是「零回归」** |
| 3 | 原始 9/5 发现销账 | ✅ 安全 ×4 全兑现；死代码簇已消失 |
| 4 | 性能基线 | ✅ 已测并记录（§4） |
| 5 | 文档对齐 | ✅ 架构图/路线图/契约与代码一致 |

**第 2 项必须如实说明**：冻结基线是 104 条预存失败，项目从未有过
「全量绿」这个状态。分道后的可复现状态是「失败集合 ⊆ 冻结基线」。
把这一项写成「绿」是不诚实的——见 §2。

---

## 1. 删除兑现

| 项 | 验证方式 | 结果 |
|---|---|---|
| 旧 asyncio.Queue 路径（scheduler） | `_session_loop` / `_ensure_consumer` 在 `core/study/scheduler.py` 零命中 | ✅ GONE |
| `research_graph_enabled()` 门 | `entry_adapters.py:38`，函数体 `return True` | ✅ 已退役 |
| `study_entry_enabled()` 门 | 生产代码零命中（仅 2 处测试引用历史名称） | ✅ GONE |
| `new_entry_enabled()` 门 | 仍在（`entry_adapters.py:22`） | ⏸ P5-1c 挂起，符合决议 |

**一处需要澄清的假阳性**：`api/session/service.py` 仍有 10 处
`_session_queues`。那是 **`SessionService` 自己的队列**（旧 chat 路径的
在途队列），不是被删的 scheduler 队列——两者同名不同物。清单第 1 节
删的是 scheduler 侧，已兑现。

---

## 2. 「全量绿」这一项的实际情况

| 指标 | 值 |
|---|---|
| PR 道收集 | 9911 条 |
| 通过 | ~9741 |
| 失败 | 93 failed + 7 error = 100 |
| 冻结基线 | 104 条 node id |
| 判定 | 失败集合 ⊆ 基线（零回归） |

**为什么基线有 104 条而本次失败 100 条**：4 条是已知的顺序依赖/环境
脆弱项（`test_auth_tokens_hardening` ×2、`test_bg_tools` ×2、
`test_data_clean_tool` ×2 —— 具体哪些失败按运行顺序浮动）。

**基线的来源与性质**（逐条可查 `tests/baselines/pr-lane-failures.json`
的 `post_freeze_additions`）：

| 族 | 数量 | 性质 |
|---|---|---|
| `test_shell_tool` 等 POSIX 依赖 | ~6 | Windows 环境性 |
| `test_route_agent_event_*` / `test_compact_*` / runner | ~40 | 旧执行体生态 |
| `test_cli_server_cmd` / `test_bg_*` | ~15 | 环境/时序 |
| 顺序依赖脆弱项 | 4 | 共享 fixture 状态 |
| 分散其余 | ~39 | 待 B4/C2 处置 |

**诚实结论**：审计门② 的「第一次真实全量绿」在项目当前的兑现形式是
「第一次可复现的零回归基线」。真正的「全绿」需要清掉这 104 条，
而那属于 C1 之后的独立工作（约 44 条会随旧执行体退役自然消失）。

---

## 3. 原始 9/5 发现销账

### 3.1 安全 ×4（全部兑现，代码级验证）

| # | 项 | 验证锚点 | 结果 |
|---|---|---|---|
| ① | `/api/chat/` 移出公开前缀 | `middleware.py:20-42` 列表内无该前缀 | ✅ |
| ② | trace 端点属主校验 | `chat.py` 含 `_fetch_session_owned` | ✅ |
| ③ | 工作流对象级鉴权 | `workflow.py` 含 `_require_run_access` | ✅ |
| ④ | admin 种子随机化 | `user_db.py` 含 `SR_ADMIN_PASSWORD` | ✅ |

### 3.2 死代码簇

| 项 | 结果 |
|---|---|
| `api/sse_buffer.py` | ✅ GONE（无文件、无 import） |
| `api/session/bridge_v2.py` | ✅ GONE（同上） |

（SSE 单轨在 A2 已完成，`EventStore.subscribe` 是唯一通路。）

### 3.3 本轮新增的死代码清理（副产物）

| 项 | 提交 |
|---|---|
| `workflow/decorators.py`（163 行，零 import） | `755d9051` |
| `NodeContext._failures`（只读不写的幻影字段） | `755d9051` |
| `agent_node` + 重复的 `first_two_sentences`（零调用方） | `0218897d` |
| `role_factory` 的 `client` 死参数 | `755d9051` |

---

## 4. 性能基线（2026-10-29 实测）

复现：`D:\tools\python312\python.exe scripts\audit_gate2_perf.py`

### 4.1 GraphEngine 单次 run 延迟（200 次）

| 节点数 | median | p95 | max |
|---|---|---|---|
| 1 | 2.233 ms | 2.840 ms | 4.842 ms |
| 5 | 8.710 ms | 14.991 ms | 20.484 ms |
| 10 | 30.512 ms | 35.283 ms | 78.744 ms |

**读数**：单节点 2.2ms，5 节点 8.7ms，10 节点 30.5ms。增长超线性
（5→10 节点延迟 ×3.5），与 langgraph 每次 run 重建 StateGraph + compile
的已知开销一致。若 chat 成为延迟敏感路径，这是一个明确的优化点
（引擎实例复用 / 图缓存），**本轮不优化**（无实测痛点）。

### 4.2 mailbox 吞吐

| 指标 | 值 |
|---|---|
| 提交 2000 条耗时 | 提交速率 37,439 条/秒 |
| 排空 1000 条耗时 | 排空 2.74 ms |
| 排空速率 | 365,617 条/秒 |

**注**：2000 条提交只排出 1000 条，因为 mailbox 深度 cap = 20（50 个
会话 × 20 = 1000），溢出按设计丢弃（cap 是「唯一拒绝处」，契约 §7）。
这不是 bug，是容量契约的体现。

---

## 5. 文档对齐

| 文档 | 声明 | 代码实际 | 一致 |
|---|---|---|---|
| `architecture-diagram.html:154` | 「唯一**编排** runtime」 | 只有 `graph_engine.py` import StateGraph | ✅ |
| `architecture-diagram.html:1002` | 「AgentLoop 执行原语保留」 | 3 个合法构造点 | ✅ |
| `project-status-and-roadmap` §6 | B2「已符合选项 B」 | study 编排在 `graph_preset.py` | ✅ |
| `project-status-and-roadmap` §6 | B3「已完成（结论：不迁）」 | 提交 `755d9051` | ✅ |
| 契约 §1 能力矩阵 | ContextHub collect+arbitrate | `hub.py` 含 `_arbitrate` | ✅ |
| 契约 §1 | todo provider | `context_providers.TodoContextProvider` | ✅ |

### 5.1 AgentLoop 构造点核对（契约 §6 判据）

| 站点 | 性质 |
|---|---|
| `core/agent/role_factory.py:157` | ✅ 唯一工厂（study/workflow 经此） |
| `core/agent/executor.py:413` | ✅ swarm/subagent |
| `core/agent/chat_loop.py:129` | ⏳ P5-1c 待删（已挂起） |

（`chat_loop.py:3`、`loop.py:304/315` 是注释与 docstring 示例，非构造点。）

**与契约一致**：`test_domain_layers_use_the_agent_factory_not_raw_agentloop`
与 `test_graph_engine_is_the_only_orchestration_runtime` 两条门禁均绿（16 条全绿）。

---

## 6. 遗留项（明确移交）

| 项 | 归属 | 前置 |
|---|---|---|
| 104 条预存失败清理 | C2 / B4 | B4 的 7 个语义决策 |
| B4 完整事件化 | B4 | 同上 |
| P5-1c 删旧 chat 分支 | A3 | 业务稳定 |
| GraphEngine 图缓存优化 | 无 | 出现实测延迟痛点 |
| `message_count` 恒 0（D8） | B4 | 归属决策 |

---

## 7. 复验命令

```bat
:: item 1 -- 删除兑现
findstr /n "_ensure_consumer" src\strategy_research\core\study\scheduler.py
findstr /n "def research_graph_enabled" src\strategy_research\core\gateway\entry_adapters.py

:: item 3 -- 安全四锚点
findstr /n "_fetch_session_owned" src\strategy_research\api\routers\chat.py
findstr /n "SR_ADMIN_PASSWORD" src\strategy_research\api\user_db.py
findstr /n "_require_run_access" src\strategy_research\api\routers\workflow.py

:: item 4 -- 性能基线
D:\tools\python312\python.exe scripts\audit_gate2_perf.py

:: 门禁
D:\tools\python312\python.exe -m pytest tests/test_architecture_gates.py -q
```
