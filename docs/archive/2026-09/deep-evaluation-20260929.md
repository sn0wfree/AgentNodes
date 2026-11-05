# 深度评估报告（认真版）

> 2026-09-29 · 全量复核 · v0.6.0  
> 方法：lint 全量 + 分层测试抽样 + 逐项对照结构债清单

---

## 1. 总评

| 维度 | 评级 | 依据 |
|---|---|---|
| 可运行性 | **A-** | CLI/回测/因子/真 LLM eval 0.96 |
| 正确性（近期修复后） | **B+** | 本轮又抓出 3 处真 bug 并修 |
| 测试 | **B+** | 核心路径 200+ 绿；Windows 3 处已知脆 |
| 结构 | **B** | W1–W3b 已减 ~5.5k 行；巨文件仍在 |
| Agent 运行时 | **B+** | A1–A7 齐全；loop 仍是巨石 |
| 文档 | **A-** | code-map / architecture / eval 可追溯 |

**结论**：项目**能跑、有评测网、有持续发现 bug 的能力**；主要剩余风险是 **结构复杂度** 与 **Windows/并发边角**，不是算法错误。

---

## 2. 本轮认真评估抓到并修复的真 Bug

| # | Bug | 影响 | 状态 |
|---|---|---|---|
| 1 | W1 回归：包内 `prepare.py` 缺 `save_metrics` | 指标契约永远退回 run.log | **已修** `98f999e` |
| 2 | `study.py` REDO 路径 `StudyStatus` 未导入 | 重做研究轮 → NameError | **已修** `c153768` |
| 3 | `runner.py` 知识收集 `session` 未定义 | collector emit → NameError | **已修** `c153768` |
| 4 | 测试 `read_text()` 无 encoding | Windows GBK 炸 | **已修** |

此前：`Registry.keys()`、零本金除零、子任务白名单丢弃、websearch 不回退、`run_backtest` 参数、`git_diff` None…

---

## 3. Lint 全量（诚实数字）

```text
109 问题：F401 44 · I001 26 · F821 15 · W292 11 · C901 9 · F841 4
```

| 类型 | 性质 | 处理 |
|---|---|---|
| F821×14 | 多为字符串注解 `StudyGraph`/`StudyInterrupt`（`from __future__ import annotations`） | 可 TYPE_CHECKING 导入消音 |
| **F821 session** | **真 NameError** | ✅ 已修 |
| F401/I001/W292 | 79 可 autofix | 建议一次 `ruff --fix` |
| C901×9 | loop/study 巨函数 | W5 |

---

## 4. 测试诚实面

| 批次 | 结果 | 说明 |
|---|---|---|
| 结构变更（CLI/workspace/projector） | 105+141+128 绿 | |
| 量化核心 | 147+76 绿 | |
| Agent 运行时 | 绿 | |
| study/API | 184 绿 / 3 红 | 红：`/tmp/ws` 路径、并发 supersede（Windows/竞态） |
| 全量 pytest | 本机 >10min，不宜盲跑 | 建议分片 CI |

**未覆盖**：真 LLM 全 golden 仅 5 题；无性能回归；无 API 压测。

---

## 5. 结构债进度与剩余风险

| 项 | 状态 | 剩余风险 |
|---|---|---|
| W1 templates | ✅ | 必须 **内容对齐** 后再删副本（已踩坑） |
| W2 CLI | ✅ | 旧命令仍并存，文档需统一 |
| W3a HTMX | ✅ | 低 |
| W3b 事件桥 | ✅ | 测试 harness 在 fixtures，可接受 |
| W4 models | ⬜ | engine 仅 re-export，风险低 |
| W5 loop 拆 | ⬜ | **最大维护成本** |
| 巨文件 | ⬜ | service 92KB / autoresearch 75KB |

---

## 6. Agent / 产品能力评估

| 能力 | 状态 | 备注 |
|---|---|---|
| 预算控制 A1 | ✅ | tokens/cost/time |
| Toolset A2 | ✅ | 已收紧 factor/backtest/strategist |
| ToolResult A3 | ✅ | |
| Memory 注入 A4 | ✅ | |
| Runtime 缝 A5 | ✅ | 未完全拆 loop |
| 轻量子任务 A6 | ✅ | toolset+budget |
| 评测网 A7 | ✅ | **mean 0.96** |

**产品判断**：研究链路（数据→因子→回测→验收）可用；Agent 不会「瞎跑」但复杂任务仍偏探索。

---

## 7. 风险矩阵

| 风险 | 可能 | 影响 | 缓解 |
|---|---|---|---|
| 再删“副本”踩内容差 | 中 | 高 | 删除前 diff/hash |
| 巨文件改动回归 | 高 | 中 | eval + 分层测试 |
| Windows/并发测试脆 | 中 | 低 | 修路径/encoding |
| 真 LLM 评测偏窄 | 中 | 中 | 扩 golden 到 20+ |
| C901 隐藏逻辑洞 | 中 | 中 | W5 拆分 |

---

## 8. 建议下一步（按 ROI）

1. **`ruff --fix` 清 79 项卫生问题**（半小时，零风险）
2. **TYPE_CHECKING 消音 F821**（静音噪音，暴露真 bug）
3. **golden eval 扩到 20 题**（提高回归灵敏度）
4. **W4 models 三合一**（低风险）
5. **W5 拆 `_execute_tool_call_core`**（中风险，测试保护下）
6. **CI：分片 pytest + 模拟 eval 门禁**

---

## 9. 一句话

> 这不是「到处漏水」的项目，而是 **功能扎实、边角有债、评测网已上岗** 的中型框架；  
> 认真扫一轮仍能抓出 NameError 级 bug，说明 **测试与 lint 门禁还不够硬**——先把卫生和 golden 加厚，再动巨文件。
