# P2-2 设计：C901 收尾（最后 3 个）

> 现状：ruff src = 3 errors，全为 C901。范围外工作，本批次清零。
> 分类：2 个真拆分 + 1 个 lint 范围决策（一次性脚本不修）。

## 1. 三者评估

| 函数 | 行数 | C901 | 性质 | 处置 |
|---|---|---|---|---|
| `agent/event_store.py::emit` | 103 | 16 | 生产元件层（session 事件发射） | **拆分** → ≤10 |
| `agent/prompt_schema_extractor.py::extract_agent_schema` | 109 | 16 | 生产（schema 提取） | **拆分** → ≤10 |
| `scripts/migrate_study_history.py::migrate_study_history` | 170 | 20 | **一次性迁移脚本**（rg 全库零外部引用） | **per-file-ignore** |

migrate 的理由：`scripts/` 迁移工具跑完即存档，170 行单函数的可读性对一次性工具不构成维护负担；花半天降它复杂度的回报为零。`pyproject.toml` 的 per-file-ignore 对 `scripts/migrate_study_history.py` 加 C901（与 `_archived` 同政策：存档类不修 lint）。

## 2. 拆分方案（沿用 Step 2/3 已四次验证的模式）

**`event_store.emit`（16 → ≤10）**：读码后按「校验/解析 → 落盘 → 通知」切 2-3 个私有助手，签名不改。

**`extract_agent_schema`（16 → ≤10）**：按提取流水线的自然段切（字段收集 / 约束解析 / 组装）。

两者的具体切点在读码后定（同 Phase_engine 惯例：先审计分支构成，再按块抽；改动锚定 def/装饰器边界——块操作含装饰器的教训已固化 4 次）。

## 3. 验收

| # | 项 | 条件 |
|---|---|---|
| 1 | C901 全库 | **0**（migrate 进 ignore；两个拆完 ≤10） |
| 2 | ruff src | **0 errors** |
| 3 | 定向 | `test_event_*` · `test_prompt_*` 相关全绿 |
| 4 | 69 回归 | 1162+ / 0 failed |

## 4. 边界

- 不动 `scripts/` 其他文件、`_archived`
- emit 的对外行为（事件形态/落盘顺序/异常语义）零变化——被 session/study 测试覆盖
