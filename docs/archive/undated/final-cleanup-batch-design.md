# 最终清理批次（c-4）：撒谎标签修正 + dag/env 真删除 + 余项收口

> 触发：第三轮快照后「继续清理」——对剩余 5 活标签与 dag/env 逐条做最终复验。
> **复验推翻了 4 个标签的事实陈述**（又一批 workflow/dag.py 式过时标签）。

## 1. 复验结果（rg 证据）

| 项 | 标签/此前判断 | 实际 | 处置 |
|---|---|---|---|
| `session/store.py::get_messages` | "0 production callers" | **`cli/tui/app.py:271` + `service.py:623`** | **修标签**（撤销） |
| `session/store.py::create_attempt` | "0 production callers" | **`service.py:215`** | **修标签**（撤销） |
| `session/store.py::list_attempts_by_status` | "0 callers; use list_attempts_by_status"（自指） | **`service.py:407`**（reload-recovery 路径） | **修标签**（撤销） |
| `webui Skeleton.tsx` (B17) | "never imported" | **`catalog/stories.tsx` + `skeleton.test.tsx`** | **修标签**（撤销） |
| `goal.py::market` 字段 (B8) | 静默丢弃 | 字段无读取方；pydantic extra=ignore → **删字段与现状行为等价**（传入仍被忽略） | **删字段** |
| `routers/memory.py` (B12) | "legacy, test-only — keep if ever needed" | 唯一调用方是 `test_api.py::test_memory_search`（测它自己） | **删 router + 该测试段** |
| dag/env（Step 2 只做 warning） | 收口 | 唯一消费方是 phase_engine 映射 + 2 个行为锁定测试 | **真删除**（行为收窄：'dag'/env → fallback phases + warning） |
| `progress.py` docstring | — | 含 "DELETE-CANDIDATE B3 executed" 字样 → rg 统计漂移 | 改写为纯历史陈述 |

## 2. dag/env 删除的语义变化（唯一的破坏性面）

| 输入 | 删前 | 删后 |
|---|---|---|
| `engine='dag'` | 静默 remap → langgraph | **warning + fallback → phases**（历史 if/else 语义的兜底） |
| `SR_STUDY_DAG_ENGINE=1` | remap phases → langgraph | **不再 remap**（env 被忽略） |

- API/schema/models 三处注释同步去掉 `'dag'`（字段/参数保留接受任意字符串——校验收窄是独立工作）
- `test_study_engine_dispatch` 两个行为测试**改写**：dag → fallback phases；env 不再改变行为
- **风险**：真有外部调用方传 dag 时行为从 langgraph 变 phases——这就是「假选项」的收窄目标；DB 存量 `engine='dag'` 的 study 会走 fallback warning 而非静默换引擎（更诚实）

## 3. 执行清单

1. `_resolve_engine`：删两 remap 分支（保留 unknown→pass-through + dispatcher fallback warning）
2. schemas/routers/models 注释 ×3
3. `test_study_engine_dispatch` 两测试改写为 fallback 语义
4. `routers/memory.py` 删 + `app.py` include + `test_api.py` 该测试
5. `goal.py` 删 `market` 字段
6. session/store 3 处 + Skeleton 标签撤销（改注释）
7. progress.py docstring 去标签字样
8. `test_dead_code_labels.py`：EXPECTED_LABELS 空（B8/B12 执行、B17 撤销）——反向检查（`test_no_unlabelled...`）保留为唯一活规则

## 4. 验收

- ruff src = 0 保持
- dispatch/round_outcome 测试全绿（行为断言已改写）
- 69 回归 PR-lane 0 failed
- `rg DELETE-CANDIDATE` 只剩 fixtures 注记（tests/_fixtures/legacy_events.py）
