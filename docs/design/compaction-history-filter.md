# Compaction History Filter (700dc7f7 错误修复)

日期：2026-07-31
状态：实施中
相关会话：`700dc7f7-95de-45e0-b568-d713fe05065f`

> **Note (Phase A, 2026-07-31)**: This doc describes the L1/L3/L4
> 3-layer system. Phase A (commits A1-A5) simplified to L4-only.
> See `compaction-phase-a-simplification.md` for the opencode-aligned
> redesign that fixes the 700dc7f7 infinite loop root cause.

---

## 问题概述

会话 `700dc7f7` 在累积到 100 条消息后，用户发送"你好"等简单消息时，MiniMax API 返回 400 错误：
```
bad_request_error: invalid params, chat content is empty (2013)
```

### 错误链路

1. 会话累积 100 条消息（多次 L4 压缩）
2. 用户发"你好" → history 73 条（含 5 次旧 compaction）
3. Agent loop: `messages = [system] + history + [user("你好")]`
4. L4 触发：`_split_into_turns` 按 assistant 角色切分
5. 由于最新消息"你好"无 assistant 回复 → `len(turns) <= 2`
6. `tail_turns_list = []` → `recent = []`（空）
7. L4 把 74 条全部进 head，生成 3200 字符 summary
8. 返回 `new_messages = [system]`（**只剩 system 消息**）
9. 后续 LLM 调用发 `[system]` → MiniMax 400 `chat content is empty (2013)`

---

## 调研：Opencode 如何处理

通过阅读 `anomalyco/opencode` 源码（`packages/opencode/src/session/compaction.ts`），关键发现：

### 1. 只使用最近一次压缩

```typescript
const prior = completedCompactions(history)
const hidden = new Set(prior.flatMap((item) => [item.userIndex, item.assistantIndex]))
const previousSummary = prior.at(-1)?.summary  // 只取最后一个
const selected = yield* select({
  messages: history.filter((_, index) => !hidden.has(index)),
  cfg,
  model,
})
```

**Opencode 把所有旧的压缩消息从 history 中过滤掉，只保留最近一次 summary**。

### 2. 溢出时 Replay 机制

```typescript
if (input.overflow) {
  // 找到触发压缩前的那条用户消息
  for (let i = idx - 1; i >= 0; i--) {
    if (msg.info.role === "user" && !parts.some(p => p.type === "compaction")) {
      replay = { info: msg.info, parts: msg.parts }
      messages = input.messages.slice(0, i)
      break
    }
  }
  // 安全检查
  const hasContent = replay && messages.some(m => m.info.role === "user" && ...)
  if (!hasContent) { replay = undefined; messages = input.messages }
}
```

Opencode 在溢出时保存最后一条用户消息，压缩成功后**重新注入**为新 user 消息（replay）。

### 3. Synthetic Continue

```typescript
const text = "Continue if you have next steps, or stop and ask for clarification if you are unsure how to proceed."
yield* session.updatePart({...text, synthetic: true})
```

压缩成功后自动发合成 user 消息，保证 LLM 总是有可响应内容。

---

## 我们的现状 vs Opencode

| 维度 | Opencode | 我们 |
|------|----------|------|
| 压缩位置 | 用户消息的 part | 独立 `message_type='compaction'` 消息 |
| 旧压缩处理 | 全部隐藏，只用最近 | **全部进 history**（5 个旧 compaction 累积） |
| Replay 机制 | 溢出时保存最后 user 消息 | **无** |
| Synthetic Continue | 压缩后自动发"Continue..." | **无** |
| 错误处理 | `ContextOverflowError` → "stop" | LLMError → 错误消息气泡 |

---

## 实施方案

### 阶段 1：`_convert_messages_to_history` 只保留最近 compaction

**过滤位置**：构建 LLM 上下文时（`service.py`），不影响 DB

**关键设计**：
- ✅ **不修改前端**：所有 compaction 卡片仍显示（审计可见）
- ✅ **不物理删除 DB**：数据可恢复
- ✅ **可配置**：`CompactConfig.keep_all_compactions_in_history: bool = False`

**改动**：
1. `CompactConfig` 新增 `keep_all_compactions_in_history: bool = False`
2. `_convert_messages_to_history` 加 `keep_all_compactions: bool = False` 参数
3. 第一遍扫描定位所有 compaction 索引
4. 第二遍转换时只保留最后一个 compaction
5. 写 `[HIST] hiding N older compactions, keeping 1 most recent` 日志
6. 调用点（2 处）传参

### 阶段 2：压缩后消息太短时回退（Replay 防御）

**目标**：即使 L4 产生无效结果（new_messages 太短），也不会丢 user 消息

**改动**：
1. `_llm_summarize_v2` 末尾检查 `len(new_messages) < 2` 或无 user role → 返回 `None`
2. `compact_messages` 检测 `l4_result is None` → 不应用 L4 → 保留原 messages
3. 环境变量 `SR_KEEP_ALL_COMPACTIONS=1` 紧急回退

**效果**：
- L4 失败时，原始 messages（含 user 任务）保留
- 下游 L3 hard truncate 仍可触发
- 杜绝 `new_messages = [system]` 这种空 context

### 阶段 3：错误文案 + MiniMax 适配器

**目标**：400 错误被准确识别 + 友好提示

**改动**：
1. `_friendly_error_text` 新增：
   - MiniMax `2013` / `chat content is empty` → "会话内容已压缩为空，请新建会话"
   - `400` + `invalid params` → "请求参数无效，请稍后重试"
2. `MiniMaxAdapter.handle_error` 增强：
   - `400` + `2013` → `LLMConfigError`（避免 stream→achat 回退到又一次 400）
   - 保留现有 403/429 quota 映射

### 阶段 4：Kill Switch + 监控

**目标**：紧急回退 + 可观测性

**改动**：
1. Admin API: `POST /api/admin/compaction/keep-all/{true|false}`
   - 二次确认（query param `?confirm=yes`）
   - Audit log（写入 `audit.log`）
2. 内存监控：
   - 每会话 hidden 数（gauge）
   - 全局 hidden 总数（counter）
   - L4 失败次数（counter）
3. `[HIST]` 日志打印过滤统计

---

## 紧急回退机制

### 环境变量
```bash
SR_KEEP_ALL_COMPACTIONS=1 python3 -m quantnodes_strategy_research.serve
```

### Runtime API
```bash
# 启用旧行为
curl -X POST "http://localhost:8783/api/admin/compaction/keep-all/true?confirm=yes" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# 恢复新行为
curl -X POST "http://localhost:8783/api/admin/compaction/keep-all/false?confirm=yes" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### 配置文件
`~/.quantnodes/llm.json`:
```json
{
  "compact": {
    "keep_all_compactions_in_history": false
  }
}
```

---

## 改动量汇总

| # | 文件 | 类型 | 行数 |
|---|------|------|------|
| 1.1 | `core/agent/compact.py` | `CompactConfig` 字段 | +5 |
| 1.2 | `api/session/service.py` | `_convert_messages_to_history` 过滤 | +25 |
| 1.3 | `api/session/service.py` | 调用点传参（2 处） | +4 |
| 2.1 | `core/agent/compact.py` | `_llm_summarize_v2` 安全检查 | +10 |
| 2.2 | `core/agent/compact.py` | 环境变量读取 | +3 |
| 3.1 | `core/llm/provider/minimax.py` | `handle_error` 增强 | +6 |
| 3.2 | `api/session/service.py` | `_friendly_error_text` 增强 | +4 |
| 4.1 | `api/admin.py` | 新文件 | +30 |
| 4.2 | `api/session/service.py` | 监控埋点 | +8 |
| 5.1-5.6 | `tests/*` | 6 个新测试文件 | +390 |
| **总计** | **~12 个文件** | | **+485 行** |

---

## 提交计划

1. **Commit 1 (docs)**: 设计文档（本文件）
2. **Commit 2 (Phase 1)**: 核心过滤 + 测试
3. **Commit 3 (Phase 2)**: L4 防御 + env var + 测试
4. **Commit 4 (Phase 3)**: 错误文案 + MiniMax 适配器 + 测试
5. **Commit 5 (Phase 4)**: Kill Switch + 监控 + 集成/性能/兼容性测试
6. **Commit 6 (docs)**: 更新 `llm-retry-error-bubble-sse-fix.md`

---

## 700dc7f7 预期效果

| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| History 长度 | 73 条（含 5 个旧 compaction） | 69 条（只 1 个最新） |
| 旧 compaction 进 LLM | 5 个 | 0 个（仅 DB 存） |
| L4 后 messages 长度 | 1（仅 system）| ≥ 2（含 user） |
| 400 bad_request 概率 | 高 | 极低（多道防御） |
| 用户看到 | 错误气泡"请求失败" | 正常对话 |

---

## 风险与回退

| 风险 | 等级 | 回退 |
|------|------|------|
| LLM context 变小影响回复质量 | 中 | `SR_KEEP_ALL_COMPACTIONS=1` |
| 阶段 2 防御过度 | 低 | 监控 L4 成功率 + 日志告警 |
| Kill switch 误触发 | 极低 | 二次确认 + audit log |
| 旧会话不兼容 | 极低 | 兼容性测试覆盖 |
| 前端显示异常 | 极低 | 不修改前端，DB 不动 |
