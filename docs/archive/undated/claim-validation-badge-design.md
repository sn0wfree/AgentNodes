# Claim Validation + Verifiability Badge Design

> **背景**: 承接 `truthfulness-common-rules-design.md`（L1 prompt 抽象）。本文档设计 L2 (claim_validator) 与 L3 (verifiability badge + StrategyFileSection) 的实施。
> **目标**: 从"提示词约束"升级到"结构性校验"——让用户能直观看到模型声称的数字是否有工具返回值支撑。
> **范围**: L2 验证最终答案 + L3 前端徽章 + ContextPanel 策略文件展示。

---

## 一、问题

### 1.1 翻车案例（L1 已解决的部分）

- L1 通过 `_common/principles.md` + `chat.md` 内联规则 + `rules/` 按需读取，在**提示词层**约束模型不编造。

### 1.2 剩余风险

**纯 prompt 是软约束**。即使模型被提示"没调用工具就没有数据"，压力下仍可能编造。需要**结构性校验**兜底：

- 模型声称的数字是否能在本轮/历史 tool 返回值中找到？
- 找不到 → 必须让用户看到"未验证"标记（而不是默默相信）。

### 1.3 设计原则

1. **默认关闭**：`enable_claim_validation` / `strict_claim_validation` 均默认 `False`，灰度安全。
2. **不阻断**：validator 只打 metadata + 可选软追加警告，不改写模型原意。
3. **纯函数**：validator 无 IO，便于单测。
4. **可验证性**：最终答案里的每个"指标数字"都应能追溯到 tool 结果。

---

## 二、架构

```
[后端] AgentLoop.run/arun
   │  ── _finalize_metrics() 调用 validate_claims()
   │      输入: result.answer + messages 里 role=="tool" 的 content
   │      输出: ClaimValidationResult → result.metrics["claim_validation"]
   ▼
[API] service.py / chat.py 持久化 assistant message
   │  └─ metadata["claim_validation"] = metrics["claim_validation"]
   ▼
[前端] SSE 事件 / list_messages 携带 metadata
   ▼
AssistantMessage.tsx 读 metadata.claim_validation → 🟢/🟡/🔴 徽章
ContextPanel.StrategyFileSection → 展示实际策略文件（可验证性兜底）
```

---

## 三、L2 — claim_validator（后端）

### 3.1 新文件 `core/agent/validators/claim_validator.py`

```python
@dataclass(frozen=True)
class ClaimValidationResult:
    ok: bool                 # unverified 为空 或 total_claims == 0
    total_claims: int        # 检测到的"指标数字"总数
    verified: list[str]      # e.g. ["sharpe=1.42"]（tool 结果中有此数）
    unverified: list[str]    # e.g. ["sharpe=1.5"]（tool 结果中无此数）
    confidence: float        # verified / total_claims（无 claim 时为 1.0）
    detail: str              # 人类可读摘要（前端 tooltip 用）

def validate_claims(
    assistant_text: str,
    tool_result_texts: list[str],
    metric_keywords: list[str] | None = None,
    tolerance: float = 1e-3,
) -> ClaimValidationResult
```

### 3.2 检测逻辑

1. **定位指标数字**：在 `assistant_text` 中找 metric 关键词，在关键词 ±30 字符窗口内提取数字 → `(keyword, value)` 对。
   - metric 关键词：`sharpe`、`ic`、`年化`、`收益`、`回撤`、`drawdown`、`calmar`、`夏普`、`胜率`、`alpha`、`beta`、`换手`、`波动`、`maxdd`、`return`、`pnl`、`净值`、`volatility` 等。
   - 非关键词附近数字（日期、计数、run_id、序号）被排除 → 降低误报。
2. **交叉验证**：从 `tool_result_texts` 提取所有浮点数（正则 + 容差比较）。每个 claim 数字在 tool 结果中存在 → verified，否则 unverified。
3. **计算**：`confidence = verified / total_claims`；`ok = (unverified 为空) or (total_claims == 0)`。
4. **detail**：如 `"3 个数字未在工具返回值中找到: sharpe=1.5, 年化=18%"`。

### 3.3 行为边界

| 场景 | 结果 |
|---|---|
| 无指标数字（`total_claims == 0`） | `ok=True`，前端不显示徽章 |
| 全部可追溯 | `ok=True, confidence=1.0`，🟢（或不显示） |
| 部分未验证 | `ok=False`，🟡（confidence >= 0.5） |
| 大部分未验证 | `ok=False`，🔴（confidence < 0.5） |

### 3.4 `loop.py` 注入

- `AgentLoop.__init__` 新增：
  ```python
  enable_claim_validation: bool = False,
  strict_claim_validation: bool = False,
  ```
- `_finalize_metrics`（loop.py:775）末尾：
  ```python
  if self._enable_claim_validation:
      tool_texts = [m.get("content","") for m in messages if m.get("role") == "tool"]
      cv = validate_claims(result.answer or "", tool_texts)
      result.metrics["claim_validation"] = cv.__dict__
      if self._strict_claim_validation and not cv.ok:
          result.answer += ("\n\n> ⚠️ 数据真实性警告：以下数字未在工具返回值中找到，"
                            f"可能为模型推测：{', '.join(cv.unverified)}")
  ```

  `messages` 已含 history + 本轮全部 tool 结果 → **跨轮验证天然成立**。

### 3.5 配置接线

- `chat_loop.py` `build_chat_agent_loop`：加 `enable_claim_validation: bool = False`、`strict_claim_validation: bool = False` passthrough。
- `role_factory.py` `build_agent_loop`：同上。

---

## 四、L2 metadata 流向前端

### 4.1 新路径（`api/session/service.py`）

`assistant_message` 事件的 `metadata`（service.py:630-634）加：

```python
"claim_validation": (result_dict.get("metrics") or {}).get("claim_validation"),
```

### 4.2 旧路径（`api/routers/chat.py`）

- `_persist_assistant_message` 增加可选参数 `claim_validation`，并入 `metadata`。
- `chat.py:350` 从 `result.metrics` 传入。

`persist_message` 已支持 `metadata` → `metadata_json`，无需改 DB schema。

---

## 五、L3 — Verifiability badge（前端）

### 5.1 `stores/chat.ts`：metadata 类型扩展

```ts
claim_validation?: {
  ok: boolean
  total_claims: number
  verified: string[]
  unverified: string[]
  confidence: number
  detail: string
}
```

### 5.2 `AssistantMessage.tsx`：`VerifiabilityBadge`

在 `headerLine` 的 `modelLabel` 后渲染：

```tsx
const cv = message.metadata?.claim_validation
// 无 cv 或 cv.ok 或 total_claims===0 → 不渲染
// confidence >= 0.5 → 🟡
// confidence < 0.5  → 🔴
```

- 黄/红圆点 + hover tooltip `cv.detail`
- 样式沿用现有 `text-xs` badge 惯例

---

## 六、L3 — StrategyFileSection（ContextPanel）

### 6.1 `contextExtractors.ts` 新提取器

```ts
export interface StrategyFile {
  path: string
  status: 'modified' | 'created'
  old_content: string
  new_content: string
  timestamp: number
}
export function extractStrategyFiles(messages): StrategyFile[]
```

- 过滤 `file_edit` part 且 `file_path` 含 `strategies/`
- 同路径保留最新 `new_content`；`status` 由 `old_content === '' ? 'created' : 'modified'` 推断
- 补齐 `contextExtractors.ts` 里已有的 file_edit status TODO

### 6.2 新组件 `components/context/StrategyFileSection.tsx`

- 列出策略文件（路径 + 状态徽标）
- 点击展开显示 `new_content`（当前实际内容）
- 与 `old_content` 做简单 diff 高亮
- 只读展示

### 6.3 `ContextPanel.tsx` 集成

- 回测结果段前插入 `<StrategyFileSection messageList={messageList} />`
- `isEmpty` 条件加入策略文件非空判断

---

## 七、测试设计

### 7.1 `tests/test_claim_validator.py`（单元）

| 测试 | 断言 |
|---|---|
| `test_flags_unverified_sharpe` | 无 tool 结果 → ok=False, unverified=["sharpe=1.5"] |
| `test_passes_metric_in_tool_result` | tool 有 1.42，答案"sharpe 1.42" → verified |
| `test_ignores_dates_and_counts` | "3 个因子 / 2020-01-01" → total_claims=0 |
| `test_tolerance_comparison` | tool 有 1.420，答案 1.42 → verified |
| `test_chinese_metrics` | "年化收益 15%" 无 tool 结果 → 被标记 |
| `test_strict_mode_appends_warning` | strict → answer 追加警告后缀 |
| `test_empty_claims_ok` | 无指标数字 → ok=True, confidence=1.0 |

### 7.2 `tests/test_agent_loop_claim_validation.py`（集成）

| 测试 | 断言 |
|---|---|
| `test_enabled_writes_metrics` | enable=True → metrics["claim_validation"] 存在 |
| `test_disabled_default` | 默认 False → 无 claim_validation 键 |
| `test_strict_rewrites_answer` | strict → answer 含警告 |
| `test_history_tool_results_count` | 历史 tool 结果参与验证 |

### 7.3 前端 vitest

- `contextExtractors.extractStrategyFiles` 测试（含 status 推断）
- 视现有惯例加 badge 渲染测试

---

## 八、改动文件清单

**后端（新增 2 + 修改 5）**：
- `core/agent/validators/__init__.py`（新）
- `core/agent/validators/claim_validator.py`（新）
- `core/agent/loop.py`、`core/agent/chat_loop.py`、`core/agent/role_factory.py`
- `api/routers/chat.py`、`api/session/service.py`

**前端（新增 1 + 修改 4）**：
- `components/context/StrategyFileSection.tsx`（新）
- `stores/chat.ts`、`components/chat/AssistantMessage.tsx`
- `components/context/ContextPanel.tsx`、`utils/contextExtractors.ts`

**测试（新增 2 后端 + 1 前端）**

---

## 九、灰度顺序

| 步 | 内容 | 验证 |
|---|---|---|
| **G1** | validator + loop 注入 + 配置接线（默认全关） | 后端单测 + 集成测试 |
| **G2** | metadata 接线（service.py / chat.py） | 后端回归 |
| **G3** | badge（chat.ts 类型 + AssistantMessage） | tsc + vitest + build |
| **G4** | StrategyFileSection（extractor + 组件 + ContextPanel） | 前端 build + 手工 |

每步独立可回滚。

---

## 十、风险与缓解

| 风险 | 缓解 |
|---|---|
| 误报：模型引用论文数字被标记 | metric 关键词窗口限制 + 容差；strict 默认关；徽章只提示不阻断 |
| 漏报：编造但数字恰好与 tool 结果吻合 | 结构性校验非银弹，与 L1 prompt + 后续 fine-tune 协同 |
| metadata 体积增长 | claim_validation 结构小（<200 字节），仅 assistant 消息带 |
| 前端渲染回归 | badge 为独立组件 + 单元测试 |
