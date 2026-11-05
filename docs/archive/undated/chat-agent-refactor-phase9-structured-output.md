# Phase 9: StructuredOutputParser — 四层降级

## 1. 目标

将 `parser.py:parse_tool_arguments` 的 3 层 JSON 解析升级为 4 层降级，确保 LLM 输出的 tool_call arguments 永远能被解析（最差返回 None + 错误列表），永不抛 ParseError。

## 2. 设计

### 2.1 四层降级

```
Layer 1: 严格 JSON 解析
   ↓ (失败)
Layer 2: 修复（trailing comma、single quote → double quote）
   ↓ (失败)
Layer 3: 正则按 schema 字段提取
   ↓ (失败)
Layer 4: 返回 None + 错误列表（永不抛 ParseError）
```

### 2.2 修复规则（Layer 2）

| 规则 | 模式 | 修复 |
|---|---|---|
| 尾随逗号 | `,]` 或 `,}` | 移除 |
| 单引号键名 | `'key':` | `"key":`（小心撇号） |
| 单引号字符串 | `'value'` | `"value"` |

### 2.3 正则提取（Layer 3）

当 JSON 修复失败时，按 schema 字段名用正则提取值：

```python
# 对每个 schema 字段，尝试提取
for field_name, field_type in schema.items():
    if field_type == "string":
        # 提取 "field_name": "..." 或 'field_name': '...'
    elif field_type == "number":
        # 提取 "field_name": 123 或 'field_name': 123.45
    elif field_type == "boolean":
        # 提取 "field_name": true/false
```

### 2.4 行为

- **可恢复**（Layer 1-3 成功）：返回 parsed dict + source 标注（"strict"/"repaired"/"regex"）
- **不可恢复**（Layer 4）：返回 None + 错误列表，调用方转 tool message 回 history

### 2.5 集成点

`parser.py:parse_tool_arguments()` 改为调用 `StructuredOutputParser.parse()`

## 3. 接口

```python
@dataclass
class ParseResult:
    """Structured output parse result."""
    data: dict[str, Any] | None  # None if all layers failed
    errors: list[str]            # empty if successful
    source: str                  # "strict" | "repaired" | "regex" | "failed"

class StructuredOutputParser:
    """4-layer degradation JSON parser for tool_call arguments."""

    def parse(self, raw_args: str, schema: dict[str, str] | None = None) -> ParseResult:
        """Parse raw_args with 4-layer degradation.

        Args:
            raw_args: Raw JSON string from LLM tool_call
            schema: Optional field schema for Layer 3 regex extraction
                    e.g. {"name": "string", "count": "number"}

        Returns:
            ParseResult with data/errors/source
        """
        ...
```

## 4. 测试计划

| 测试 | 验证 |
|---|---|
| 严格 JSON | Layer 1 成功，source="strict" |
| Trailing comma | Layer 2 修复，source="repaired" |
| Single quotes | Layer 2 修复，source="repaired" |
| Markdown fence | Layer 1 提取后成功 |
| Regex 提取 | Layer 3 成功，source="regex" |
| 全部失败 | Layer 4，data=None，errors 非空 |
| 空输入 | Layer 4，data=None |
| 非字符串输入 | Layer 4，data=None |
