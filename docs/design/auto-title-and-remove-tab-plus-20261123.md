# 会话标题自动生成 + 移除 SessionTabs 加号方案（R-014 · 待批准 · 零代码）

> 日期：2026-11-23
> 性质：方案。批准后才动代码；本文不改任何实现。
> 触发：用户选定 B+A——B：首次发送自动生成会话标题（治本，解决三个「新会话」重复）；A：删除 SessionTabs 末尾加号（消除明确冗余）。
> 注意：本方案**替换** `remove-session-tabs-20261123.md`（上一版「全删 tab」方案未采纳）。

## 1. 核心发现（agent 实测）

**仓库已有完整可用的自动标题实现，但生产 Web 发送链路没接**：
- `kernel/session/service.py:1560-1597` `auto_title_session(session_id, content)`：纯文本截断（30 字 + `…`），空内容跳过，哨兵 `if row["title"] != "新会话": return None`
- `kernel/session/service.py:223-248` `SessionService.send_message`（legacy/TUI）已在首条消息调用它，并 emit `session_meta_updated` SSE
- 前端 `hooks/sse/metaHandlers.ts:62-79` 已消费该事件，patch `sessions[].title`（`useSSE.ts:126-131`）
- 生产链路 `POST /api/chat/send_async`（`chat.py:165-316`）**绕开了这一切**——所以三个 tab 都叫「新会话」
- 6 处前端调用点全部显式传 `'新会话'`（`SessionSidebar:62`、`SessionTabs:59`、`CommandPalette:107`、`AppShell:112`、`useKeyboardShortcuts:47`、`session.ts:405`），时间戳默认标题从未生效

**结论**：B 不是新建功能，而是「把已有单点接回生产链路」。

## 2. 批次与实施

### 批 B：自动标题接回 send_async（后端为主，前端零改动）

1. **抽单点**：在 `SessionService` 新增 `auto_title_and_emit(session_id, content)`，封装 `service.py:223-248` 的逻辑（调用 `auto_title_session` → 回读完整 meta → emit `session_meta_updated`）；legacy `send_message` 改为调用它（消除两份实现漂移风险）
2. **接线**：`chat.py::send_async` 在拿到 `service` 后（`:208` 附近）、创建 attempt 前调用 `service.auto_title_and_emit(session_id, body.content)`；异常只 warning 不阻断（与 legacy 一致）
3. **前端零改动**：`metaHandlers.ts` 已 patch title → tab/侧栏自动显示新标题

**关键约束**：
- `auto_title_session` 的哨兵是 `title == "新会话"`——**保留前端 6 处 `'新会话'` 传参不改**（若改成时间戳默认标题，自动标题永不触发）
- meta_update 必须含完整字段（title/message_count/starred/tags/archived），否则 `patchSessionMeta` 浅合并会把其他字段 undefined 化

### 批 A：删除 SessionTabs 加号（前端小改）

1. `SessionTabs.tsx:58-64`：删除末尾 `<button onClick={() => createNewSession('新会话')} title="新建会话 (⌘T)">` + `Plus` import + `createNewSession` 解构
2. `test/SessionTabs.test.tsx`：L46 存在断言 + L91-94 点击断言 —— 这 2 条改为「加号不存在，新建由侧栏承载」的反向断言
3. 保留 tab 条本身（工作集可视化）与 Cmd+T 快捷键（新建仍可达，侧栏也有按钮）

## 3. 测试计划

**后端**：
- `tests/test_kernel_session_auto_title.py`：已有 8 条行为矩阵保持
- `tests/test_session_service_units.py`：加「auto_title_and_emit 第二条不重复 emit」
- `tests/test_webui_api.py::TestAutoTitle`：加端到端——创建 `title='新会话'` → `POST /send_async` → `GET /session/{id}` title 已变 + SSE 可见 `session_meta_updated`；自定义标题会话发送后 title 不变

**前端**：
- `SessionTabs.test.tsx`：加号不存在的断言（2 条改造）
- `metaHandlers.test.ts`：已有 title patch 用例保持
- 可选：`sessionSidebar.test.tsx` 加「SSE title patch 后列表文本更新」

## 4. 验收标准

1. 创建「新会话」→ 发第一条「分析 20 日动量因子 IC」→ tab 与侧栏显示「分析 20 日动量因子 IC」（≤31 字符、换行折叠）
2. 发第二条消息 → 标题不变
3. 用户手动重命名 → 再发消息 → 标题保持用户定义值
4. tab 条无加号；侧栏「新建会话」与 Cmd+T 仍可用
5. 前端全量测试 / tsc / lint、后端全量 pytest、batch_gate 13/13、双主题基线重录（tab 条少一个按钮，主会话截图变化）

## 5. 风险与回滚

| 风险 | 处置 |
|---|---|
| `auto_title_session` 直连 DB 与 projector 并发写同一行 `updated_at` | 保持 legacy 已有行为（warning 吞异常），不引入新锁；端到端测试覆盖 |
| `send_async` 经 Gateway 异步流，标题 SSE 事件与实际消息落库顺序 | 标题事件在 attempt 创建前 emit，先于消息 SSE，前端 patch 无竞态 |
| 删除加号后 Cmd+T 仍存在（新建入口未全归侧栏） | 用户要求「加号无用」，未要求删快捷键；Cmd+T 属键盘效率入口，保留 |
| 三条历史「新会话」不会自动变（只对新发送生效） | 可接受——历史会话用户可手动重命名 |

两批独立提交（B 后端 + A 前端），各自可 revert；无数据迁移。

## 6. 不做什么

- 不删 tab 条本身（上一版方案未采纳）
- 不改 `auto_title_session` 的哨兵与截断逻辑（已验证的现成实现）
- 不改前端 6 处 `'新会话'` 传参（哨兵依赖它）
- 不动 Trace / DAG 方案（独立）
