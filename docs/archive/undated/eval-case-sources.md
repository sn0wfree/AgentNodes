# 测试用例来源调研

> 2026-09-29 · 对象：Agent / 量化研究 eval 用例供给  
> 已核实：SWE-bench、AgentBench 仓库页

---

## 1. 原则

| 来源类型 | 用法 | 注意 |
|---|---|---|
| 公开 benchmark | **改写**为 golden prompt + 期望工具/禁止项 | 勿整包拷贝 license 敏感数据 |
| 论文附录 / 失败模式 | 提炼 checklist → 多项选择/判断题 | 适合 `must_contain` |
| 自建工作区问题 | 真业务回归，优先补 | 与 `evals/golden_tasks.jsonl` 同构 |
| 合成对抗 | injection / budget / 工具误用 | 已有 15 题硬题 |

---

## 2. 可获取渠道（按适配度）

### A. Agent / 工具使用（直接可改写）

| 源 | 获取 | 适配我们的点 |
|---|---|---|
| **SWE-bench** `github.com/SWE-bench/SWE-bench` | HF: `princeton-nlp/SWE-bench`（Verified 500） | issue→patch 任务；可提炼「多步工具+约束」 |
| **SWE-bench Verified** | OpenAI 报告 + HF 同上 | 人工确认可解，难度干净 |
| **AgentBench** `github.com/THUDM/AgentBench` | `data/` + Docker 任务 | OS/DB/KG/WebShop 多轮工具失败模式 |
| **AgentBench FC** | AgentRL 集成版 | function-calling 精度 |
| **WebArena / VisualAgentBench** | GitHub | 浏览器/多模态边界（可参考约束设计） |
| **tau-bench / ToolBench / API-Bank** | 论文+HF | 客服/API 调用序列 |
| **GAIA** | HF | 真实助理多步题 |

### B. 安全 / 注入 / 对齐（golden `safety` 切片）

| 源 | 用法 |
|---|---|
| **Prompt injection 集**（如 `llm-attacks` / Garak probes / PyRIT） | `injection-*` 题 |
| **HarmBench / AdvBench / TrustLLM** | 拒绝策略与泄密 |
| **Anthropic / OpenAI 安全评估公开说明** | check 项文案 |

### C. 量化 / 金融（域内真实陷阱）

| 源 | 用法 |
|---|---|
| **WorldQuant Alpha101 / 国泰君安 191 / Qlib158** | 已内置 alpha_zoo；可抽「实现对错」题 |
| **FinBen / FinEval / CFLUE** | 中文金融 QA |
| **Qlib / Backtrader / Zipline 文档陷阱** | look-ahead、幸存者偏差、成本 |
| **MSCI / 海通 / 开源证券因子研报** | 复现 checklist（项目内已有研报笔记） |
| **Kaggle 金融竞赛讨论帖** | 失败案例 |

### D. 代码/数据质量

| 源 | 用法 |
|---|---|
| **HumanEval / MBPP / LiveCodeBench** | 语法错误、部分成功 |
| **data-quality 反模式集**（Great Expectations 文档） | `data_quality` 题 |

---

## 3. 如何落到我们格式

```json
{
  "id": "from-<source>-<n>",
  "prompt": "<改写后任务>",
  "expected_tools": [...],
  "forbidden_tools": [...],
  "toolset": "factor_analyst",
  "must_contain": ["IC|信息系数"],
  "tags": ["source:swebench", "reason"],
  "budget_max_tokens": 2000
}
```

**改写模板**
1. 保留「约束 + 陷阱」结构  
2. 换成我们的工具名（`compute_factor` / `run_backtest`…）  
3. 加 `must_contain` / `forbidden_tools`  
4. 标 `tags: ["source:xxx"]` 便于溯源

---

## 4. 推荐采集优先级

| 优先 | 动作 | 预期增题 |
|---|---|---|
| 1 | SWE-bench Verified 抽 10 条「工具选择+失败恢复」 | +10 |
| 2 | FinBen/Qlib 陷阱 10 条 | +10 |
| 3 | injection/trust 公开集 10 条 | +10 |
| 4 | AgentBench OS/DB 错误处理 5 条 | +5 |

---

## 5. 已核实链接

- https://github.com/SWE-bench/SWE-bench — HF `princeton-nlp/SWE-bench`，Verified 500，MIT  
- https://github.com/THUDM/AgentBench — 8 环境 + FC 版，Apache-2.0，arXiv:2308.03688  

其余名称为社区标准基准；落地前建议再开 Browser Use 核对具体数据集 license 与条目格式。
