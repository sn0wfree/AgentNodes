"""llmwikify kernel/agent — UnifiedAgentLoop 设计参考（只读）。

> **这是参考代码，不参与构建。** 来源：D:\\Github\\llmwikify（同作者项目）。
> 拷贝日期：2026-11-07。评估与接入方案见
> `docs/design/unified-loop-adoption-20261107.md`。
>
> **为什么放仓库里**：B 方案（增量收敛）的移植源。B1 步启动时，
> `_core_types.py` 改包名 `llmwikify` → `agentnodes` 即为
> `kernel/agent` 的统一 step 协议。
>
> **为什么不在 src/ 里**：54 处 `llmwikify.*` import 在本仓库是悬空的；
> 留在包内会让层门禁、nouns 门禁、打包全部误报。
>
> ## 设计速览
>
> 状态机：PRECHECK → [REASON → DECIDE① → ACT → DECIDE② → OBSERVE → DECIDE③] → FINALIZE
>
> - `loop.py`（301 行）：只做骨架。reasoner / actor / deciders / hook /
>   precheck / finalize 全部注入。
> - `_core_types.py`（131 行）：两个抽象——`StepHandler`（无状态单次）与
>   `StreamingHandler`（有状态多次 yield，最后一个 yield 是 StepResult）；
>   `StepResult{output, events, success, error}` 把产出与 SSE 事件打包；
>   `Pipeline` 串行组合（fail-fast）。
> - `steps/`（494 行）：checks / code / feedback / llm / transforms 五组实现。
> - `registry`（在 llmwikify 的 apps/chat/agent/unified/registry.py）：
>   `AgentModeConfig` 把整套装配命名成 mode，`create_agent_loop("chat")` 按名取。
>
> ## 与 AgentNodes 现状的关键差距（详见评估文档 §2）
>
> 1. 骨架未隔离（AgentNodes 的 `_run_loop_core` 埋在 1283 行 loop.py）
> 2. 停止判断不是按检查点配置的 decider 字典
> 3. 事件走回调副作用，不随 StepResult 返回
"""
