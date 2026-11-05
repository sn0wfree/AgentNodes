# Rule Index（何时读哪个 rule）

> 本目录存放**低频规则的详细版**，不自动注入上下文。遇到不熟悉的方法/工具/场景时，通过 `read_file(workspace="{workspace}", path="_common/rules/<rule-name>.md")` 读取对应文件。
>
> **chat 模式常用规则已内联到 `chat.md`**，无需读本目录。
>
> 2026-11-05 批次 7：研究域规则（`backtest.md`）随 templates 去研究化归档到
> `testcase/golden/templates/prompts/_common/rules/`。本目录只保留通用规则。

## 触发条件速查表

| 触发情况 | 读哪个 rule |
|---|---|
| 准备调用不熟悉的 tool | [`tools.md`](./tools.md) |
| 处理 role agent 的 JSON 输出细节 | [`json-output.md`](./json-output.md) |
| 修复 / 优化迭代 | [`iteration.md`](./iteration.md) |
| 长命令可能超时、需后台化 | [`long-task.md`](./long-task.md) |
| 不知道该读哪个 | 先读本文件 |

## 各 rule 内容概览

### `tools.md` — 通用工具使用（详细方法）

算子语法 vs 工具参数的区别、读目录的工具、文件操作规范、参数探查策略。

### `json-output.md` — JSON 输出约定（详细版）

role agent 的 schema 约束、缺失字段处理、嵌套结构示例。

### `iteration.md` — 小步迭代原则 + 自检清单

修复/优化的"每次只改一处"原则、3 轮上限机制、执行前自检清单（含示例）。

### `long-task.md` — 长任务后台化

超时边界、后台命令模式、结果回收。

## 读取方式

```python
# Python 示例（伪代码）
rule_content = read_file(
    workspace=workspace,
    path="_common/rules/tools.md"
)
```

读取后只需关注与你当前任务相关的部分，不必通读整个文件。

## 何时**不**读本目录

- 你已经明确知道方法 → 直接执行
- 你能在内联规则（chat.md / role 文件）中找到答案 → 用内联版
- 当前模式是 chat，且问题在 chat.md 高频规则覆盖范围内 → 不用读
