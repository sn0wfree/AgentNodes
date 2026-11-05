# P5-4 ContextHub W0 骨架 + pre-run 通路迁入 — 2026-10-27

> 调查修正了清单语义：**ContextHub 在代码里不存在**（差距图 §2.5「能力散落
> 3 处」），「4 builder」= `context_injector.py` 的 4 个 injector 实现
> （Goal/MemoryRetriever/Todos/GoalContinuation）。本 stage = 设计文档
> （`context-hub-discussion.md`）的 **W0 骨架落地 + pre-run 通路迁入**，
> 不是纯搬家。

## 1. 范围（对齐 W0/W1 切分）

| 件 | 处置 |
|---|---|
| `core/context/`（protocol + hub 骨架） | **新建**——ContextItem/ContextProvider/ContextQuery/ContextBundle/ContextHub |
| goal + memory pre-run 注入 | **迁入**——桥接为 provider，AgentLoop 组装走 hub |
| TodosInjector（per-iteration + 去重状态） | 留 W1（per-iteration 形态，非 pre-run） |
| GoalContinuationInjector（返回 bool = 决策） | **不转**（设计文档 §4 明文：控制决策不是上下文） |
| ContextBuilder（渲染器角色） | 不动——渲染归渲染器（讨论稿开放问题 1）；G6 另议 |
| ContextPlan 仲裁/裁剪 | 留 W2 |

## 2. 关键发现与形态决策

**检索 query 的链式语义**：旧 pre-run 循环是逐个前缀叠加——goal 注入后的串
作为 memory 检索的 query（`MemoryRetrieverInjector._build_block(loop, task)`
收到的 task 含 goal 块）。逐字节等价要求复现这个叠加。因此：

- **不做逐 injector 拆散桥接**（那会改变检索 query → 行为漂移）；
- **`PreRunContextProvider` 桥接整条 pre-run 链**：provide() 内按 order
  复刻叠加循环，产出按最终渲染序的多 items（memory、goal 各一条，kind
  分开可溯源），内容与旧路径逐字节相同。等价性由构造保证（与 B1-b 同
  方法论），等价测试钉死。
- W1 把链内成员独立成 provider 时，用 ContextQuery.prior_items 解决演进
  语义（设计稿本就预留 prior_kinds）。

## 3. 硬约束（讨论稿 §3，落地为架构门禁）

中枢只依赖协议：`core/context/` **禁止 import** `core/agent`/`core/goal`/
`core/study`——加进 `test_architecture_gates.py`。生产方适配器在
`core/agent/context_providers.py`（域 → 平台方向合法）。

## 4. 接口（与设计稿 §6 的差异如实记录）

```python
hub.register(provider)                     # 注册（生产方侧）
hub.build(query) -> ContextBundle          # 收集（best-effort：provider
                                           # 异常降级为无贡献 + dropped 记录）
bundle.explain() -> dict                   # 保留/丢弃/账本
```

差异：设计稿 `build(query, *, history, tools)` 的 history/tools 形参
**暂不收**——渲染还在 ContextBuilder，history/tools 入 bundle 是 W2
ContextPlan 并入时的事。tokens 用 `token_utils.estimate_tokens` 统一口径
（消三套估算中的 hub 侧一套）。

## 5. 迁移点（AgentLoop）

- `__init__`：构造 `ContextHub` + 注册 `PreRunContextProvider`（provider
  在构造时对 `_injectors` 的 order<0 成员做**一次性快照**作为桥接数据源
  ——`_injectors` 构造后视为冻结；todos/continuation 仍走
  `self._injectors` 的 per-iteration/post-response 通路，不动）。
- `_prepare_run`：`for injector order<0` 循环 →
  `hub.build(ContextQuery(task_text=full_task, session_id=...))` +
  渲染（items 序拼接 + full_task）。
- **行为不变量**：full_task 逐字节等价（等价测试直证）；provider 开关
  （enable_goal_injection / enable_memory_injection）语义不变。
- **契约收窄（如实记录）**：pre-run injector 从「任意 task 变换」收窄为
  **前缀注入**（`block + "\n\n" + task`）——hub 的 items+task 渲染契约
  表达不了任意变换；生态里仅有的两个真实实现（goal/memory）本就是
  前缀式。违约 injector 由 endswith 守卫按无贡献降级 + 告警（不产出
  垃圾块、不污染后续检索 query）。`test_dsh_deep_integration` 的追加式
  假件已改为前缀式。

## 6. 测试

- `tests/test_context_hub.py`：best-effort 降级（provider 抛异常不阻断 +
  dropped 记录）、token 账本统一口径、explain、同 query 同 bundle 确定性。
- `tests/test_context_providers_bridge.py`：整链桥接等价（旧 injector
  循环 vs hub 渲染，full_task 逐字节相等，含检索 query 链式语义）；
  开关关闭 → 无贡献；空 task → 无贡献。
- 架构门禁：core/context 零生产方 import。
- 回归：chat_loop / p2_e2e / dsh_deep_integration / role_factory /
  tool_brief / context_builder 全绿。

## 7. 评审轮（2026-10-28，多 agent 并行）

三切面并行评审（等价性切面因并发限额由其余切面覆盖）。实锤并已修复：

| # | 发现 | 修复 |
|---|---|---|
| P1 | **架构门禁相对导入盲区**：AST `ImportFrom.node.module` 不含前导点，`from ..agent import x` 对两个门禁（hub 门禁 + platform→study）均漏检（本仓库平台包相对导入是主流写法 30+ 处） | `_imports` 按 `node.level` 解析为绝对名 + `from pkg import name` 子模块候选 + 边界感知匹配；PLATFORM_PKGS 纳入 context |
| P1 | **hub 不变式 4 破洞**：`for item in items` 在 try 外，provider 返回非 list 直接 TypeError 外抛 | None 规范化为空贡献；非 list 在 try 内 raise → 降级 dropped |
| P1 | **契约违约无守卫**：追加式 injector 会被长度算术切成垃圾块并污染链 | endswith 守卫：违约按无贡献降级 + warning |
| P1 | **真 memory injector 零 hub 路径覆盖**：链式语义只有假件证明 | 补真 injector 集成测试（GoalContextInjector 打桩 + 真 MemoryRetrieverInjector + stub memory，逐字节等价 + 检索 query 链式断言 + 空 task 短路 + 违约降级） |
| P2 | DefaultPreRunStep 若接进主循环会与内联 `_prepare_run` 双跑 | pre_run.py 模块 docstring 互斥约定 |
| P2 | provider 构造时快照 `_injectors`，构造后变更失同步（旧路径每 run 重读） | loop.py 冻结声明注释 |
| P3 | hub 边界无测试（tokens 预填/provider 覆写/None 返回）、docs 声称的「空 task」测试不存在 | 全部补齐（测试 19→29） |

评审确认无问题项：等价性数学（渲染序反转逐字节一致）、无死代码残留、
无第二消费者遗漏、无双跑活跃路径、循环 import 无风险、四条消费链
（chat/study/workflow/swarm）均不传自定义 injectors、开销可忽略。
另记录两个范围外发现：workflow/executors.py AgentLoopExecutor 传 `tools=`
形参疑错（预存）；`test_default_injectors_loaded` 3→4 属修复 e97f3e94
（2026-09-29）遗留的破窗而非掩盖回归。

## 8. 后续（W1/W2 尾项，不在本轮）

W1：todos provider 化（per-iteration）、记忆注入三改（ContextBuilder 的
frozen snapshot + user-message recall 与 MemoryRetriever 合流）、链内
成员独立 provider（prior_items）。W2：ContextPlan 并入仲裁面 + 裁剪 +
snapshot/eval-diff。
