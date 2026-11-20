# 4c B2.3 上下文跨界（父/worker 必要上下文 schema）R-014 方案

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **前置**：B2.2 3a–3d 已验收（B2.2 §12 裁决）；seam 已归 L3
> `kernel/agent/tools/`；roadmap WP-4 行（§3）为验收/回滚要求来源。
> **范围**：只写方案。不接生产、不改 `isolation.py`/`_isolation_worker.py`、
> 不动普通工具调用路径。

## 0. 本阶段目标与非目标

**目标**（对齐 WP-4 目标行）：定义并验证**父进程与隔离 worker 之间必要
上下文**的传递与隔离——

1. 把现在自由透传的 `ctx` dict 收敛为**显式字段白名单 schema**（只有
   ToolContext 中的纯数据字段可跨界）；
2. **协议版本字段**：缺失 / 无效 / 不兼容版本显式报错，不静默容忍；
3. 双向构造器（`ToolContext` → wire dict → worker `ToolContext`）归属
   单一 codec 模块，两端（父进程发送侧、worker 解码侧）共用同一份校验；
4. 契约测试覆盖：往返一致、无对象/句柄泄漏、错误路径显式。

**非目标**：① 不接生产 AgentLoop / `BaseTool.ainvoke`（B2.4）；② 不实现
POSIX 生产执行（禁启用不变）；③ 不接 shell（B2.5 裁决延后）；④ 不改
ToolContext 本身的字段定义（`core.py:101-143` 不动）；⑤ 不引入 per-call
id 关联协议（判断属 B2.4，见 §5.6 / §6）。

## 1. 现状证据

**E1 · wire 层无 schema、无版本、无校验。** 任务行
`{"tool": ..., "args": ..., "ctx": ...}` 由
`_execute_on_channel` 直接 `channel.send`（`isolation.py:298`）；`ctx` 是
调用方手工拼的自由 dict（channel/pilot 测试即此形态），一路透传到
worker。worker 侧 `_build_ctx`（`_isolation_worker.py:72-92`）用
`.get()` 逐键取值：缺键 → 静默 None，键名拼错 → 静默 None，
**多余键 → 静默忽略**。schema 漂移在当下不可见。

**E2 · wire 字段清单缺 `runs_dir`。** ToolContext 的纯数据字段共 **9** 个
（`core.py:108-134`：workspace / session_id / strategy_dir / runs_dir /
results_tsv / write_roots / read_roots / message_id / tool_call_id）；
进程内装配传全 9 个（`loop/tools.py:70-86`），worker 只承载 8 个——
`_build_ctx` 没有 `runs_dir` 映射，B2.2 文档的「8 字段清单」沿袭自原型
E2 实验。worker 内任何读 `ctx.runs_dir` 的工具看到 None，与进程内行为
**静默分叉**。B2.3 必须显式裁决：纳入（本方案推荐）或记录排除理由。

**E3 · wire 存在类型谎言。** ToolContext 声明 `Path` / `tuple[str, ...]`
（`core.py:108-117`），JSON wire 只能是 `str` / `list`；`_build_ctx` 原样
传入（worker :83-92）→ worker 内 `ctx.workspace` 实际是 str。当前 write
试点能跑通仅因 `PathWhitelist.__init__` 做了
`Path(workspace).resolve()` 强转（`sandbox/legacy.py:181`）——任何工具
直接做 `ctx.workspace / rel` 的 Path 运算在 worker 内会 TypeError、在
进程内正常。类型强转必须是 codec 的显式职责，不能依赖下游偶然容忍。

**E4 · 必须拒绝跨界的进程内对象/回调字段。** ToolContext 中：
`emit_progress`、`emit_event`（绑定 `AgentLoop._emit`）、
`permission_evaluator`、`permission_gateway`、`data_store`、`sandbox`、
`before_execute`（`core.py:118-143`）——全部由 AgentLoop 每次调用装配
（`loop/tools.py:70-86`）。worker docstring 已声明这些不跨界、「其余留
3c 之后评估」（worker :78-79）——**B2.3 就是那次评估**：结论是全部留在
父进程，永不入 wire。审批已经走对象无关路径（ASK 事件行 +
`ApprovalProxy` 裁决，`isolation.py:118-131`），worker 从不接触
permission 对象；`tool_call_id` 是数据字段，继续作为审批配对键。
另注意：loop 还在 ctx 之外注入 kwargs（`_progress_callback` /
`emit_event` / `workspace` / `session_id` / `_subagent_count_ref` /
`_parent_registry`，`loop/tools.py:65-93`）——worker 路径只注入 `ctx`
（worker `_run_tool` :105），不注入这些 kwargs，此分叉记入契约（§2.6）。

**E5 · 父进程侧完全没有编码器。** 现在没有
`ToolContext → wire dict` 的构造器：调用方手工拼 dict，生产侧
ToolContext 装配单一事实源是 B1-d 的 `context.py::build_tool_context` +
`loop/tools.py::_build_tool_kwargs`——两者都不知道 wire 的存在。B2.4
接线时若让生产调用点继续手拼 dict，schema 将失守。

**E6 · 版本错配的现实形态有限但契约价值不变。** worker 脚本由
`isolation.py` 的 `Path(__file__)` 定位（`isolation.py:98`），父/worker
天然同树；常驻 worker 生命周期不超出父进程。版本字段不是解决 skew 的
机制，而是**漂移显式化**的契约锚（混合版本/手拼 dict 当场报错，而不是
静默错行为）——这也是 WP-4 回滚行「协议版本化」的直接落点。

## 2. 深模块设计

### 2.1 落位：新私有同目录模块 `_isolation_context.py`

**建议**：`kernel/agent/tools/_isolation_context.py`（新，L3 私有
sibling）。理由：

- 命名与既有 `_isolation_win32.py` / `_isolation_worker.py` 一致——
  seam 的私有实现件已在同目录成组，codec 是第三件；
- **不并入 `isolation.py`**：该文件已承载 adapter/通道/常驻 worker/
  审批代理（~450 行），其公开面是「隔离执行 seam」；wire schema 是协议
  内部件，单独成件使契约测试（全平台无 skipif）可以直接测 codec 而不
  触碰平台路径；
- **不并入 `context.py`**：那是 B1-d 的执行引擎共享 ToolContext 装配
  单一事实源，被所有执行器消费；把隔离 wire codec 塞进去会迫使每个
  执行器间接耦合隔离协议（违反 locality）；
- worker 入口 import 面最小化：worker 脚本只 import codec（+
  `tools.core.ToolContext`），不拉整个 `isolation.py`。

### 2.2 schema：显式白名单 dataclass

```
IsolationCtxWire（frozen dataclass，字段即白名单）
  ctx_v: int                     # 协议版本，编码时由模块常量注入
  workspace / strategy_dir / results_tsv / runs_dir : str | None   # 绝对路径
  session_id / message_id / tool_call_id : str | None
  write_roots / read_roots : tuple[str, ...] | None
```

**dataclass 而非 dict 白名单**（推荐）：① 类型强转有显式落点（E3）；
② 字段清单可 grep、可被契约测试逐字段断言；③ 构造器天然拒绝未知
kwarg（TypeError），白名单由语言机制而非 if 链保证。TypedDict 是类型
注解不是运行时约束，挡不住 E1 的静默漂移。

**版本字段**：wire dict 顶层必须含 `ctx_v`（当前 = 1）。缺失 / 非整型 /
`> 1`（不兼容）/ `<= 0`（无效）→ 显式报错。不做「无版本视为 v0」的
过渡容忍：零生产接线是收紧契约的最便宜时刻（B2.2 全部 15 个手拼 ctx
调用点在 3b 一并切换），且 WP-4 验收行明确要求「缺失…显式报错」。

### 2.3 双向构造器（codec 两个纯函数）

- `to_wire(ctx: ToolContext) -> dict`：**白名单拷贝**——按显式字段映射
  取 9 个数据字段，Path → str、tuple 原样；进程内对象/回调字段**按
  设计不读**（不是"丢弃"，是从不出现在映射里）——泄漏由构造 impossibility
  保证，而非运行时过滤。版本键由 codec 注入，调用方无法伪造缺版本。
- `from_wire(dict) -> ToolContext`：worker 侧唯一入口，校验顺序：
  ① `ctx_v` 缺失/无效/不兼容 → 拒；② 白名单外键 → 拒（报错含键名）；
  ③ 类型校验 + 强转（str→Path、list→tuple）；④ 路径字段绝对性校验
  （`is_absolute()`、parts 中无 `..`）；⑤ 缺失字段 → None（与
  ToolContext 默认一致）。

错误类型：`IsolationContextError`（模块内定义）。**两侧失败面不同**：
父进程侧 `to_wire`/校验失败在**发送前**抛异常（坏 dict 根本不出父进程）；
worker 侧 `from_wire` 失败**不得让 worker 崩溃退出**——返回结构化错误
结果行 `{"result": {"status": "error", "error": "ctx schema reject: ..."}}`
→ 父进程映射 `failed`/`returned`。常驻 worker 的解码失败**不毒化**
（worker 仍然健康，毒化语义只属于 deadline/EOF——与 3c D2 边界清晰）。

### 2.4 敏感值与路径穿越

- **api key / 凭据**：白名单结构保证 wire 上**不存在**可放密钥的字段；
  任何新字段必须先改 schema（改 codec + 契约测试 + 版本评审），密钥
  无法从 args 之外的暗道进入 ctx。codec 不读环境变量。
- **路径字段**：wire 层只做类型 + 绝对性 + `..` 段检查——**不做**语义级
  穿越判定（相对 workspace 的越界归属）——那是 `PathWhitelist` 的
  单一职责（3b 已证：绝对路径写入被工具写根保护拒绝）。两层防线各管
  各的，不在 codec 里复制白名单逻辑。
- **已知边界**：worker 子进程继承父进程环境变量（API key 经 env 跨界
  是 adapter 层事实，不在 ctx 管辖）——记入 §4，不假装 schema 解决了它。

### 2.5 缺失 / 多余字段策略

| 情形 | 策略 | 依据 |
|---|---|---|
| 必填字段缺失 | 无（全部可选） | ToolContext 本就全默认 None；工具级处理（write 对缺 workspace 返回 err_actionable）已验证 |
| 可选字段缺失 | None | 与进程内默认一致 |
| 白名单外多余键 | **两侧都拒** | E1 的静默忽略正是要消灭的漂移通道；wp-4「对象/句柄不跨进程泄漏」的 decode 侧防线 |
| `ctx_v` 不兼容 | 显式报错 | WP-4 验收行原文 |

多余键拒绝的代价：B2.2 的手拼 dict 调用点（测试 15 处）在 3b 切换为
codec 构造——一次性的、可见的 diff，换来今后 schema 变更必然显式。

### 2.6 契约口径（写入 codec docstring + 契约测试）

1. wire 上只有 9 个数据字段 + `ctx_v`；回调/进程内对象永不出现在
   wire（契约测试：回调全填的 ToolContext 编码后键集合恰为白名单）；
2. worker 内 `ToolContext` 的回调字段恒为 None——**isolatable 工具必须
   容忍无回调 ctx**（此条记为 B2.5 准入复核候选，本批不实现，见 §6）；
3. worker 只注入 `ctx`，不注入 loop 的 kwargs 注入面
   （`workspace`/`session_id`/`emit_event`/`_progress_callback`/…）——
   依赖 kwargs 直注 workspace 的工具在 worker 内行为不同，B2.5 准入
   复核时一并校验；
4. 解码失败 ≠ 毒化；毒化只属于 deadline / EOF（3c D2 语义不动）。

## 3. 分批与验收

| 子批 | 内容 | 验收 |
|---|---|---|
| 3a | `_isolation_context.py`：schema dataclass + 版本常量 + `to_wire`/`from_wire` + `IsolationContextError` + 契约测试 `tests/test_isolation_context.py`（**全平台无 skipif**——codec 纯函数，与 platform_matrix 同风格）；零接线（仅测试 import） | 往返测试 9 字段全过（含 Path/tuple 强转还原）；`ctx_v` 缺失/无效/不兼容各自显式报错；白名单外键两侧拒；回调全填的 ToolContext 编码后无任何对象键（泄漏不可能性断言）；相对路径 workspace 被 wire 层拒；全量 pytest + batch_gate 绿；新增 L3 文件走 arch 数字同步 |
| 3b | 接线：worker `_build_ctx` 改用 `from_wire`（`runs_dir` 纳入）；`isolate_tool_call`/`IsolationWorker.call` 发送路径编码 + 校验；`ctx_v` 进任务行；worker 解码失败 → 结构化错误行（不毒化）；channel/pilot 测试切换为 codec 构造 ctx | 真 worker E2 往返（Windows 真机，win32 gate）；版本不兼容真 worker 报错测试（patch 版本常量模拟旧端）；解码失败 → failed/returned 且 worker 存活可复用；既有隔离 19 测试不退化；全量 + batch_gate 绿 |
| 3c | 契约硬化：worker 增加 `probe_ctx_echo` 探针（仅测试用，回显收到的 ctx 键集合）→ 契约测试断言无对象/回调键跨界；B2.5 准入复核候选清单落 §2.6 口径；B2.2 文档交叉引用更新 | WP-4 验收行逐条映射通过（见下表）；平台矩阵守卫不受影响；POSIX skip-with-reason 口径不变 |

**WP-4 验收行 → 落点映射**（3c 收口时逐条核对）：

| WP-4 验收 | 本方案落点 |
|---|---|
| 最小上下文可跨界往返 | 3a 往返契约测试 + 3b 真 worker E2 |
| 缺失、无效和不兼容版本显式报错 | 3a codec 三类错误 + 3b worker 错误行（各自有测试） |
| 对象/句柄不跨进程泄漏 | `to_wire` 白名单构造（impossibility）+ `from_wire` 多余键拒绝 + `probe_ctx_echo` 回显断言 |
| 相应契约测试覆盖 | `test_isolation_context.py`（全平台）+ channel/pilot 扩展（win32） |

门禁沿用 R-007：先受影响聚焦测试 → 全量 pytest → `batch_gate` →
数字同步；每子批独立提交、独立回滚。

## 4. 风险与回滚

- **严格化破坏既有手拼 dict**：影响面 = B2.2 测试调用点 15 处 + 无生产
  消费者（seam 零生产接线，`isolation.py` docstring 与 §10 迁移记录
  双重确认）。3b 同批切换，diff 可全量审。
- **版本字段的诚实定位**：它是漂移显式化锚（E6），不是 skew 修复机制——
  不得在文档/发布说明宣称"版本化解决了新旧版本共存"。混合版本当场
  报错、显式失败，就是它的全部承诺。
- **codec 与 ToolContext 字段演化的耦合**：ToolContext 新增数据字段时
  codec 必须同步（否则字段静默不跨界）——缓解：契约测试断言 wire 键
  集合 == 白名单，字段漂移在 3a 测试层先红。
- **worker 继承父 env 的密钥跨界**：ctx schema 不管 env；如需隔离 env
  属 adapter 层工作（B2.4+ 另议），本批只记录不处理。
- **回滚**：每子批独立提交；3b 回退即恢复 B2.2 已验收 IPC 行为
  （wire 无版本状态）；普通工具调用路径全程未动（零生产接线不变），
  回滚零生产影响。版本键的存在使任何混合状态**显式报错**而非静默
  错行为——回滚安全网本身是版本化的收益。

## 5. 待确认决策点（含推荐默认）

| # | 决策点 | 推荐默认 | 备选与理由 |
|---|---|---|---|
| 1 | schema 载体 | **frozen dataclass**（`IsolationCtxWire`） | TypedDict/dict 白名单：类型注解无运行时约束，挡不住 E1 漂移；强转逻辑会散落成 if 链 |
| 2 | 版本字段 | **wire 内必填 `ctx_v`（int，=1）**，缺失即报错 | 放任务行信封级 `{"v":...}`：版本化整个任务行协议，但 B2.2 协议已验收、改动面更大；ctx 级最小增量，信封级留给 B2.4（若 per-call id 一起做） |
| 3 | 无版本 dict 的过渡容忍 | **不做**，3b 一次性切换 15 个测试调用点 | 容忍 v0 + 告警：迁移更平滑，但 WP-4 要求"缺失…显式报错"，且零生产接线时容忍没有受益方 |
| 4 | worker 端多余键 | **拒绝**（报错含键名） | 忽略（B2.2 行为）：向前兼容更好，但漂移不可见，与 decode 侧防线目标相悖 |
| 5 | `isolate_tool_call(ctx=...)` 形态 | **接受 `ToolContext \| None`**（内部 `to_wire`）；同时接受 codec 产出的 wire dict（校验后发送）；裸 dict 无 `ctx_v` → 报错 | 仅 wire dict：调用方负担重、易手拼出错；仅 ToolContext：契约测试不好直接驱动 wire 层。双形态都过 codec 校验，无旁路 |
| 6 | `runs_dir` | **纳入 wire**（第 9 个数据字段，进程内已在传） | 继续排除：需在 codec 里记录排除理由，且 B2.5 试点一旦涉及 runs 目录即翻案 |
| 7 | per-call id 关联协议 | **不纳入 B2.3，属 B2.4** | B2.2 §8 D2 原文将其归"B2.3 的协议演进"，但它是请求-结果关联的时序问题（迟到结果/复用安全），毒化模型已给出拒收保证（3c S3 已验收）；与 deadline/lease 同域，应随 WP-5 裁决 |

## 6. 明确不做

1. **shell 工具接入**——B2.5 裁决延后，不因 codec 落地而顺手扩大。
2. **生产接线**——不改 `BaseTool.ainvoke` / `AgentLoop` / `loop/tools.py`；
   生产调用点改用 codec 属 B2.4 接线方案。
3. **POSIX 生产启用**——禁启用保护不变；codec 契约测试全平台跑正是为
   未来 POSIX 验证积累无平台依赖的覆盖。
4. **per-call id 关联协议**——归 B2.4（§5.7）。
5. **worker 进程 env 脱敏**——adapter 层另议。
6. **ToolContext 结构变更 / `context.py`（B1-d）装配变更**——schema 只
   是 ToolContext 数据字段的 wire 投影，源结构不动。
7. **`emit_progress` 等回调的跨界实现**（如 worker 侧进度回传协议）——
   本批只确立"回调不入 wire"；若 B2.5 试点需要进度回传，另走方案。
