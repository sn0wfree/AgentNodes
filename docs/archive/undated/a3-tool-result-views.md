# A3 — 工具结果 Schema / Views 设计

> 2026-09-29 · Agent Runtime 工具面  
> 状态: Design → Implementation

---

## 1. 问题

| 现状 | 后果 |
|---|---|
| 工具返回裸 JSON 字符串 | LLM 难读大表/长文 |
| history 硬截断 head+tail 30k | 表格中间被掐，行列错位 |
| compact 序列化再截 2k | 双重截断，信息不一致 |
| 无「视图」概念 | UI（右侧面板）与 LLM 争同一串 |

---

## 2. 概念

```text
ToolResult
├── status: ok | error
├── data:     结构化原始数据（机器用 / UI）
├── views.text:   给 LLM 的紧凑文本（表摘要、要点）
├── views.table:  可选 Markdown 表片段
└── meta:  tool, elapsed, truncated, row_count…
```

**原则**
1. `data` 完整保留（截断发生在 **渲染层**，不丢结构化源）
2. LLM 只看 `views`（预算内最优表达）
3. 渲染规则统一：`render_for_llm(result, max_chars)`

---

## 3. 渲染优先级

```text
error → views.text / error
表格 → 表头 + 前 N 行 + 「…共 M 行」+ 关键统计
长文 → 首段 + 要点行 + 尾段 + [truncated K chars]
JSON → 摘要 key + 数值预览
```

超过 `max_chars` 时 **保留表头/统计/首尾**，而不是从中切开。

---

## 4. 接口

```python
# core/agent/tool_result.py
@dataclass
class ToolResult: ...
def ok(data=None, text="", table=None, **meta) -> ToolResult
def error(message, **meta) -> ToolResult
def table_result(rows, columns=None, title=None, **meta) -> ToolResult
def from_any(value) -> ToolResult          # 兼容 str / dict / ToolResult
def render_for_llm(result, max_chars=30000) -> str
def to_wire(result) -> dict                # SSE / DB 完整形态
```

`BaseTool.invoke` → `from_any(execute(...))` → `to_wire` JSON。

---

## 5. 接线

| 位置 | 改动 |
|---|---|
| `tools.BaseTool.invoke` | 统一包 `from_any` + `to_wire` |
| `loop._execute_tool_call_core` | `render_for_llm` 替代裸 head/tail |
| `compact._serialize_tool_result` | 读 `views.text`，降级旧 JSON |

兼容：旧工具返回 `str` JSON → `from_any` 解析失败则包成 `text`。

---

## 6. 测试

1. `from_any` 三种输入
2. `table_result` 生成表头/行/汇总
3. `render_for_llm` 长表不切断表头
4. `render_for_llm` 长文保留首尾 + 计数
5. error 渲染
6. `to_wire` 含 data + views

---

## 7. 非目标

- 所有 18 工具改返回 ToolResult（先兼容层，逐步迁移）
- 二进制/图表 base64
- 流式工具输出
