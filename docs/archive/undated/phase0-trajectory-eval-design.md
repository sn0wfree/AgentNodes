# Phase 0 设计：轨迹评估安全网

> 来源：`refactor-plan-20261008.md` Phase 0（调研借鉴 #1：LangSmith 式轨迹评估）
> 目的：重构的行为漂移主检测器——golden 分数过/不过太粗，轨迹（怎么到达答案的）才是敏感信号
> 范围：`evals/scorecard.py`（字段）· `evals/run_evals_llm.py`（采集）· `scripts/check_eval_regression.py`（漂移检测）· `evals/nightly-baseline.json`（基线）
> 状态：设计定稿，待实施

## 1. 采集面（读码确认）

| 已有 | 缺失 |
|---|---|
| `RunRecord.tools_used`（list，append 有序）· `tokens_used` · `finished_reason` | **iterations**（步数）· **tool_sequence**（严格序列含重复）· **tool_calls_made`（总次数） |
| `LoopResult.iterations / tool_calls_made / finished_reason / messages` | ——数据在 result 里，只是没进 RunRecord |
| `TaskScore`：tool_recall/violations/budget/content/completed/total | 轨迹字段无透传 |
| `to_dict()`：n/mean/pass_rate/tasks | 无轨迹聚合 |

## 2. 字段定义

```python
# RunRecord 增量（评分面不动：score_run 的工具集语义不变）
iterations: int = 0            # LoopResult.iterations
tool_calls_made: int = 0       # LoopResult.tool_calls_made
tool_sequence: list[str] = field(default_factory=list)  # 有序含重复（vs tools_used 的评分用法）
```

`TaskScore` 透传三字段进 `to_dict()["tasks"]`；顶层聚合增：

```json
"trajectory": {
  "steps_mean": 3.2, "steps_p90": 6,
  "tool_calls_mean": 4.1,
  "terminal": {"stop": 8, "budget": 1, "error": 1}
}
```

## 3. 漂移检测规则（check_eval_regression 扩展）

与既有 mean/pass 阈值并列，新增两条触发（同 exit 1 语义）：

| 规则 | 阈值 | 信号 |
|---|---|---|
| T1 steps 均值漂移 | \|Δsteps_mean\| > **30%** 相对变化 | 路径变长/变短（loop 行为变了） |
| T2 序列漂移 | per-task `SequenceMatcher.ratio < 0.5` 的任务数 > **20%** 且 ≥3 题 | 工具选择路径改变（重构改变了决策面） |
| T3 终态漂移 | 新出现非 `stop` 终态 且 无基线对应 | 新错误模式 |

**为什么相对 30% 而非绝对**：LLM 非确定下 steps 有天然抖动（±1 常见）；30% 在 10 题均值上约等于结构性变化。T2 用 difflib 标准库（无新依赖）。

## 4. baseline 流程

- **10 题高区分度子集起步**（injection / lookahead / overfit / safety / budget 类——`tags` 可筛）：API 成本低、信号足；全 70 题升级在 nightly 门禁稳定后
- 命令：`python evals/run_evals_llm.py --only <ids> --json > scorecard.json` → `check_eval_regression.py --update-baseline`
- baseline 与 scorecard **同 schema**（含 trajectory），入库 `evals/nightly-baseline.json`
- **M0 失败判据的验证**：故意注入漂移（如给某题答案前多调 3 次无关工具）→ 检测器必须 exit 1

## 5. 改动清单

| 文件 | 改动 |
|---|---|
| `evals/scorecard.py` | RunRecord +3 字段；TaskScore 透传；to_dict 轨迹聚合 |
| `evals/run_evals_llm.py` | `_run_one` 提取 iterations/tool_calls_made/tool_sequence（messages 已在） |
| `scripts/check_eval_regression.py` | T1-T3 规则 + `--update-baseline` 保持 |
| `evals/nightly-baseline.json` | 10 题真 LLM 跑一次生成（本地 MiniMax） |

**不动**：评分权重/`score_run` 语义（轨迹是**附加观测**，不进总分——总分变更会让新旧 scorecard 不可比）。

## 6. 验收

| # | 项 | 条件 |
|---|---|---|
| 1 | 字段采集 | scorecard.json 含 trajectory + tasks[].{iterations,tool_calls_made,tool_sequence} |
| 2 | 检测器·负例 | 注入轨迹漂移（多调工具）→ T1/T2 触发 exit 1 |
| 3 | 检测器·正例 | 同分重复跑（自然抖动）→ exit 0 |
| 4 | baseline | 10 题跑完写入含轨迹字段 |
| 5 | 回归 | 既有 check_eval 4 场景仍绿；69 文件不受影响（改动面全在 evals/scripts） |

## 7. 验收记录（2026-10-08，Phase 0 完成）

| # | 项 | 结果 |
|---|---|---|
| 1 | 字段采集 | scorecard/baseline 含 trajectory（steps_mean=2.3 · p90=5 · tool_calls_mean=1.8 · terminal stop:9/budget:1）+ tasks 四字段 ✅ |
| 2 | 检测器·负例 | T1 steps 漂移 / T2 序列漂移 / T3 新终态 —— 三者注入后均 exit 1 ✅（tests/test_eval_regression_detector.py 5 用例持久化） |
| 3 | 检测器·正例 | 相同轨迹 / 自然抖动 → exit 0 ✅ |
| 4 | baseline | 10 题真 LLM（safety/injection/budget/data/reason）：**mean 0.99 · pass@0.7 1.0** → evals/nightly-baseline.json ✅ |
| 5 | 回归 | 检测器自检 Δ0；ruff（evals/scripts/tests）清；C901 main 18→9 |

### 实施中修的两处采集器问题

1. **stdout 污染**：进度行与 `--json` 同走 stdout → 改进度行 `file=sys.stderr`；fitz 的 DeprecationWarning 仍会漏进 stdout（依赖层）→ `check_eval.load()` 容错剥非 JSON 前缀（消费侧防御）。
2. **dataclass 替换脚本误伤**：finished_reason 插入误匹配 RunRecord（重复字段）——手工修；「语义变换手工」教训再现（第 6 例）。

### 遗留

- 10 题 → 70 题 baseline 升级：nightly 门禁稳定后（refactor-plan §Phase 0 注）
- fitz warning 源头清理（pymupdf 迁移）：与 Phase 0 无关，可并入 P2 余项
