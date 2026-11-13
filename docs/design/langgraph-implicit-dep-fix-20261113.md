# 切断 chat 主路径的 langgraph 隐式依赖（2026-11-13）

> **性质**：**移动 + 改引用**（零删除，R-020 不适用）。
> **来源**：跨平台 e2e 实测发现（WSL 3 failed / Windows 6 passed）。
> **上位**：`step-protocol-migration-20261112.md` 无关；本批纯依赖拓扑修复。

---

## §0 结论摘要

| # | 事 | 结果 |
|---|---|---|
| 1 | 缺陷 | chat 主路径（`/api/chat/send`）**运行时**需要 `langgraph`，而它是可选 extra |
| 2 | 根因 | `service.py:997` 从 `kernel.workflow` 包内 import → 触发 `workflow/__init__` → `types` → `swarm` → `runtimes` → **`graph_engine.py` 硬 import langgraph** |
| 3 | 修法 | `orchestrate_guard`（122 行、纯 `os`/`re`/settings）**下沉**到 `kernel/agent/text_guard.py`；链条切断 |
| 4 | 影响面 | 2 个文件的 import 行 + 1 个测试的 import 行 |
| 5 | 删除 | **0**（原文件保留为 re-export 兼容壳，见 §3.3）|

---

## §1 问题实证

### 1.1 现象（WSL e2e，2026-11-13）

```
tests/test_chat_real_llm.py::test_send_and_get_real_response FAILED
E  Failed: LLM call failed: API key not configured...（表面原因）
服务端日志真因：
  File ".../session/service.py", line 997, in _run_with_guard
    from agentnodes.kernel.workflow.orchestrate_guard import (...)
  File ".../kernel/workflow/__init__.py", line 4
    from .types import AgentCall, AgentStatus, SwarmHook
  File ".../kernel/swarm/__init__.py", line 3
    from .preset_loader import list_presets, load_preset
  File ".../kernel/swarm/runtime.py", line 21
    from agentnodes.kernel.runtimes import END, INTERRUPT_KEY, GraphEngine, ...
  File ".../kernel/runtimes/graph_engine.py", line 30
    from langgraph.graph import END as _LG_END
  ModuleNotFoundError: No module named 'langgraph'
```

### 1.2 为什么是真缺陷

| 事实 | 含义 |
|---|---|
| `pyproject.toml`：`langgraph = ['langgraph>=0.2', ...]` 是**独立 extra**，不在 `dependencies`、不在 `api` | 按文档 `pip install -e ".[api]"` **拿不到** langgraph |
| `service.py` 是 API 核心（`/api/chat/send` 必经）| **所有 chat 用户**在第一条消息就炸 |
| import 在**函数体内**（`service.py:997`）而非模块顶部 | 启动/健康检查/登录全正常，**发第一条消息才炸**——延迟暴露，比启动即崩更难查 |
| Windows 环境恰好装了 langgraph | Windows 的"绿"是**环境债掩盖代码缺陷**（跨平台测试的价值所在）|

### 1.3 `orchestrate_guard` 本体不需要 langgraph

实测其 import：`os`、`re`、`agentnodes.foundation.settings`（3 个轻量）。三个导出函数
（`looks_like_question` / `max_continues` / `continue_instruction`）全是**纯文本判定 + env 读取**，
与 `workflow` 包无任何语义关系——它放在 `kernel/workflow/` 是**历史错位**（最初只为 DAG 会话服务）。

---

## §2 归属裁决

| 候选落点 | 评价 |
|---|---|
| `kernel/agent/text_guard.py` | ✅ **选定**：判的是"LLM 回答是否像在提问"，属 agent 语义；`kernel/agent/` 无重依赖 |
| `kernel/workflow/` 保持原地 + 改 `__init__` 懒加载 | 治标：链条仍在（仍加载 swarm/runtimes 全部模块），只是不炸 |
| 把 langgraph 并进 `api` extra | 让 chat 用户被迫装重依赖（含 `langgraph-checkpoint-sqlite`），违背可选依赖设计 |

**命名**：`text_guard.py`（不是 `orchestrate_guard.py`）——它已不专属 orchestrator，
叫 orchestrate 会再次误导归属。

---

## §3 落地设计

### 3.1 新文件 `kernel/agent/text_guard.py`

原 `orchestrate_guard.py` **全文搬移**，仅改模块 docstring 的归属说明
（去掉"DAG-orchestrator 专属"的暗示，改为"chat 通用续跑守卫；当前唯一消费者是
DAG 会话的 `_run_with_guard`"）。**逻辑零改动**——28 条参数化测试是行为契约。

### 3.2 改引用（2 处）

| 文件 | 改动 |
|---|---|
| `service.py:997` | `from agentnodes.kernel.workflow.orchestrate_guard import ...` → `from agentnodes.kernel.agent.text_guard import ...` |
| `tests/test_orchestrate_guard.py:11` | 同上（测试文件重命名见 §3.4）|

### 3.3 兼容壳（零删除，R-020 精神）

`kernel/workflow/orchestrate_guard.py` **保留为 re-export 壳**：

```python
"""兼容壳（2026-11-13）：实现已下沉到 kernel/agent/text_guard.py。

保留本模块仅为**不打断**可能的外部引用（本仓实测 0 处）；调用方应改
import kernel.agent.text_guard。择期删除（随 U6 之后的清理批）。
"""
from agentnodes.kernel.agent.text_guard import *  # noqa: F401,F403
from agentnodes.kernel.agent.text_guard import __all__  # noqa: F401
```

**为什么留壳而不是直接删**：本次是"移动"，删原文件属删除动作（R-020 要求排在批末、
R-015 要求四项齐备）。留壳零风险、零成本，且保持"移动"的语义纯度。

### 3.4 测试文件

`tests/test_orchestrate_guard.py` → **重命名**为 `tests/test_text_guard.py`
（git mv 保留历史），import 改指向新模块。28 条用例**断言零改动**（行为契约）。

---

## §4 验收

| # | 断言 | 方法 |
|---|---|---|
| 1 | **链条切断**：import `text_guard` 不触发 `langgraph` | 子进程测试：`sys.modules` 里无 `langgraph`，且 `workflow`/`swarm`/`runtimes` 均未被加载 |
| 2 | 行为零变化：28 条原测试全绿 | `tests/test_text_guard.py` |
| 3 | 兼容壳可用：从旧路径 import 仍工作 | 一条断言 `from kernel.workflow.orchestrate_guard import looks_like_question` |
| 4 | **WSL 复现验证**：不装 langgraph 也能跑 chat e2e | WSL venv 里 `uv pip uninstall langgraph` 后跑 `test_chat_real_llm.py` |
| 5 | 全量 + batch_gate | R-007 三件套 |

**第 1 条是本批的核心断言**——它必须**在 langgraph 未安装**的环境里验证才有效；
Windows 上 langgraph 已装，只能用 `sys.modules` 副作用检查（间接证据）。

**第 4 条是最强证据**：WSL 上卸载 langgraph 后 e2e 从 3 failed → 6 passed。

---

## §5 回滚

单 commit revert。移动 + 引用改动，无数据/配置迁移。
