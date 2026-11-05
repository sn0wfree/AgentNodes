# 通用工具使用（详细方法）

> 工具使用的基础约定。chat.md 内联了精简版（5 条），本文件是详细方法说明，含每种场景的具体操作。

## 算子语法 vs 工具参数：两种不同的"不确定"

| 项 | 算子语法 | 工具参数 |
|---|---|---|
| 是什么 | 因子表达式里的函数，如 `ts_return(close, 20)`、`rank(...)` | 工具调用的参数，如 `list_files(path=...)`、`run_backtest(strategy_name=...)` |
| 出错后果 | Python 解析失败 / 静默错误结果 | 工具调用报错或返回错误 |
| 验证方法 | **读文档**（`.skills/*.md`） | **实际探查**（`list_files` / `read_file` 跑一下看返回） |
| 不确定性来源 | 算子是否支持、参数顺序、参数类型 | 参数名、参数取值范围、目录是否存在 |

## 核心约定

### 1. 先 `list_files` 后 `read_file`

**规则**：用 `list_files` 确认目录/文件结构，再 `read_file` 读取具体文件。

```python
# ✅ 正确流程
list_files(workspace=workspace, path="strategies")       # 先列目录
list_files(workspace=workspace, path="strategies/momentum_v1")  # 列子目录
read_file(workspace=workspace, path="strategies/momentum_v1/strategy.py")  # 再读文件

# ❌ 错误：直接 read_file 一个路径（可能是目录）
read_file(workspace=workspace, path="strategies")  # 报错 "not a regular file"
```

### 2. 不要用 `read_file` 读取目录

`read_file` 只能读**文件**，不能读目录。读目录必须用 `list_files`。

### 3. 写用 `write_file`，读用 `read_file`

| 操作 | 工具 |
|---|---|
| 创建新文件 | `write_file` |
| 修改现有文件 | `write_file`（覆盖写） |
| 读取文件内容 | `read_file` |
| 列出目录内容 | `list_files` |

### 4. 不猜测算子语法

**算子** 是因子表达式里的函数，如 `ts_return(close, 20)`。算子语法**以文档为准**：

```python
# ✅ 正确：先 read_file 读文档
read_file(workspace=workspace, path="templates/.skills/factor-research.md")
# 然后才用算子
factor_code = "ts_return(close, 20)"  # 从文档确认支持

# ❌ 错误：直接编一个算子
factor_code = "momentum_20d(close)"  # 这个算子可能不存在
```

**算子语法验证清单**：

- [ ] 算子名称是否在文档中？
- [ ] 参数顺序是否正确？
- [ ] 参数类型是否正确（Series vs scalar）？
- [ ] 返回值类型是什么？

### 5. 不猜测工具参数

**工具参数**是工具调用时传入的值。**先实际探查**：

```python
# ✅ 正确：先 list_files 看可用路径
result = list_files(workspace=workspace, path="strategies")
# 看到返回的目录列表后再决定
read_file(workspace=workspace, path="strategies/<真实路径>/strategy.py")

# ❌ 错误：猜测路径
read_file(workspace=workspace, path="strategies/my_strategy/strategy.py")
# 路径可能不存在
```

**工具参数探查清单**：

- [ ] 路径是否存在？
- [ ] 参数名拼写正确？
- [ ] 参数类型匹配？
- [ ] 必填参数都填了？

## 错误处理：调查，不要直接转给用户

工具报错时：

1. **分析根因**：报错信息说的是什么？缺依赖？路径错？参数错？
2. **尝试替代方案**：
   - 路径不存在 → `list_files` 看实际结构
   - 算子不支持 → `read_file` 查文档找替代算子
   - 参数错 → `read_file` 读示例
3. **不要直接把错误转给用户**——除非确实无法解决

## 何时读本文件

- 准备调用一个不熟悉的 tool
- 工具返回错误，需要排查
- 不确定算子是否支持
- 用户提供了新工具但你不了解用法

## 何时**不**读本文件

- chat 模式 + chat.md 精简版已够用
- 你在多次对话中使用过同一工具
- 问题与工具使用无关