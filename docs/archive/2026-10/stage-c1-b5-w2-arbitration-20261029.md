# B5 W2 实施：ContextPlan 仲裁 + 预算取数修正 — 2026-10-29

> 上游：`docs/archive/2026-10/stage-c1-b5-contexthub-w1w2-design-20261028.md` §3-§4
> 前置：`docs/archive/2026-10/stage-c1-b5-wiring-20261029.md`（R-1/B-a/B-c/W1-p，已交付 `1a9f2c49`）

---

## 0. 范围

| ID | 内容 | 文件 |
|---|---|---|
| **W2-a** | `ContextPolicy`（kind 秩 / 保护类 / provider 上限）+ `ContextHub.build(budget_tokens=, policy=)` 仲裁 | `core/context/policy.py`（新）、`core/context/hub.py` |
| **W2-b** | 默认不启用（`budget_tokens=None` → 零仲裁，W0 行为不变） | 同上 |
| **R-3** | 预算取数改 `client.config.model_context_tokens`（现存恒 None → 压缩阈值恒 8k） | `gateway/session_controller.py` |
| **R-2** | `_inject_context` 传 `budget_tokens` / `policy`；over_budget 发事件 | 同上 |
| **P3** | `_active_scopes` scope 隔离（可选，零行为变化的加固） | `core/context/hub.py` |

---

## 1. 仲裁规则（设计 §3.1-3.3 落地）

### 1.1 两级排序

跨 kind 顺序由 **kind 秩表**决定（调用方传入，hub 不硬编码）；
同 kind 内由 `priority` 升序（低重要度先丢）。

默认秩由调用方（`SessionController`）提供，**必须与现有渲染序一致**
（memory 先于 goal），否则一上线就改字节：

```python
DEFAULT_KIND_RANK = {"memory": 0, "goal": 1, "todo": 2, "hypothesis": 3,
                     "knowledge": 4, "artifact": 5, "context": 6,
                     "tool_result": 8, "history": 9}
```

### 1.2 裁剪序（确定性全序）

```
candidates = [i for i in items if i.kind not in policy.protected_kinds]
sort key = (kind_rank DESC, priority asc, tokens desc, provider, source)
```

> **实施修正（设计稿写反了）**：设计 §3.3 原文写「低秩先丢：tool_result(8)
> → history(9) 先于 memory/goal」，但秩表把 memory 定为 0（最低）。按字面
> 实现会把最稳定的 memory 最先丢掉——与「稳定段在上」正好相反。
> 实测抓到了（`test_trim_drops_low_rank_first` 一开始失败）。
> **正确语义：秩 = 稳定性，高秩先丢**（tool_result/history 是易变/低价值，
> memory/goal 是durable 块）。排序键用 `-kind_rank`。

- 丢弃至只剩保护类仍超预算 → 保留 + `accounting["over_budget"]=True` +
  `dropped` 记 `reason="budget_exhausted_protected"`。
  这是对讨论稿「total_tokens ≤ budget 恒成立」的**如实放宽**（保护类与
  恒等式互斥）。
- 存活项**相对顺序不变**（稳定排序）→ 渲染序对存活者零变化。

### 1.2b 无 policy 时的默认

`build(budget_tokens=N)` 不传 policy → 用 `ContextPolicy()` 默认值：
保护 `goal`、默认秩表、无 provider cap。
理由：设计 §1.2 明确「protected_kinds 默认 ("goal",)」，且「目标不可丢」
是业务底线而非可选。调用方要关掉保护须显式传空集。

### 1.3 provider 上限（谁产出谁拥有）

`policy.provider_caps: dict[str, int]`，默认 `{}`。
超 cap 的 provider 的 items 先被裁（同上述键），账本记 `over_cap`。
**不扩协议**——归属落在账本而非状态。

### 1.4 明确不做

| 项 | 理由 |
|---|---|
| 改 `PreRunContextProvider` 的 priority（order→rank 归一化） | 会改渲染字节 → 破 W0 等价测试；留 W3（设计 §3.1 已裁定） |
| `render.py` / `join_items` | 设计 §3.4 判为 P2 可选：3 行重复的收益是边际的 |
| hub 渲染 / hub 持有 sink | W0 三条硬约束 |
| 前端可见 `context.over_budget` | 需同步改事件桥白名单，归契约 §2.3 缺口 E1 的同一处置 |
| 压缩本身 | 仍在 runtime（`ReactConfig.compactor`），hub 只发信号 |

---

## 2. R-3：预算取数（修现存缺陷）

```python
# 现状（恒 None —— OpenAICompatClient 无该属性）
model_context_tokens=getattr(self._client, "model_context_tokens", None),
# 修正
cfg = getattr(self._client, "config", None)
ctx_tokens = getattr(cfg, "model_context_tokens", None)
```

影响链（设计 §4.2 实测）：`None` → `make_compactor` 的
`_resolve_threshold_tokens` 落 `fallback_threshold_tokens=8_000`
→ **chat 的实际压缩阈值是 8k，而非模型窗口的 80%**。
修好之后 chat 与 AgentLoop（`loop_compaction.py:58` 读 `LLMConfig`
正确）的压缩口径一致。

`model_max_output_tokens` 同源修正。

---

## 3. R-2：接线

`_inject_context` 增加：
- `budget_tokens=self._context_budget_tokens()`（默认 = 模型窗口的 80%，
  与压缩阈值同源；窗口未知 → None → 不仲裁）
- `policy=self._context_policy`（构造参数，默认 None → 用 hub 默认？不，
  默认 None = 仲裁时用空 policy：不保护、无 cap、秩表全 0 → 等价「按
  provider/source 决定」，即**不启用仲裁**的更安全路径）

精度说明：`budget_tokens` 与 `compaction threshold` 必须同源，否则
「先裁后压」会互相打架。两者都取 `config.model_context_tokens`。

---

## 4. 改动面

| 文件 | 改动 |
|---|---|
| `core/context/policy.py`（新） | `ContextPolicy` dataclass（kind_rank / protected_kinds / provider_caps） |
| `core/context/hub.py` | `build(..., budget_tokens=, policy=)`；`_arbitrate()`；`_active_scopes()` |
| `gateway/session_controller.py` | R-3 取数修正；R-2 传参 + over_budget 事件 |
| `tests/test_context_arbitration.py`（新） | 仲裁全路径 |

---

## 5. 测试

| # | 断言 |
|---|---|
| T1 | `budget_tokens=None` → 零仲裁，items 与 W0 逐条相同（兼容） |
| T2 | 超预算 → 按 (秩, priority, tokens desc) 裁剪，存活序不变 |
| T3 | 保护类（goal）单独超预算 → 保留 + `over_budget=True` + dropped 记录 |
| T4 | provider_caps 生效：超 cap 的 provider 先被裁，账本记 `over_cap` |
| T5 | 确定性：同输入两次 build 产同结果（不变式 3） |
| T6 | R-3：`client.config.model_context_tokens` 被读到（假 client 验证） |
| T7 | R-2：over_budget 时发出 `context.over_budget` 事件 |
| T8 | scope 隔离：study-only provider 在 session 查询下被跳过并记 dropped |
| T9 | 现有 29 条 hub 测试零改动通过（W0 兼容） |

回归门：`test_context_hub + test_context_providers_bridge + test_b5_wiring
+ test_b1_chat_react_wiring + test_session_controller + test_b1c_react_compaction`
→ `c1_gate.py`。

---

## 6. less-is-more 调查表

| 需求 | 查过 | 结论 |
|---|---|---|
| 排序 | `hub.build` 现有 collect 循环 | 扩展，不新建收集器 |
| 账本 | `bundle.accounting`（W0 已有 per_provider） | 扩展字段 |
| 降级记录 | `bundle.dropped`（W0 已有） | 复用同一形态 |
| 预算数值 | `LLMConfig.model_context_tokens`（config.py:164） | 唯一来源 |
| 压缩阈值 | `compaction_runtime.make_compactor` | 同源，不新造 |
| tokern 估算 | `hub._content_tokens`（hub.py:23） | 复用 |
