# 第三轮复审两个 Strong 候选收口 R-014 方案

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **来源**：第三轮架构复审（`C:\Users\1\AppData\Local\Temp\architecture-review-20261120-163911.html`，OS temp 不入库）两个 Strong 候选。
> **前置**：前两轮 14 候选已全部处置（#6 冻结）；本方案不含任何删除批动作。
> **范围**：G1 = emit 显式化并删 `_GwProxy`；S1 = 归属校验公共 adapter 收口。两批独立提交，G1 先行。

## 0. 目标与非目标

**目标**：
- G1：把 RSI 事件发射器从"经 Gateway 私有字段 `_combined_sink` 走私"改为 `build_rsi_loop` 的显式 keyword-only 参数；删除唯一职责是覆写该私有字段的 `_GwProxy` 浅 adapter；测试从私有字段断言改为接口行为断言。
- S1：把"请求 → user_id → db → `_fetch_session_owned`"这段在 21 处重复的 adapter 收进 `authz.py` 一个深 helper；端点签名零改动，三处真实特例留在本地。

**非目标**：
- 不改 `RsiHost` interface、不改 `build_rsi_host` 装配（前两轮已收口）。
- 不改 `wire_gateway`（它本就有显式 `emit` 参数，`_combined_sink` 仅作默认回落，属另一个关注点）。
- 不改 `_fetch_session_owned` 的位置（M11a 已定在 `interfaces/api/authz.py`），不删任何过渡 re-export。
- 不触碰 goal.py 三枚 IDOR 旁路开关、chat study-404 自动重建、permission 条件检查、workflow live-runner 短路——均保持原语义与位置（goal 开关属冻结删除批）。
- 不改 GLOSSARY：两个候选都是现有 module 的接口收口，未命名新深模块；`emit` / "会话归属校验"均为通用概念，不满足本项目特有领域名词门槛。无 ADR 触发（无候选被否决）。

## 1. 现状证据（已回读源码核对）

### G1
- `rsi_wiring.py:161` `def build_rsi_loop(gw, *, seed=0)`；`:176` `emit = getattr(gw, "_combined_sink", None) or (lambda k, d: None)`（`_combined_sink` 是 `gateway.py:257` 的绑定方法）。
- `cycle_driver.py:304-319` `_GwProxy`：唯一职责 `object.__setattr__(self, "_combined_sink", emit)`，让 `build_rsi_loop` 能挖出注入的 session-scoped emit；`:362` 唯一调用 `build_rsi_loop(_GwProxy(gw, emit), seed=seed)`。
- `cycle_driver.py:557` `emit = _session_emitter(gw, sid)` 产出 emit，经 `_assemble_loop → loop_builder → _GwProxy._combined_sink → build_rsi_loop` 绕一圈。
- `rsi_wiring.py:282-304` `_heldout_kwargs(gw, *, seed, emit)` 函数体完全不引用 `gw`（已核实）。
- 测试锚：`test_evolution_cycle_driver.py:209-225` 断言 `captured["gw"]._combined_sink` 可调用（私有字段）；`test_production_wiring.py:169,195` 两处 `build_rsi_loop(gateway, seed=42)`。
- 另一处 `_combined_sink` 读取 `gateway_wiring.py:137` 在 `wire_gateway` 内，该函数已有显式 `emit` 参数——不在本批范围。

### S1（事实清单已核对；修正复审报告的 "~24" 为 **21**）
- `_fetch_session_owned` 定义 `authz.py:19-38`（docstring "15 处"已过期）。
- 21 处调用点：chat.py 10（9 函数，chat_events 有 2 处）、web_session.py 5、workflow.py 3、goal.py 1、permission.py 1、schedule.py 1。
- user_id 口径：15 处 `getattr(..., "anonymous")`（chat 10 + web_session 5），6 处 `getattr(..., None) or "anonymous"`（goal/permission/schedule/workflow 3）。
- db 获取：20 处内联 `_get_db()`（`kernel/session/db.py:588`，thread-local 缓存）；零 `Depends(get_db)`；仓库无 `get_db` 符号。
- goal/schedule/workflow 已有本地 wrapper（`_session_owned` / `_require_owner` / `_require_run_access`），是特例的天然归属地。
- FastAPI `Depends` 先例仅 admin_users.py / auth.py 两处；`app.dependency_overrides` 只在 docstring 出现，无测试使用。

## 2. 设计（grilling 全 A 共识）

### G1（Q1-Q4）

| # | 决策 | 落位 |
|---|---|---|
| Q1 | `build_rsi_loop(gw, *, emit, seed=0)`：emit 必填 keyword-only，删 `getattr(gw, "_combined_sink")` 行 | `rsi_wiring.py:161,176` |
| Q2 | 删整个 `_GwProxy` + 删 `or (lambda k, d: None)` 静默回落；no-op 保留在生产者 `_session_emitter`（显式） | `cycle_driver.py:304-319,362`；`rsi_wiring.py:176` |
| Q3 | 同批删 `_heldout_kwargs` 的未用 `gw` 参数 | `rsi_wiring.py:282,240` |
| Q4 | 测试锚改行为断言；同步 `test_production_wiring.py` 两处调用 | `test_evolution_cycle_driver.py:209-225`；`test_production_wiring.py:169,195` |

**实现要点**：`make_loop_builder` 的 `builder(gw, emit, seed)` 直接 `build_rsi_loop(gw, emit=emit, seed=seed)`；`_session_emitter` 的 no-op 分支不动（它在生产者侧显式存在，不再有 build_rsi_loop 内的第二份静默路径）。

### S1（Q5-Q8）

| # | 决策 | 落位 |
|---|---|---|
| Q5 | **普通深 helper**（非 `Depends`）：`authz.enforce_session_owned(request, session_id) -> Row`，内部独占 user 提取 + `_get_db()` + 调 `_fetch_session_owned`；端点签名零改动 | `authz.py` 新增；21 处各改一行 |
| Q6 | user_id 统一 `getattr(request.state, "user_id", None) or "anonymous"`（与 `auth.get_current_user_id` 一致） | helper 内部唯一实现 |
| Q7 | 三处特例留本地：chat study-404 重建、goal 三开关、permission 条件检查、workflow live-runner 短路不动 | 各 wrapper 内 |
| Q8 | 两批独立提交；各补 helper 直接行为单测；全量 + gate + 数字同步；不 push | 批次表 |

**实现要点**：
- helper 用 lazy `from agentnodes.kernel.session.db import _get_db`（匹配全部 21 处现有 idiom，避免 authz.py 模块级 downward import 时序变化）。
- chat `chat_events`：保留 try/except，两处调用换 `enforce_session_owned(request, session_id)`，删本地 `user_id`（仅用于这两处）。
- goal `_session_owned`：三枚开关与顺序不变，只把 35-38 行换成一次 helper 调用；删本地 user_id。
- permission：保留 `if owner_session:` 守卫，换 `enforce_session_owned(request, owner_session)`；`user_id` 若仅用于该检查则删（实施时核实）。
- schedule `_require_owner`：45-49 行换一行。
- workflow `_require_run_access`：保留 `user_id` 用于 live-runner owner 短路比较，只把 `:79` 的检查换 helper；`:98,:630` 换一行。
- web_session 五处：`row = enforce_session_owned(...)`（get_session/update_session 用 row）；`conn = _get_db()` 若后续语句仍用则保留（thread-local 同一连接对象，零行为变化）。
- 新增 `tests/test_session_ownership.py`：fake request（含 `user_id=None` 边界）+ monkeypatch `kernel.session.db._get_db` 返 fake conn，断言 owner 返回 row / 404 / 403 透传、`None → "anonymous"`。现有 9 个 E2E 测试文件全部不动。

## 3. 分批与验收

| 批次 | 内容 | 验收 |
|---|---|---|
| G1（先） | emit 显式化 + 删 _GwProxy + _heldout_kwargs 瘦身 + 测试锚改行为 | 全量绿；`test_evolution_cycle_driver` 无私有字段断言；gate + 数字同步 |
| S1（后） | 21 处换 helper + 新增 `test_session_ownership.py` + 特例零改动 | 全量绿；9 个 E2E 鉴权文件不动且绿；`_fetch_session_owned` 定义位置不变；gate + 数字同步 |

**回滚**：每批单提交回退即恢复；两批互不依赖。

## 4. 风险与回滚

- **G1**：`build_rsi_loop` 变成必填 emit 后，`test_production_wiring` 两处必须补 emit（可用 capture lambda）。生产唯一调用链经 `make_loop_builder`，已握有 emit——无遗漏调用方。
- **S1**：web_session 五处此前把 `conn` 传给 helper，现 helper 内部 `_get_db()`；因 thread-local 缓存是同一对象，且 `conn` 变量在后续语句仍可用，零行为变化。permission 的 `user_id` 是否为该函数其他语句所用需实施时核实，若仍用则保留。
- **口径统一**：Q6 把 15 处 `"anonymous"` 默认值改为 `None or "anonymous"`；差异仅在"属性存在但为 None"，中间件已保证认证用户为字符串、无 token 在中间件即 401/403，实际不可达。新增单测钉住该口径。
- **数字漂移**：每批源码行数变化后重跑 `sync_architecture_diagram.py --write` + `check_framework_numbers.py`，同步两份文档锚点。

## 5. 明确不做

① 任何删除批动作（僵尸分支 / 过渡 re-export / 14 垫片 / seq_generator / prune / preset_path 等）；② 改 RsiHost / build_rsi_host / wire_gateway；③ 把 helper 做成 `Depends`（Q5 已否决）；④ 移动 `_fetch_session_owned`；⑤ 改 goal 三开关 / chat study-404 / permission 条件 / workflow live-runner 短路。

## 6. 实施前确认

本方案来自 grilling 全 A 共识（Q1-Q8）。**未获批不得实施任何代码**；获批后按 R-007 分两批独立提交，doc first 纪律已满足（本文件）。

## 7. 执行记录（2026-11-20 · 两批已实施 · 用户"开始吧"批准）

### G1 · `43b76401`（7 files，+33/-43）
- `rsi_wiring.py`：`build_rsi_loop(gw, *, emit, seed=0)` 必填 emit；删 `getattr(gw, "_combined_sink")` 与静默 no-op fallback；`_heldout_kwargs` 删未用 `gw`。
- `cycle_driver.py`：删整个 `_GwProxy`；`make_loop_builder` 直接 `build_rsi_loop(gw, emit=emit, seed=seed)`。
- 测试锚：`test_evolution_cycle_driver.py:209-225` 从 `assert callable(captured["gw"]._combined_sink)` 改为行为断言（emit 透传 + 真发事件）；`test_production_wiring.py:169,195` 两处调用补显式 emit。
- 验证：RSI 面 67 passed；全量 `-n 6` 8045 passed；batch_gate ALL GREEN；数字 464/96,893（L1 21,929 / L2 11,542）。

### S1 · 本批（21 处收口 + 新单测）
- `authz.py`：新增 `enforce_session_owned(request, session_id) -> Row`（lazy import `_get_db`，统一 `None or "anonymous"` 口径，包装 `_fetch_session_owned` 位置不动）。
- 21 处调用点：chat 10 / web_session 5 / workflow 3 / goal 1 / permission 1 / schedule 1 全换 helper 一行；三处特例原样保留（chat study-404 try/except、goal 三开关、permission 条件、workflow live-runner 短路 + start-definition owner stamp）。
- `test_session_ownership.py` 新增 5 例（owner 取 row / 403 / 404 / user_id=None 与属性缺失回落 anonymous）。
- 实施中发现并修复：`workflow.start_definition` 的 `runner._owner = user_id` 是真实本地用途，恢复其局部提取（非归属校验重复）。
- 验证：归属 E2E 面 136 passed / 1 skipped；全量 `-n 6` **8050 passed** / 0 failed（+5 新单测）；batch_gate ALL GREEN；数字 464/96,879（L1 21,915）。

### 共同事实
- 每批独立提交，不 push；`sync_architecture_diagram.py --write` + `check_framework_numbers.py` 已同步 md/HTML 锚点。
- 环境插曲：`set PYTHONUTF8=` 曾给子进程留下空值导致 82 个 subprocess 类测试假失败；确认根因后以 `PYTHONUTF8=1` 重跑全绿，非代码回归。G1 的 `-n 12` 曾因资源压力 node down，按 rules §2.1 降 `-n 6` 通过。
