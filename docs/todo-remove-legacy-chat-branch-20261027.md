# 待办 — 删除旧 chat 分支（标记，未执行）

> 状态：**已标记，延后执行**。2026-10-27 首标，2026-10-30 复核更新。
> 用户决策：**等业务稳定后再做**。

---

## 0. 2026-10-30 复核（B4/B5 之后）

### 0.1 待删点仍在（实测）

| 位置 | 内容 | 状态 |
|---|---|---|
| `api/routers/chat.py:845-846` | `/send` 的 `new_entry_enabled()` 分支 | 仍在 |
| `cli/tui/session.py:236-238` | CLI 的 `new_entry_enabled()` 分支 | 仍在 |
| `core/gateway/entry_adapters.py:22` | 门函数本体 | 仍在 |
| `core/gateway/__init__.py:11,26` | 导入 + `__all__` | 仍在 |

### 0.2 新增的前置（原清单没有）

| # | 事项 | 说明 |
|---|---|---|
| **N1** | `ContextHub` 接线改变了「旧路径」的含义 | B5 之前 Gateway 的 `_context_hub` 恒 None（B1-b 注入只在旧路径活着）；B5 `1a9f2c49` 之后 Gateway 有了 hub + pre-run provider + todos。**删旧路径不再丢失上下文注入**——这是原清单假设「新路径已等价」时还不成立的一条 |
| **N2** | `MemoryManager.append` 已事件化（`73351b3d`） | 旧路径的 `_persist_user_message` 与新路径现在写同一张表、同一套事件。删除时不必担心「历史写不进去」 |
| **N3** | 旧会话栈已退役（`594441df`） | 原清单 §3 第 3 项「study/goal/hypothesis 是否依赖 SessionService」需重新核：`SessionService` 本身还在，但它底下的 `core/session/db.py` 已删。**若 SessionService 还引用旧栈，那早该炸了**——实测没有，说明它已只用统一库 |
| **N4** | `_run_agent_loop_background`（`chat.py:395`）零调用 | B5 调查实测：全仓无调用方。原清单把它列为「删除后成为死代码」，**其实现在就是死代码**，可以独立于 P5-1c 先删 |

### 0.3 建议的开工顺序（在原 §4 基础上修正）

```
⓿ 先删 _run_agent_loop_background（已是死代码，N4）
① 删 CLI 分支（影响面小）
② 删 API /send 分支
③ 观察一轮（跑测试 + 手动验证 WebUI）
④ 删 new_entry_enabled 函数与导出
⑤ 评估 /send_async + SessionService（见 §3 第 1 项，仍是最不确定的一块）
```

---

## 1. 为什么可以删了

P5-1 + P5-1b 已把新路径补齐到与旧路径等价：

| 能力 | 状态 |
|---|---|
| 历史注入 | ✅ `SessionStore.get_messages` + `_convert_messages_to_history` |
| 消息持久化 | ✅ `event_bus.emit` → projector |
| `message_id` | ✅ |
| 流式输出 | ✅ llm/react 节点 `stream=True` |
| 工具链 | ✅ Gateway 装配 `build_default_registry()` |
| 推理不泄漏 | ✅ MiniMax `<think>` 跨 chunk 缓冲（`3d03ed65`） |
| 真实 LLM 验证 | ✅ 3 项（流式 / 工具 491 / 历史 742） |
| **上下文注入（goal/memory/todos）** | ✅ B5 `1a9f2c49` + `db8bc7b8` + `38ad7786`——**2026-10-30 补** |

## 2. 要删的东西

**入口分支**（2 处）：

| 文件 | 行 | 内容 |
|---|---|---|
| `api/routers/chat.py` | 875-876 | `new_entry_enabled()` 分支（`/send` 的旧路径） |
| `cli/tui/session.py` | 236-238 | CLI 的 `new_entry_enabled()` 分支 |

**门本身**：

- `core/gateway/entry_adapters.py:22` `new_entry_enabled()` 函数；
- `core/gateway/__init__.py` 的导入与 `__all__` 条目。

**旧路径代码**（删除后成为死代码）：

- `api/session/service.py::SessionService.send_message` 及其队列（`_process_session_queue` / `_run_attempt`）；
- `api/routers/chat.py::_run_agent_loop_background` 及 `_persist_user_message` / `_persist_assistant_message`；
- 相关测试的旧路径用例。

## 3. 删除前必须确认的

| # | 检查项 | 说明 |
|---|---|---|
| 1 | `/send_async` 端点 | 它独立于 `/send`，走 SessionService 的队列。删除 SessionService 会连带影响——**需单独评估是否也在本次迁移范围** |
| 2 | `loop_stream` / SSE 流式管线 | 旧路径的逐 token 推送用了 `_PartAccumulator`，新路径用 `install_sink`。确认前端消费的事件名一致 |
| 3 | study / goal / hypothesis 是否依赖 SessionService | 这些域可能通过 session 队列发消息 |
| 4 | CLI 的 `_run_via_gateway` | 已存在且可用（P5-1 前就接了 MemoryManager 历史），删门后成为唯一路径 |
| 5 | 全量回归 | 删除后跑完整套件（非 live + live） |

## 4. 建议的删除顺序

```
① 先删 CLI 分支（影响面小，CLI 的 Gateway 路径已验证）
② 再删 API /send 分支
③ 观察一轮（跑测试 + 手动验证 WebUI）
④ 删 new_entry_enabled 函数与导出
⑤ 清理死代码（SessionService 等，需先做 §3 检查）
```

**不要一次性全删**——入口分支和 SessionService 是两个独立层次，分开可回退。

## 5. 恢复方式

若删除后发现问题：`git revert <commit>`。因为删除是独立提交，回退干净。

---

## 6. 相关文档

- `p5-1-chat-gateway-parity-design-20261027.md`（历史/持久化设计）
- `p5-1b-streaming-tools-design-20261027.md`（流式/工具链设计）
- `stage-4-cleanup-checklist-20261027.md` §7（阶段 5 待办清单）

---

## 7. 执行记录（2026-11-01）：完成第 ⓿ 步，其余保持

按 §0.3 的顺序，**只做了第 ⓿ 步**（删真死代码），未动入口分支。

### 已完成 ⓿：删 `_run_agent_loop_background` 及其专属 helper

| 项 | 内容 |
|---|---|
| 删除 | `api/routers/chat.py` 214–525 行（313 行） |
| 涉及符号 | `_PartAccumulator`（含 12 个 `_on_*` 方法）、`_persist_user_message`、`_auto_title_and_notify`、`_persist_assistant_message`、`_emit_config_error`、`_emit_agent_done`、`_emit_agent_error`、`_run_agent_loop_background`、`_run_test_script` |
| 删除依据 | 全库零调用方（仅剩注释与文档字符串引用）；每个 helper 的引用数都是「定义 + 死函数内调用」= 2 |
| 保留 | `api/session/part_accumulate.py` 是**另一份实现**，被 `loop_events.py` / `service.py` 使用，属活代码 |
| 文件规模 | chat.py 1360 → 1047 行（−23%） |

**连带清理一个自指测试**：`tests/test_text_part_routing.py` 的最后一个用例
（80 行）注释里写着「We replicate the relevant branches here」——它复刻
`_run_agent_loop_background` 的 on_event 逻辑再自己断言。源实现删除后，
被测对象变成测试自己写的模拟，价值归零。已删除；同文件其余 10 个用例测的
是活的 `_accumulate_part`，保留。

### 未做：入口分支与 `new_entry_enabled`（有意保留）

§0.3 的 ①–④ 步（删 CLI/API 分支、删门函数）**没有执行**，原因不是技术
障碍，而是决策性质不同：

`new_entry_enabled()` 读取 `QNODES_NEW_ENTRY` 环境变量，是**生产可回退
开关**（见 `entry_adapters.py:22-35`）。删除它意味着放弃「出问题时退回旧
路径」的能力——这是一次不可逆的生产行为变更，不是死代码清理。

按 §3 的自检清单，第 1 项仍未结：

| # | 检查项 | 状态 |
|---|---|---|
| 1 | `/send_async` 端点是否随 SessionService 一起删 | ❌ **未评估**——它独立于 `/send`，走 SessionService 的队列，删门会连带影响 |
| 2 | `loop_stream` / SSE 事件名一致性 | ✅ B5 已验（新路径 install_sink） |
| 3 | study / goal / hypothesis 是否依赖 SessionService | ⚠️ 部分依赖（`/study` 命令拦截仍在 chat.py 内） |
| 4 | CLI `_run_via_gateway` 可用 | ✅ 已接 |
| 5 | 全量回归 | ✅ 本次 PR lane 分片跑过 |

**结论**：第 ⓿ 步的收益（−313 行死代码、−80 行自指测试）已经拿到，且零
风险。①–④ 步需要一个明确的产品决策：**是否接受失去 `QNODES_NEW_ENTRY=0`
回退能力**。在做出该决策前，门保持现状是诚实的——它标记的是「灰度开关」，
不再是「两条活跃路径」（旧路径的实际执行代码已经不存在了）。

### 下一步若要继续

1. 先评估 §3 第 1 项（`/send_async` 与 SessionService 的归属）；
2. 再决定 `QNODES_NEW_ENTRY` 是否退役（这决定 ①–④ 能否执行）；
3. 若退役：按原顺序 ① CLI → ② API → ③ 观察 → ④ 删门，各自独立提交。
