# 阶段收口总结

> 2026-09-29 · 量化研究框架治理 + Agent 运行时 + 评测网  
> 冒烟：CLI 正常 · eval 70 题 mean=1.0 · 核心测试 78 绿

---

## 1. 交付一览

| 域 | 成果 |
|---|---|
| **可运行性** | CLI 双注册修复；`serve`/`backtest`/`check` 合并入口；版本 0.6.0 对齐 |
| **Agent 运行时** | A1 预算 · A2 ToolGateway · A3 结果视图 · A4 Memory · A5–A7 runtime/子任务/评测 |
| **结构债** | W1–W5 完成；巨文件大幅削减 |
| **评测网** | 5 → 70 golden；真 LLM mean≈0.96，弱题已校准 |
| **修复** | keys()、除零、save_metrics 回归、StudyStatus/session NameError、bg/Windows、白名单丢弃等 |

## 2. 巨文件削减

| 文件 | 前 | 后 |
|---|---|---|
| `agent/loop.py` | 105KB | **55KB** + 6 mixin |
| `api/session/service.py` | 92KB | **61KB** + 3 模块 |
| `core/autoresearch.py` | 75KB | **48KB** + 3 模块 |
| `study/runner.py` | 62KB | **48KB** + 3 mixin |
| 根 `templates/` + HTMX + 事件桥 | — | 已删/迁出 |

## 3. 评测

```text
70 题覆盖：因子 / 回测 / 安全 / 注入 / 预算 / 多步 / 推理陷阱 / 公开模式
真 LLM（MiniMax-M3）：mean ≈ 0.957，pass@0.7 ≈ 0.94
```

## 4. 剩余建议（下阶段）

1. CI：分片 pytest + 夜跑 `run_evals_llm.py`  
2. Study 双引擎收口（phase / langgraph）  
3. chat_loop / role_factory 合一  
4. 可选：QuantStudio primary_score v2  

## 5. 一句话

> **框架已可跑、可测、可评**；结构从巨石收成多模块，评测网上岗。下阶段是「门禁 + 双轨收口」，不必再堆功能。
