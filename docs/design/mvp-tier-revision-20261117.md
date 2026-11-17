# MVP 三层归层判定修订 —— R-014 会话下沉后的 kernel/session 归层

> **日期**: 2026-11-17 · **状态**: 已确认，执行中 · **范围**: 只改**图与数据层**，不动生产代码
> **触发**: 审阅 `docs/architecture-layered-l3-clusters.html` 时发现 —— 红层（最小可对话）在「上下文与状态」容器里一根红条都没有

## 1 · 结论摘要

1. **MVP 有上下文管理与状态管理**，且状态管理整块住在 `kernel/session`（会话域真身）。
2. 图上「C2 上下文与状态」没有红色，**不是 MVP 设计缺失**，是**归层判定停留在 R-014 下沉之前**：
   `kernel/session` 当时只是 6 个符号的「只读浏览门面」，判定为绿（C）；下沉后它变成 89 个符号的会话域真身，
   而判定没跟着走。
3. 由此产生一个可命名的自相矛盾：**红层能力集声明含 C8「历史持久化」，图上 CAPS 也写明 C8 的实现者含
   `kernel/session`，但 `kernel/session` 本身被涂成绿色。**
4. 修订：`kernel/session` 归层 **C → A（红）**。判据不变（红层按**能力集**标注，不是 import 闭包）。

## 2 · 证据（全部可复算）

### 2.1 最小对话链路上的状态管理（红层包的硬依赖）

| 环节 | 位置 | 依赖的会话域能力 |
|---|---|---|
| HTTP 入口 | `interfaces/api/routers/chat.py:33` | `from agentnodes.kernel.session.service import SessionService` |
| 异步发送 | `chat.py:276` | `service.send_message(...)` —— 落库 + 入队 + 发事件 |
| 同步发送（读历史） | `chat.py:422` | `service.store.get_messages(session_id, limit=100)` |
| 历史 → LLM 消息 | `chat.py:423` | `service._convert_messages_to_history(...)`（C3 的「历史」半边） |
| 建会话行 | `chat.py:770` | `ensure_session_row`（`kernel/session/db.py`） |
| 组合根装配 | `interfaces/api/container.py:85-107` | `SessionStore` + `Projector` + `SessionService` |

包级依赖实测（`scan_arch.py` 口径，每条 import 语句计 1 次）：

```
interfaces/api  → kernel/session   29 次      ← 红层包 → 绿层包（闭包破口）
kernel/session  → kernel/agent     13 次
kernel/session  → kernel/events     3 次
```

### 2.2 「只读门面」判定已被自己的迁移推翻

`tmp/probe/mvp_tiers_data.py` 里的原判定（2026-11-16，R-014 下沉**之前**）：

> `("C", "CLI / MCP / TUI 的会话浏览门面（列·搜·删）—— 只读查询，不在对话链路上")`

R-014 下沉后实测：

| 判定原文 | 现状 |
|---|---|
| 「只读浏览门面」 | 浏览门面只剩 `kernel/session/reader.py`（6 符号，仅 cli / mcp / tui 使用）；其余 **80/89 符号**是会话域真身（service / store / db / models / history_convert / projector / …） |
| 「不在对话链路上」 | 被推翻：`interfaces/api/routers/chat.py` 4 次 import + `container.py` 3 次；`/send`、`/send_async` 都走它 |

`interfaces/api/session/` 下 13 个文件现已是**纯 re-export 垫片**（实体实现全在 `kernel/session`），
只有 `web_session_state.py`（前端 DTO）仍留在 L1。

### 2.3 图上的自相矛盾（committed 原版图同样存在）

- `docs/architecture-layered.html` 的 `CAPS` 里，C8 的 providers = `["kernel/session", "foundation/storage"]`
  —— 图自己声明 C8 由 `kernel/session` 实现；
- 同一张图里 `kernel/session` 的 tier = **C**，tierWhy = 「只读查询，不在对话链路上」，n = 6（陈旧）；
- `tests/test_arch_layered_html.py:52-57` 已经把「C8 的实现者含 kernel/session」钉成契约
  —— **契约网早已认定 kernel/session 是 C8 的实现者，只有色条没改**；
- `foundation/storage` 的 tierWhy 写着「领域 owner 已下沉 kernel/session（R-014 批次 B）」，
  同样是「知道归属、没改色条」。

### 2.4 红层本来就不是「可裁剪子集」（判据澄清）

红层按**能力集**标注（2026-11-16 拍板），不是 import 闭包 —— 实测闭包会把 32/36 个包都算进红色。
红层指向非红层的**项目内**依赖实测 **118 次 import**（口径：`scan_arch.py`，每条 import 语句计 1 次、包级；
**修订后**的数值 —— 修订前 `interfaces/api → kernel/session 29` 是最大破口，kernel/session 归红后这条变成红→红）：

| 红层包 | → 非红层 | 次数 |
|---|---|---|
| interfaces/api | apps/goal | 14 |
| interfaces/api | kernel/workflow | 13 |
| interfaces/api | apps/scheduled_research | 12 |
| kernel/agent | foundation/utils | 10 |
| kernel/gateway | kernel/orchestration | 10 |
| kernel/agent | foundation/observability | 8 |
| interfaces/api | interfaces/cli | 7 |
| kernel/gateway | kernel/runtimes | 5 |
| kernel/agent | foundation/web | 4 |
| kernel/agent | kernel/memory / kernel/skills / kernel/context | 各 3 |
| kernel/gateway | kernel/context | 3 |

> 结论：**「红层里没有上下文与状态管理」这个说法不能从图上推出**（红层本就不是封闭集）。
> 成立的是「红层声明有 C8，却没有实现 C8 的包」——这一条被 R-014 制造、被色条掩盖。

## 3 · 判定修订

| 对象 | 修订前 | 修订后 | 理由 |
|---|---|---|---|
| `kernel/session` 的 tier | C（绿 · 全能力） | **A（红 · 最小可对话）** | 会话域真身承载 C8（历史持久化）、C3 的历史侧、C7 的会话标识；红层主链路硬依赖 |
| `kernel/session` 的 tierWhy | 「只读浏览门面……不在对话链路上」 | 会话域真身（service/store/projector/db）…… | 原判定已被 R-014 推翻 |
| C8 的 providers | `["foundation/storage"]` → 图内已是 `["kernel/session","foundation/storage"]` | 保持 | 底座 + 实现者两个都要在图上可见 |
| `foundation/storage` 的 tierWhy | 「历史持久化（C8）的 SQLite 后端」 | 明确为**底座**，实现者是 kernel/session | 避免「底座红 = 实现红」的误读 |

**不变的部分**：判据（能力集）、红黄绿三层的定义、C1–C28 的分层归属、`kernel/context`（黄 · C15 注入）、
`kernel/memory`（绿 · C24 记忆）、`kernel/evolution`（绿）、`kernel/reader` 的绿属性。

> `kernel/session` 是**包级**节点，其内 `reader.py`（浏览门面）与 `backfill_event_log.py`（维护脚本）
> 仍属绿层能力 —— 包级粒度无法在图上拆开，故在 tierWhy / tooltip 里注明。

## 4 · 红层性质的免责说明（本次一并补）

图上红层容易被误读成「最小可运行部署单元」。实测它**不是**（红→非红 118 次 import），
它的含义是「**最小可对话所需能力的实现归属**」。这句话要写进图注，否则「红层有 C8 却没有会话包」
这类误读会反复出现。

## 5 · 同步点（三处，必须同时改，否则图之间会互相矛盾）

| # | 位置 | 性质 | 状态 |
|---|---|---|---|
| 1 | `tmp/probe/mvp_tiers_data.py` | 原版图的**管线数据源** | ⚠ **未入库**（`tmp/` 在 .gitignore:130） |
| 2 | `docs/architecture-layered.html` | 原版图**生成物**（入库） | 由管线重跑刷新 |
| 3 | `docs/architecture-layered-l3-clusters.html` | v2 草稿（手工数据，未入库） | 手工同步色条与文案 |

**新增债 D4（本次暴露）**：原版图的数据源与管线脚本全部躺在 `tmp/probe/`（gitignored），
只有生成物入库。已发生的实际后果：R-014 F 批更新 DEBTS / CAPS / SESSION_LIFECYCLE 时**只改了生成物**，
数据源没同步 → 现在直接重跑管线会把「D1 已处置」退回「未实施」。
处置：本次先把生成物里的现行值**回灌**到数据源（让数据源重新成为唯一事实源）；
脚本本身是否提升为入库的 `scripts/arch/`，另案处置（不在本次范围）。

## 6 · 校验

- `tests/test_arch_layered_html.py` —— 已钉住 C8 providers / D1·D2 已处置 / D3 开放 / 生命周期八阶段 owner；
  本次只改 tier 色条与文案，不触碰这三组数组的语义 → 该测试必须仍全绿。
- 重跑管线后核对：`kernel/session` 符号数 6 → 89、`interfaces/api` 333 → 251、边 137 → 143。
- 两张图的 `kernel/session` tier 必须一致（都是 A），否则又是一处漂移。

### 6.1 实际验证结果

| 项 | 结果 |
|---|---|
| 契约网 `tests/test_arch_layered_html.py` | **11 passed**（CAPS / DEBTS / SESSION_LIFECYCLE 三组数组与 committed 版**逐条完全一致**） |
| 重跑管线后头部统计 | 36 包 / 1977 符号 / 143 边（`kernel/session` 6→89、`interfaces/api` 333→251） |
| tier 变化 | 只有 `kernel/session` C → A（预期内）；`foundation/storage` tierWhy 改为「底座」口径 |
| 红层破口（scan_arch 口径） | 118 次（修订后；`interfaces/api → kernel/session 29` 因归红而不再是破口） |
| 数据源回灌 | ARCH_DEBTS 3 条 / SESSION_LIFECYCLE 8 阶段 / C8 providers / SESSION_ROLE 已与生成物对齐 |

## 7 · 回滚

本修订只动**图与数据层**（HTML + 数据源），不碰生产代码：`git revert` 对应 commit 即可。
