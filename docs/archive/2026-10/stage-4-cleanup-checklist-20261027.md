# 阶段 4 清理清单 — 2026-10-27

> 原规划：ContextHub 4 builder→1 · 调度归一 · 反向依赖 · 测试隔离修复 ·
> CI 门禁 · 删旧路径。工量 7d。**本文件先划边界：哪些删、哪些留、哪些降级。**
> 原则：减法有据（谁在用、删了谁疼），不为了「看起来干净」而动在跑的东西。

---

## 1. 已确认可删（本轮执行）

| 项 | 依据 | 状态 |
|---|---|---|
| **旧 asyncio.Queue 路径（scheduler）** | 双门已翻默认开 + 3c 执行等价测试通过 | ✅ 已删（`_session_loop` / `_session_queues` / `_ensure_consumer`） |
| **`research_graph_enabled()` 门** | B1-b 等价性测试（RoundOutcome 逐字段相等）已钉死 | ✅ 已退役（恒 True，环境变量无效） |
| **`study_entry_enabled()` 门** | 3c 执行等价已验；旧通道已删 | ✅ 已删 |
| **`new_entry_enabled()` 门（chat/CLI 分支）** | ⚠ **暂缓**——见 §1.1 | ⏸ |

### 1.1 为什么 chat/CLI 旧路径分支暂缓删除

清理中发现的**功能缺口**：`_send_via_gateway` 目前不写对话历史、不落事件库
（`message_id: None`），而旧 `SessionService.send_message` 两条都做。直接删旧分支
= 让 WebUI 的 `/send` 失去历史持久化与事件流——属于功能退化，不是清理。

删除前置（阶段 5 第一项）：Gateway 的 chat 路径接入
1. 会话历史读写（MemoryManager / SessionService 的历史注入）；
2. `PlatformStore.emit` 事件持久化（Gateway 已有 store 参数，但 chat 路径未接）；
3. `message_id` 签发（P-4 身份）。

补齐后按 replan v2 §5 方法论做**执行等价**验证（历史条数、事件条数、返回结构），
再删旧分支。

## 2. 明确不删（保留，附理由）

| 项 | 为什么留 |
|---|---|
| **SSE 双轨（`api/sse_buffer.py` + `api/session/bridge_v2.py`）** | 3b-2 延后至今；影响面 8 个生产文件 + 7 个测试文件，且是 WebUI 实时链路的现存实现。删除属独立工程，不是「清理」 |
| **`ContextBuilder`（`core/agent/context.py`）** | 服务旧 `AgentLoop`（study/runtime 仍在用），GraphEngine 的 llm/react 节点**不用**它。合并不是删文件，是迁移调用方——属阶段 5 |
| **`core/study/langgraph_engine.py`** | B1-b 的 research preset 与它**无关**（那是独立实现）；删它必须先确认 study 的 `engine=langgraph` 无人使用。需先做使用面调查 |
| **`core/study/_archived/`** | 历史归档，零运行时成本，保留可追溯 |
| **`tests/attic/`** | 同上 |

## 3. 测试隔离修复（本轮核心，优先做）

**问题**：全量套件 order-dependent（9/5 记录在案）——合跑失败、单跑通过。这是让
「全量绿」失去警报效力的根因，也是阶段 4 最有价值的产出。

**已知三类污染源**：

| 源 | 机制 | 修法 |
|---|---|---|
| 模块级单例 | `get_gateway()` 缓存 mailbox/session 表 | 已在部分文件加 `reset_gateway()`；推广到 conftest 全局 autouse |
| 环境变量泄漏 | `QNODES_*` / `SR_*` 由测试设置未清 | conftest 全局快照/还原 `os.environ` |
| 全局注册表/缓存 | `ToolRegistry`、LLMConfig、model_catalog 等模块级 dict | 逐个加 reset fixture |

**验收**：`pytest -p no:randomly`（固定序）与 `pytest --randomly-seed=N`（随机序）
各跑 3 次，失败集合为空或稳定为「已知预存」集合。

## 4. CI 门禁

| 门 | 内容 |
|---|---|
| 分道 marker | `llm` / `e2e` / `slow` 已有，补 `pytest -m "not llm and not e2e and not slow"` 的 PR 门 |
| 依赖方向 | 一条 AST 测试：`core/{gateway,orchestration,runtimes,storage}` 不得 import `core/study`（域→平台单向） |
| 不变量 | `verify_all()` 在 CI 跑（INV-1/3/5/8/9） |
| 新门 | 无裸 `except:`、无 `print(`（`src/` 内，`noqa: T201` 白名单） |

## 5. 本轮执行顺序（保守版）

```
S1  测试隔离（conftest 全局 env 快照 + gateway 单例重置 + 注册表重置）   ✅ 完成
S2  删旧 asyncio.Queue 路径 + study 门                                  ✅ 完成
S3  删 research_graph 门（保留图路径为唯一实现）                        ✅ 完成（函数保留恒 True）
S4  依赖方向 AST 测试 + 不变量 CI + 分道门                              ✅ 完成
S5  全量分道跑 + 审计门②                                                ⏳ 待做
```

**已删除**（本轮实测）：
- `StudyScheduler._session_queues` / `_session_consumers` / `_ensure_consumer` /
  `_session_loop`（旧 asyncio.Queue 通道，约 40 行）
- `study_entry_enabled()` 门（含 3 个测试的 off 分支）
- `research_graph_enabled()` 的门接线（`_resolve_branch_fn` 恒走图；函数保留恒 True）

**新增门禁**（`tests/test_architecture_gates.py`，10 条）：
- 平台层（gateway/orchestration/runtimes/storage）不得 import `core.study`；
- `core/gateway` 零 FastAPI import；
- `verify_all()` 全绿 + INV 登记表覆盖 INV-1/3/5/8/9；
- 门退役契约（research_graph 恒 True、环境变量无效、phase_engine 无门调用）；
- kind 过滤双向不丢单。

**langgraph 分支退役（原 S?）单独列为阶段 5**，理由：它是 1000+ 行的独立执行体，
需要先调查 `study.engine='langgraph'` 的真实使用面，且删除后的回归面远超本轮。
本轮只做「门翻默认 + 旧队列/旧门删除」，不动它。

## 6. 审计门② 检查项

1. 删除兑现：清单第 1 节每项确认已删（`git grep` 零命中）；
2. 第一次真实全量绿（分道后）；
3. 原始 9/5 发现销账（安全 ×4 / plan-dag 阻塞 / 死代码簇 / 小 bug ×2）；
4. 性能基线（GraphEngine 单 turn 延迟、mailbox 吞吐）；
5. 文档对齐（架构图/目录结构/契约文档与实际代码一致）。

## 7. 阶段 5 待办（本清单明确移交）

| # | 事项 | 前置 | 理由 |
|---|---|---|---|
| P5-1 | ✅ **已完成**（`af4cb235`）：Gateway chat 补历史注入 + 事件持久化 + message_id | — | 已交付 |
| P5-1b | ✅ **已完成**（`0d25b702` + `3d03ed65`）：流式 + 工具链 + MiniMax `<think>` 跨 chunk 修复 | — | 已交付 |
| **P5-1c** | ⏸ **挂起（2026-10-28 用户拍板）**：删除旧 chat 分支 + `new_entry_enabled` 门——**业务稳定后再执行**（清单见 `todo-remove-legacy-chat-branch-20261027.md`，前置已就位） | 业务稳定 | 入口真正只剩一条路 |
| **P5-2** | ✅ **已完成**（`5c4c544`，2026-10-27）：langgraph 分支退役——净删 2782 行（langgraph_engine 679 + runner_langgraph 155 + `_run_langgraph_branch` + checkpoint DDL + `_rebuild_phase_outputs` + 6 个纯分支测试文件）；3 个引擎无关方法（`_load_graph`/`_wait_for_approval`/`_expire_interrupt`）从 mixin 恢复到 runner 本体；`engine='langgraph'` 存量值 warning 后回落图路径（真实 DB 零行，实测无影响）。设计：`stage-p52-langgraph-retirement-design-20261027.md` | 无（前置已就位） | 单引擎达成：研究轮唯一执行体 = GraphEngine 图路径 |
| P5-3 | SSE 单轨（删 `api/sse_buffer.py` + `api/session/bridge_v2.py`） | 3b-2 一直延后 | 8 个生产文件 + 7 个测试文件，属独立工程 |
| **P5-4** | ✅ **已完成**（`7e6e183d`，2026-10-27）：ContextHub W0 骨架落地 + pre-run 上下文（goal/memory）迁入。**清单前提修正**：ContextHub 原本不存在于代码（差距图 §2.5）；「4 builder」= injector 家族——goal+memory 转 provider 整链桥接（检索 query 链式语义保持，逐字节等价测试钉死），todos 留 W1、continuation 不转（决策类）。设计：`stage-p54-context-hub-w0-design-20261027.md`。W1/W2 尾项：todos provider 化、记忆注入三改、ContextPlan 并入仲裁面 | 无 | 「目录供件 → 事件记录 → 上下文组装 → LLM」三中枢之二落地（hub + EventStore） |
| **P5-5** | ✅ **已完成**（2026-10-28，四项独立提交）：安全债 ×4 清零——② trace 端点属主校验（`174ecea1`，任意调用方可读任意会话完整 LLM 轨迹→404/403）；④ admin 种子随机化（`4d6696da`，admin/admin 常量凭据→SR_ADMIN_PASSWORD env 或随机密码落盘 0600）；③ workflow 对象级鉴权（`0c02c346`，双 runner 族 + drafts owner 列 + definitions delete 收紧 admin）；① `/api/chat/` 移出公开前缀 + permission 应答属主化（`87802d5e`，匿名共享身份消除，前端零改动 401→/login） | 无 | 与架构重构正交的 9/5 审计项全部销账；测试适配 6 文件 + 前端零改动（已验证） |

## 8. 本轮实测数据

- 删除代码：约 40 行（旧队列通道）+ 门接线若干
- 新增门禁：10 条架构测试
- 回归：受影响 148 + 更宽 232 全绿；lint 全绿

## 9. 全量分道跑发现的预存问题（非本次改动引入）

### 9.1 用户主目录沙箱（阶段 4 追加，系统性解决「测试写真实配置」）

**问题定性**：不是单个测试的 bug，是结构性缺陷——28+ 个模块解析 `Path.home()`，
其中 13 个是 import 时求值的模块级常量；只重定向 `HOME` 的 fixture 在 Windows
上拦不住 `Path.home()`（读 `USERPROFILE`）。此前至少两类测试在写/读真实配置：
auth_tokens 覆写 jwt_secret；init wizard 依赖真实 llm.json 才能通过。

**三层防线**（`tests/_home_sandbox.py` + conftest 顶部）：

| 层 | 机制 | 防什么 |
|---|---|---|
| 进程级重定向 | conftest **顶部**（任何 strategy_research import 前）把 `USERPROFILE`/`HOME` 指向 `tests/_user_home_sandbox` | import 时常量、运行时解析、env 直读三条路全部落沙箱 |
| 金丝雀对账 | session 级 `_guard_real_config`：会话前后对真实 `~/.quantnodes`、`~/.quantnodes-research` 做全量文件指纹比对，有变化即报错并列出文件 | 兜底抓绕过 home 解析的写入（win32 直读、绝对路径拼接） |
| 显式豁免 | live 套件夹具显式把 home 指回 `_home_sandbox.REAL_HOME` | 豁免是显式动作而非默认，宿主配置只在 live 测试可触碰 |

**元测试**（`test_architecture_gates.py`）：home 落沙箱、`expanduser("~")` 落沙箱、
模块级常量（quantnodes_bridge.CONFIG_PATH）落沙箱、沙箱不是真实主目录子目录。

**沙箱暴露并修复的宿主依赖**（都是测试一直在依赖真实配置才能通过的实证）：

| 项 | 修法 |
|---|---|
| `cli/onboard.py` 四个路径常量 import 时固化 | 改 PEP 562 `__getattr__` + 访问函数（内部调用动态解析；`monkeypatch.setattr` 的补丁值经 `_patched()` 优先——两头兼容） |
| `test_init_wizard`/`test_init_e2e` 两个用例依赖真实 llm.json 才走 Overwrite 分支 | 补显式路径 patch（与其余用例一致） |
| `test_model_catalog` 断言 models.dev 外部数据字段（supports_vision 曾漂移） | 放宽为只断言 override/source，不断言外部数据内容 |
| `test_llm_profiles` chmod 0o600 ×2（Windows 无 POSIX 位） | `_posix_only` skip |

**验收**：home 常量相关 23 个套件 **496 通过 / 9 跳过**；live（真 LLM）5/5 通过证明豁免可用。

### 9.2 其余预存问题（待阶段 5）

| 问题 | 证据 | 性质 |
|---|---|---|
| `test_alpha_zoo_adapter` ×2 失败 | 单独跑也失败（9/21 已记录） | `compute_as_series` 未按约定 dropna |
| `test_e2e_full_flow no_progress` 挂起 | 单跑也挂；`_approval_event.wait(timeout=1800)` | 测试缺 `approval_timeout` 参数 |
| PR 道 ~137 个失败 | 合跑挂单跑过（order-dependence 存量） | 逐个隔离是多日任务，阶段 5 主项 |
| `test_disk_cache_written_after_refresh` | 单跑绿合跑挂 | 孤立顺序依赖，随全量再核 |
