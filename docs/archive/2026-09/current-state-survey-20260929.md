# 项目当前状态（梳理）

> 2026-09-29 · 截止本阶段（结构债 + Agent 运行时 + 评测网 + CI）  
> 实测数据，不是估算

---

## 1. 体量

| 项 | 数 |
|---|---|
| 提交 | 1,148（ahead 61） |
| 源码 py | 466（不含 alpha_zoo + pycache） |
| golden eval 题 | 70 |
| 文档 md | 61 |
| ruff 剩余 | 99（71 可 autofix，9 个 C901 复杂度） |

## 2. 巨文件（拆分后）

| 模块 | 大小 | 状态 |
|---|---|---|
| `core/goal/store.py` | 69KB | 已拆 `store_utils` |
| `core/study/store.py` | 63KB |  |
| `api/routers/study.py` | 62KB | 已拆 `study_helpers` |
| `api/session/service.py` | 61KB | 已拆 3 模块 |
| `api/routers/web_session.py` | 60KB |  |
| `api/session/projector.py` | 59KB |  |
| **`core/agent/loop.py`** | **55KB** | 已抽 6 mixin（原 105KB） |
| `core/study/runner.py` | 48KB | 已拆 3 mixin |

## 3. 测试与评估

| 项 | 数 |
|---|---|
| 单测 | 500+ py files，分片脚本就绪 |
| golden 模拟 | 70 题全过 (1.0) |
| 真 LLM（MiniMax-M3） | mean ≈ 0.957 / pass@0.7 ≈ 0.94 |
| CI | 分片 pytest（3×4）+ 夜跑 LLM eval |

## 4. Agent 能力面

| A1 | 预算（token/费用/时间） | ✅ |
|---|---|---|
| A2 | ToolGateway + 17 工具集 | ✅ |
| A3 | ToolResult 视图 | ✅ |
| A4 | Memory 注入 | ✅ |
| A5 | Runtime 拆分 | ✅ |
| A6 | 轻量子任务 | ✅ |
| A7 | 评测网 | ✅ |

## 5. CLI 入口（合并后 16 个一级）

`serve` / `backtest run|rerun|replay` / `check factor|run|accept` /
`autoresearch` / `preflight` / `import` / `init` / `list` / `llm` /
`validate` / `status` / `goal` / `hypothesis` / `study` / `engine` ...

## 6. 仍可做（按 ROI）

1. `web_session.py` 60KB 与 `projector.py` 59KB 同主题（事件→投影），可合并
2. CI 跑通后观察 flaky / 慢测，迭代 `pytest_shard.py` 分片
3. 夜跑 LLM 接入 secret 后查看首份 scorecard
4. Study 双引擎收口（phase / langgraph）
5. chat_loop / role_factory 合一
6. 借鉴 QuantStudio B1 `primary_score` v2

## 7. 评估网继续扩题的方向

- SWE-bench / AgentBench 通过 `evals/adapters/` 拉真实条目
- 装 `datasets` 后 `python evals/adapters/swebench_to_golden.py --limit 20`
- FinBen / Qlib 量化陷阱

## 8. 一句话

> **项目状态：稳。** 测试覆盖、评测网、CI 门禁、巨文件拆分、Agent 运行时均已就位。  
> 下一步是「门禁开跑后看报告 → 量化方法论借鉴 → 研究引擎唯一化」。
