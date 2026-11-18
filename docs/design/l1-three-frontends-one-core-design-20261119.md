# 三前端一核心 —— interfaces 层用例下沉与复用收敛设计

> **状态**：方案先行（R-014）· 待裁决
> **日期**：2026-11-19 · **分支**：refactor/session-lifecycle-kernel
> **依据**：2026-11-18/19 interfaces 层代码级深读审查（4 簇全读 ~19,000 行 + AST 横切扫描 + 高严重度主张人工复核）
> **前序**：结构级梳理结论（层方向 0 违规、四子包正交）仍成立——本档不动骨架，只下沉用例、清除死代码、收敛重复。

---

## §0 结论摘要

**提案**：api / cli / tui / mcp 四个表现面收敛到「一个用例核心」（L2 apps）+ 各自薄适配。统一的是**用例层**，不是表现层。

**实测依据**（§2）：7 项 P0 缺陷（其中 3 项已人工复核：schedule IDOR、goal resume 绕租约、mcp timeout 契约欺骗）、约 **2,100 行**被单测"保鲜"的死代码、9 类同义多实现的漂移实证。

**批次**（§5）：批 1 P0 缺陷修复 → 批 2 死代码大扫除 → 批 3 用例下沉（三前端一核心主体）→ 批 4 一致性收尾。每批开工前按 R-014/R-015 出实施清单，本档只批方案框架。

**预期效果**：L1 从「22.4k 行，其中 ~2.1k 死代码 + ~1.4k 用例逻辑」回到真正的薄适配层；同一用例在 api/cli/mcp 间不再漂移。

---

## §1 背景与动机

维护者直觉：**「api tui cli 理论上干的是类似的事情，应统一成一套代码复用。」**

代码级审查证实了这一直觉，且给出了精确化：三前端"类似的事情"是**用例**（发消息、列会话、看 goal、流式事件、取消 attempt），而它们各自不同的部分是**协议形状**（HTTP/SSE、Rich/TUI、argparse、JSON-RPC）。四层架构的设计本意即：用例只有一份（L2 apps），前端各自薄适配（L1）。当前状态是这个设计未完成——大量用例逻辑仍住在 L1，且多实现之间已经漂移。

---

## §2 实测证据（2026-11-18/19 快照）

> 行号来自审查时刻快照；树上有并行批次在动 loop 簇与 gateway 设计文档，实施前须按 R-015 逐项重核。

### 2.1 P0 缺陷（7 项）

| # | 缺陷 | 位置 | 复核状态 |
|---|---|---|---|
| 1 | **IDOR**：schedule 四端点（show/cancel/delete/run）缺归属校验——`_load_job_owned` 只比 `job.owner_session_id != session_id`，`session_id` 来自请求参数、从未与认证用户核对；`_require_owner` 仅 create/list 调用 | `api/routers/schedule.py:51-62,159,166,179,190` | ✅ 人工复核 |
| 2 | **resume 绕租约**：`resume_from_checkpoint` 调用与工厂签名均无 `execution_lease`——恢复的 DAG 可与 Gateway chat 并发写同一 session，违反裁决 2（B3）互斥迁移；同文件 `_start_workflow` 有传 | `cli/commands/slash_goal.py:772-777`；`apps/goal/workflow.py:933-940` | ✅ 人工复核 |
| 3 | TUI 硬取消状态记 PAUSED 可被 resume 复活（Ctrl+G 走 immediate 路径，与文件头状态机图矛盾） | `cli/tui/workers/workflow_worker.py:153-166` | 代理深读 |
| 4 | resume 弹窗「首条消息」被静默丢弃（`_pending_input` 全仓只写不读） | `cli/tui/session.py:99,108` + `app.py:278` | 代理深读 |
| 5 | **MCP `run_swarm` 的 `timeout` 参数宣告给客户端但从未生效**（schema 承诺默认 300s，handler 弃用） | `mcp/server.py:412,445-446,506` | ✅ 人工复核 |
| 6 | async 路由内同步阻塞 HTTP（`minimax_search` 为 sync def + `urllib.urlopen`）卡死事件循环 | `api/routers/search.py:43` | 代理深读 |
| 7 | goal.py IDOR 强制开关留三重 env 后门（`AGENTNODES_ENFORCE_STUDY_IDOR` 等；study 域已死、开关名未清） | `api/routers/goal.py:29-34` | 代理深读 |

### 2.2 漂移实证（同一件事多份实现）

| 同一件事 | 现状份数 | 已发生的漂移 |
|---|---|---|
| 列出/搜索会话（同调 `kernel.session.reader`） | 2（`commands/session.py` vs `slash_session.py`） | 列宽、截断（60/80/120）、时间格式各自为政 |
| `message_received` payload | chat.py 手拼 4 处 + slash_common 1 处 | 四种字段形状（`created_at`/`assistant_message_id`/status 有无各异） |
| LLM 配置检测 + provider 目录 | cli 独有 + `system.py` 反向借 7 条 | `~/.quantnodes` vs `~/.agentnodes` 目录分歧；dotenv 读写 3 套实现 |
| skills 目录解析 | mcp 与 cli 两个口径 | **MCP 看不到 workspace `.skills` 覆盖** |
| preset 按名查找 | 3 处逐字 glob 循环（server.py + swarm.py×2） | 唯一正确 helper `apps/swarm_presets.preset_path` 反而零调用 |
| RSI 驱动（预算闸/证据门/基线策略） | rsi_cli + rsi_wiring ~1,180 行住 L1 | MCP/API 宿主复用需重抄 |
| 发送消息用例 | chat.py `_send_via_gateway` + 事件桥 ~500 行 | api 独占；TUI 另走 Gateway 裸路径 |
| 时长格式化 / ellipsis | 3-4 份 / 2 份 | L4 `format_duration`、`ascii_compat.ellipsis` 现成无人用全 |
| webui/api serve 命令 | server.py ~30 行复制 | api 分支已漏 webui 的 env 注入 |

### 2.3 死代码清单（约 2,100 行，全带单测"保鲜"）

- **api 簇 ~550 行**：`schemas/study.py` 全簇 + `schemas/__init__.py` study 导入块（~490 行，独立 AST 扫描同样命中）；死四件套（`web_session.delete_messages`、`agents._CACHE_KEY`、`chat._shell_tools_enabled` 薄壳、`_shape_agents_for_frontend` 恒返回 `[]`）；`chat.py:981-993` 为已删除的 study 域重建会话行
- **cli 簇 ~700 行**：`llm_streaming.py` 全模块 273 行（TUI 已改走 Gateway）；slash 文件旧一代 `run()` 路由 + 12 个 `run_*` 包装（真路由在 `interactive/main.py:_DISPATCH`，双份已漂移）；`commands/__init__.py` 整文件零消费者垫片；`llm_config.py` 三死符号；`registry.dispatch` src 零调用；`slash_router.handler_module` 指向不存在的模块；`_PATH_ENV` 孤儿常量 + 六文件未用 import
- **tui 簇 ~800 行**：ActivityRail/WriteRail/AgentStream* 死事件链；`DAGView`（177 行仅测试引用）；`ui/rail` + `ui/transcript` + `chat_log` 三份 vibe-trading 镜像；死公共方法批（`update_tools_rail_goal`、`run_tui`、`dispatch_slash`、`SlashCompleter`、`render_hint_strip`、`active_surface`、`StreamingText.stop` 等）；3 处无人消费 re-export 垫片（session.py `__all__` 转口、banner.py、interactive/__init__）
- **mcp ~50 行**：DataFrame 序列化死链（`_package_fetch_result` 零调用）；docstring 宣传已删除的工具面
- **误导性文档 3 处**：docstring 宣称已被推翻的机制（registry.dispatch、stream_chat_to_tui、"slash_goal 已移除"假账）

### 2.4 横切指标（AST 全扫）

- **宽 except**：tui/app.py ×34（约 22 处裸 `pass`）、chat.py ×20、rsi_cli ×11 / rsi_wiring ×12 / gateway_wiring ×12、slash_goal ×11、mcp/server.py ×9
- **函数内惰性 import（环味/延迟加载）**：chat.py 33、app.py 30、onboard.py 30、mcp/server.py 29、workflow.py 28
- **模块级可变全局**：`workflow.py`（`_run_store`/`_run_registry`/`_active_runners`）、`auth.py`（`_user_db`）、`auth_tokens.py`（`_SECRET_CACHE`）、`cli/halt.py`、`cli/theme.py`
- **路由间 import 网**：chat.py 10 处函数体内 `from ...routers.web_session import _fetch_session_owned, _get_db`；goal/permission/schedule/workflow 同病——横切安全机制以"路由 A import 路由 B 私有函数"实现
- **kernel re-export 再垫片**：`web_session.py:55-70` 转出 14 个 kernel 私有名（R-014 违例；C1 注释自证兼容期）

### 2.5 P1 级结构问题（下沉时顺带解决）

1. **五套并行服务定位**：container.py / app.state / chat.py `_session_service_cache` / lazy fallback / workflow.py 自建单例——宣称的 DI 通道生产中几乎没走（6 个 provider 只有 `get_memory_manager` 被用一次）
2. **chat.py send-turn 用例 ~500 行**：历史读取失败策略、persist 桥安装时机、receipt/sync 双路径响应形状、Attempt 状态机（`attempt.mark_running()` + `service.store.update_attempt`）全在路由闭包里
3. **RSI 用例族**：预算闸 `check_budget`/`record_cycle`、证据选择 `_select_evidence`、滚动中位数基线 `_baseline_metric`（约 110 行策略逻辑）——docstring 自辩"只有组装根能同时看到"不成立（halt 以 callable 注入的模式 rsi_wiring 自己已示范）
4. **cli/__init__.py 藏三个命令实体**（status/reproduce/init），core_commands 反向函数内 import 回来——环的直接症状；`cmd_status` 一名三义
5. **workflow 路由伸手内核私有**：`runner._owner = user_id` 外部戳私有属性、`store._ensure_conn().execute()` 绕公共接口、`_draft_db()` 在路由里跑 DDL

---

## §3 目标形态

```
        L1 表现层（各留各的形状，禁止互相 import）
   ┌──────────┬──────────┬──────────┐
   │ api      │ cli/tui  │ mcp      │
   │ FastAPI  │ argparse │ JSON-RPC │
   │ SSE/     │ Rich/    │ 工具注册 │
   │ Pydantic │ Textual  │          │
   └────┬─────┴────┬─────┴────┬─────┘
        └──────────┼──────────┘
                   ▼
        L2 apps —— 用例核心（唯一实现）
   apps/chat            send_turn / cancel / attempts_query / 事件词表
   apps/session_views   会话列表/搜索/历史的行整形（唯一版）
   apps/goal            criterion 解析 / AuditRow 合成 / 状态快照
   apps/evolution       RSI 驱动 + 预算闸 + 基线策略（新簇）
   apps/onboarding      llm 配置检测 + provider 目录（api/cli 共用）
        └──────────────────┬──────────────────┘
                           ▼
        L3 kernel / L4 foundation（现状已统一，不动）
   session.reader / gateway / swarm.preset_loader / config.paths
```

### 设计原则（四条）

1. **三样东西不统一**：HTTP SSE 帧/Pydantic schema、Rich widget/Textual App、argparse 装配——各自协议的本体。强行统一会造出 `output_format="tui"` 式参数化巨函数，比重复更糟。
2. **apps 返回领域数据，不携带 UI 概念**：`✅ 已压缩` 文案留在前端拼；apps 返回 `{layers, before_tokens, after_tokens}`。判据：换一个前端不需要改 apps 一行。
3. **R-014 纪律**：下沉后 L1 引用具体模块路径；不造新垫片；旧符号面（tests patch 的位置）按 R-016 出口网迁移。
4. **async 优先**：apps 用例 async 优先；CLI 入口 `asyncio.run()` 包壳（slash_goal 现有模式）。

### 统一已开始的证据

- TUI 纯文本轮已改走 Gateway（`_run_via_gateway`，旧直连 AgentLoop 分支已删）
- `/attempts` 查询已下沉 `apps/chat.attempts_query`（2026-11-18，d9945a5c）
- goal/chat 会话互斥已收 Gateway execution lease（裁决 2，B1-B4）

---

## §4 归拢映射表

| # | 审计发现 | 统一后的家 | 批 |
|---|---|---|---|
| M1 | chat.py send-turn 编排 + 事件桥 + Attempt 状态机 | `apps/chat.send_turn`（样板 attempts_query） | 3a |
| M2 | message_received 四变体 / 引擎词表→前端词表双份 | `apps/chat` 事件词表单点；SSE 桥与 TUI `route_agent_event` 共用 | 3a |
| M3 | session.py vs slash_session.py 双渲染 | `apps/session_views` 返回领域行数据；前端只做渲染 | 3c |
| M4 | rsi_cli + rsi_wiring 用例族 | `apps/evolution`（halt 以 callable 注入） | 3b |
| M5 | slash_goal criterion/审计判定 + `__import__` 反模式 | `apps/goal`；改正常 import | 3c |
| M6 | system.py 借 cli 的 llm 检测 + PROVIDERS | `apps/onboarding`；路径沉 `foundation.config.paths` | 3c |
| M7 | preset 三处 glob | `kernel/swarm/preset_loader.find_preset` 单点（修正死掉的 `preset_path`） | 3c |
| M8 | skills 两口径（MCP 缺 workspace 覆盖） | `kernel.skills` 唯一入口（包内打底 + workspace 覆盖） | 3c |
| M9 | dotenv/llm.json 路径与读写 3 套 | `foundation.config.paths` + `foundation.utils.io_utils` 单源 | 3c |
| M10 | 时长格式化 3-4 份 / ellipsis 2 份 | 直调 L4 `format_duration` / `ascii_compat.ellipsis` | 4 |
| M11 | web_session 14 名再垫片 + 五路由横向 import 网 | `_fetch_session_owned`/`_get_db` 上移 `api/` 顶层（authz/dependencies）；kernel 名直引 kernel | 3c |
| M12 | 五套服务定位机制 | 收敛 container 一条：routers 用 `Depends(get_*)` | 4 |
| M13 | workflow runner 注册表模块级全局 | 下沉 kernel（与 Gateway 单例同模式） | 3a |
| M14 | mcp/server.py 570 行（协议 46 行 + 工具 350 行） | 协议骨架留 server.py，工具按域拆 `mcp/tools_*.py` | 4 |

---

## §5 批次计划

> 每批独立提交、独立回滚；开工前按 R-014/R-015 出实施清单（含逐项行号重核）。

### 批 1 · P0 缺陷修复（低风险，建议立即）

| 项 | 动作 | 验收 |
|---|---|---|
| #1 schedule IDOR | `_load_job_owned` 内部先调 `_require_owner`（四处调用点零改动）；或四端点显式补 | 新增归属测试：他人 session_id → 403；既有 4 端点行为对合法用户不变 |
| #2 resume 绕租约 | `resume_from_checkpoint` 工厂加 `execution_lease=None` 参数；slash_goal resume 传 Gateway 租约 | 双跑 resume+chat 并发测试：lease 互斥生效；不传 lease 时保持旧行为（供单测） |
| #3 TUI cancel 状态机 | immediate 时置 CANCELLED、禁止 resume | worker 状态机单测 |
| #4 `_pending_input` 丢弃 | 在 dispatch/on_synthesize_input 消费，或弹窗回调直接 post | resume 带首条消息的端到端测试 |
| #5 mcp timeout | 删参数（最小）或 handler 实现（线程 + join(timeout)） | MCP 契约测试 |
| #6 search 阻塞 | `run_in_executor` 包裹 | 路由并发不阻塞测试 |
| #7 goal.py env 后门 | 删三重开关（或合并为一个规范名）；与其他路由一致无条件校验 | 无开关时强制生效测试 |

### 批 2 · 死代码大扫除（~2,100 行，机械低风险）

- 前置：R-015 式判定清单（逐项：定义处 + 全仓引用 + 框架注册面三查），本档 §2.3 为底稿，行号须重核
- 动作：整删 study.py 死簇、llm_streaming.py、旧 run() 路由层、三镜像渲染器、死事件链、死公共方法、三处 re-export 垫片；对应测试**归档**（参照 `tests/attic/` 惯例）而非删除；3 处误导 docstring 修正
- 出口：引用计数复扫归零；`batch_gate` 12 步绿；collect anchor 重锚

### 批 3 · 用例下沉（三前端一核心主体，中风险）

- **3a · chat 面**：`apps/chat.send_turn`（M1）+ 事件词表（M2）+ runner 注册表下沉（M13）；chat.py 回到鉴权 + HTTP 形状（~1,289 行 → 预期 ~700 行）；**保持裁决 1 legacy send_message 兼容面不变**（兼容期公告与删除批次另行确认）
- **3b · evolution 面**：`apps/evolution` 新簇（M4）：`run_once` 驱动 + `BaselinePolicy`（可测类）+ 预算闸；rsi_wiring 只剩构造；cli/commands/rsi.py 薄命令
- **3c · 杂项归拢**（M3/M5/M6/M7/M8/M9/M11）：每项独立小批
- 出口：全量 pytest + batch_gate；L1 体量数字同步；graphify 复扫层方向 0 违规

### 批 4 · 一致性收尾（机械）

- M10 格式化函数统一；M12 DI 收敛（chat 缓存/lazy fallback 删除，app.state 收敛）；M14 mcp 拆分 + 协议形状修正（initialize/ping、isError、启动 print 污染）；宽 except 收窄（tui/app.py 34 处、chat.py 20 处优先）；`goal_panel` 展示层改写领域状态（criterion "covered"）收敛到 apps/goal 快照

---

## §6 出口网与验收（全批通用）

1. `batch_gate` 12 步全绿（含体量数字步——每批落地后同步 framework md/HTML）
2. 全量 pytest（`PYTHONUTF8=1`）通过；受影响测试先行
3. 上行违规 0（AST 复核 + graphify 双量纲）
4. **monkeypatch 面审计**：迁移前 grep tests 中对本批移动符号的 `monkeypatch.setattr`/字符串 patch，逐个迁移到新路径（R-016 出口网）
5. 死代码删除项独立成清单可回溯（删除 ≠ 静默）

---

## §7 明确不做

- 不统一三协议本体（原则 1）
- 不造 `output_format` 式参数化巨函数（原则 2）
- 不动 `api/session/` 13 垫片的既有退役计划（mvp-tier-revision-20261117 已记录；并入兼容期公告批）
- 不动 legacy send_message 兼容 API（裁决 1）与旧队列删除时机（裁决 3）
- 不动并行会话未提交工作（loop 簇 §9.6、gateway 设计文档）
- 不在本档内实施——每批开工前另出实施清单

---

## §8 与既有裁决的衔接

| 既有裁决/文档 | 本档关系 |
|---|---|
| R-014（方案先行） | 本档即产物；批 3a/3b 各自仍需实施清单 |
| R-015/R-016 | 批 2 判定清单、批 3 monkeypatch 面出口网直接引用 |
| 裁决 1（legacy send_message = 兼容 API） | 批 3a 下沉 send-turn 时兼容面**原样保留**，漂移测试照跑 |
| 裁决 2（session execution lease） | 批 1 #2 修复 resume 绕租约；批 3a 下沉后 lease 获取留在 Gateway 控制面 |
| 裁决 3（删除时机独立批次） | 本档批 2 的死代码删除**不属于**旧队列删除批次，两者互不含待 |
| agentnodes-split-plan-20261104（"先留业务侧，后续再移"） | api/session 下沉已兑现；本档完成 interfaces 剩余部分的同款兑现 |
| kernel-module-splitting-design-20261118（S1/S2） | 无重叠；本档不动 kernel 内部 |

---

## §9 待裁决点（后续讨论输入）

1. **批 1 是否立即开工**——#1 schedule IDOR 是真实安全缺口；其余六项为缺陷修复，一次批完还是逐项批？
2. **批 2 死代码删除的批准粒度**——按本档 §2.3 清单一次性批准，还是逐簇（api/cli/tui/mcp）四份清单分别批？
3. **`apps/evolution` 新簇归属**——RSI 用例下沉目标位是否接受？或并进既有 `apps/goal`？
4. **事件词表的家**——`apps/chat`（前端契约）vs kernel events（引擎侧）？本档倾向 apps/chat（词表本质是前端契约）
5. **`apps/session_views` 命名**——或归 `apps/chat`（会话视图与聊天强相关）？
6. **批 3 与并行批次（§9.6 超时加固）的顺序**——chat.py 是双方共同热区，建议等并行批落地后开工 3a

---

## §10 执行记录

（待批次开工后回填：每批的提交号、实测数字、判定清单偏差。）
