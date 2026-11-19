# 4c B2.2 隔离 worker / IPC 平台 seam 方案

> **性质**：R-014 方案先行 · **状态**：待维护者批准 · **日期**：2026-11-19
> **前置**：B2.1 准入校验已落地（`1a991dbc`）；原型五实验已验证
> （超时设计 §9.7）；维护者裁决：B2.1→B2.5 分阶段、逐阶段验收、POSIX
> 跨平台生产启用前必须验证、首试点限临时工作区 `file_write`、shell 暂缓、
> sync-only xfail 在范围外。

## 0. 本阶段目标与非目标

**目标**：建立一个**平台抽象 seam**，让「隔离执行 worker」可被替换
（Windows 实现 / POSIX 未来实现 / in-process 假件），并把原型里已验证的
五实验能力（spawn / 上下文跨界 / 审批 IPC / 树终止 / E5 截止序列）封到
一个深模块接口后。

**非目标**：① 不接生产工具执行路径（B2.4 才接 deadline）；② 不实现
POSIX 生产执行（本阶段只定义接口 + 显式禁启用保护）；③ 不迁 shell、
远端/DB 工具（B2.1 准入门槛已拒绝 DB/NET，shell 裁决暂缓）；④ 不解决
sync-only 客户端线程 xfail。

## 1. 现状证据

- 原型 `scripts/prototypes/sync_isolation_probe.py` 已验证 Windows 侧：
  Job Object 首选、受限宿主下 `taskkill /T` 降级、JSON Lines IPC、审批
  往返、子进程树确认终止、E5 截止+lease 序列（§9.7 五实验全 PASS）。
- 原型 `Worker` 强绑定 `ctypes.WinDLL("kernel32")`——**POSIX 不可导入**；
  裁决要求跨平台生产启用前验证 → 本阶段必须加平台保护。
- B2.1 已落地 `check_isolation_admission()`：DB/NET effects 的 isolatable
  声明被拒；仅 FS / 只读可声明 → 首试点 `WriteFileTool`
  （`kernel/agent/builtin_tools/file_tools.py:258`）符合准入。
- `BaseTool.ainvoke`（`tools/core.py:668`）是执行入口，但本阶段不改它。

## 2. 深模块设计

**模块**：`kernel/agent/tools/isolation.py`（新，L3）。

**接口**（唯一对外 seam，小接口深实现）：

```
isolate_tool_call(tool, args, ctx, *, deadline_s) -> IsolationResult
```

`IsolationResult` 携带：status（`completed` / `terminated` / `failed`）、
payload（工具返回或错误）、actual_state（`stopped` / `returned` / `unknown`
——与 4a 实际状态词表一致）、platform（用了哪个 adapter）。

**内部 seams**（不对外暴露）：
- `_PlatformAdapter` Protocol：`spawn_worker()` / `kill_tree_and_confirm()` /
  `supported() -> bool`；
- `_WindowsJobAdapter`（从原型提炼：Job Object + taskkill 降级）；
- `_PosixAdapter`：**`supported()` 返回 False**，调用即抛明确
  `UnsupportedPlatform`（防止未验证平台上生产启用）；
- JSON Lines IPC 通道（原型 `_emit` / `read` / `_pump`）；
- 审批回调代理（原型 E3 的 ASK→父进程裁决→回传）。

**locality**：平台差异收敛在 adapter 接口下；leverage：一个调用点服务所有
通过准入的工具；deletion test：删掉模块 → 能力降回进程内执行（无替代），
证明它不是浅转发。

## 3. 分批与验收

| 子批 | 内容 | 验收 |
|---|---|---|
| 3a | `isolation.py` 骨架 + Protocol + WindowsJobAdapter（提炼原型）+ PosixAdapter 禁启用保护 + 五实验行为测试（不接生产） | 原型五实验等价测试在深模块接口上全绿；POSIX adapter `supported()==False` 且调用抛 UnsupportedPlatform |
| 3b | IPC 通道 + 审批回调代理 + `actual_state` 与 4a 词表对齐 | E2/E3 单测；状态词表与 `timeout_tool_outcomes` 一致 |
| 3c | 临时工作区 `WriteFileTool` 试点（脚本级，不接 AgentLoop 生产路径） | 试点脚本：写文件成功 / 截止终止 / 迟到结果拒收 / lease 确认后释放；shell 不接入 |
| 3d | 平台测试矩阵（Windows 真机必过；POSIX CI 标记 skip-with-reason，生产启用前补） | gate 记录平台覆盖；未验证平台显式 skip 而非静默通过 |

## 4. 风险与回滚

- 进程终止无法撤销已发出副作用（FS 写文件可能部分完成）——试点报告
  必须声明，不宣称无副作用；
- Windows 受限宿主下 Job 分配被拒 → 降级 taskkill（原型已验证）；
- 每子批独立提交、独立回滚；本阶段不接生产 → 回滚零生产影响。

## 5. 待确认决策点

1. `isolate_tool_call()` 单入口形状是否接受？
2. POSIX adapter 本阶段只做「禁启用保护」是否接受（vs 本阶段就实现）？
3. 试点工具确认 `WriteFileTool`（临时工作区），shell 暂缓？
4. 3a-3d 逐子批独立提交、3d 后单独批准 B2.3（上下文跨界）？（零代码改动，仅方案；批准后按 R-014 子批实施。）

## 6. 子批 3a 执行记录（2026-11-19 · 已实施）

按推荐答案推进（单入口 / POSIX 禁启用 / WriteFile 试点 / 逐批）：

- `apps/chat/isolation.py`（新）：平台 seam——`WorkerAdapter` Protocol
  （supported/spawn/kill_and_confirm/close）+ `_PosixAdapter`（supported
  False、spawn 抛 `UnsupportedPlatform`）+ `build_worker_adapter()` 按
  `sys.platform` 选 adapter。**顶层不 import ctypes.WinDLL** → POSIX 可安全导入。
- `apps/chat/_isolation_win32.py`（新）：Windows 实现，从原型提炼
  Job Object + taskkill 两级降级、逐 pid 确认退出。延迟 import（仅 win32）。
- `tests/test_isolation_adapter.py` 4 用例：Protocol 合规、POSIX 禁启用、
  E1 spawn/close、E5 kill 确认退出（Windows 真机跑）。E2 上下文/E3 审批
  留 3b IPC 层。
- **零生产接线**——不改 BaseTool.ainvoke、不接 AgentLoop/GraphEngine。

验证：适配器 4 passed（Windows E1/E5 实跑）；层方向违规 0；全量
`-n 12` 7859 passed / 106 skipped / 2 xfailed / 0 failed；batch_gate
ALL GREEN；数字同步（454 文件 / 95,024 行，L2 +2 文件 = 两个 adapter）。

下一子批 3b（IPC + 审批回调 + actual_state 对齐 4a）需单独批准。

## 7. 子批 3b 执行记录（2026-11-19 · 已实施）

IPC 通道 + 审批回调代理 + actual_state 对齐 4a 词表：

- `apps/chat/isolation.py` 扩展：`IsolationChannel`（JSON Lines，后台读
  线程 + 队列；``_EOF`` 哨兵把「读超时」与「worker 已退出」区分开——
  早期实现两者都用 None 表示，worker 首次 import 慢于一次读超时就被
  误判为已退出 → actual_state 错报 unknown）；`IsolationWorker`（常驻
  worker，spawn 一次复用——每次 spawn ~500ms 成本，生产与批量测试都应
  复用）；`ApprovalProxy`（fail-closed：未知裁决 → deny，worker 内不做
  权限判定）；`isolate_tool_call()` 单入口（可传常驻 worker 复用进程）。
- `apps/chat/_isolation_worker.py`（新）：worker 入口——`_install_stdout_
  guard()` 把 sys.stdout 改指 stderr（依赖 import 的告警行不得污染协议
  帧）+ 父进程读线程跳过非 JSON 行，双向防护；可序列化 dict →
  `ToolContext` 构造；审批请求行（ASK）→ 父进程裁决 → 回传 worker。
- actual_state 词表：`ACTUAL_STATES` 与 4a `timeout_tool_outcomes[].state`
  严格一致（stopped / still_running / returned / unknown）。正常完成
  `stopped`；审批拒绝 / 工具级失败（工具返回结构化 error，worker 执行
  本身成功）`returned`；worker 无结果行退出 `unknown`。
- `tests/test_isolation_channel.py` 10 用例：E2 上下文跨界、E3 审批往返
  ALLOW/DENY 两路、fail-closed 默认拒绝、词表对齐、POSIX 显式拒绝、
  读超时≠EOF 回归、常驻 worker 复用、单入口接常驻 worker、真实 write
  工具隔离执行（绝对路径被工具写根保护拒绝 + 相对路径写入 workspace
  成功——3c 试点的前置能力证明）。Windows 真机跑。

验证：隔离 10 passed + 适配器 4 passed（并入全量，常驻 worker 使
wall-clock 无劣化）；全量 `-n 12` 7869 passed / 106 skipped / 2 xfailed
/ 0 failed（81.7s）；batch_gate ALL GREEN；数字同步（455 文件 /
95,494 行）。

下一子批 3c（临时工作区 WriteFileTool 试点）需单独批准。

## 8. 子批 3c 方案（2026-11-19 · 先行）

§3 验收行：试点「写文件成功 / 截止终止 / 迟到结果拒收 / lease 确认后
释放；shell 不接入」。方案审查发现两个必须先补的 seam 缺口，否则
S2/S4 无法诚实通过：

- **缺口 A（截止即未知）**：现在 deadline 触发后 `_execute_on_channel`
  直接返回 `failed`/`unknown` 并返回调用方，杀进程发生在 `finally` 的
  `adapter.close()`——**杀而不确认**，actual_state 永远报不到 `stopped`
  （4a 词表：确认终止才能记 stopped）。S4 的「确认后释放」无从成立。
- **缺口 B（常驻毒帧）**：常驻 worker 超时后进程还在跑工具，下次
  `call()` 的任务行会排在旧任务后面，旧任务的**迟到结果行会被新调用
  冒名接收**——正是「迟到结果拒收」要防的错配。

### 8.1 决策

| # | 决策 | 内容 |
|---|---|---|
| D1 | 截止升级为「确认停止」 | transient `isolate_tool_call` 在 deadline 触发时先 `adapter.kill_and_confirm(handle, [根pid], 确认超时 5s)`：全部确认死 → `status="terminated"`、`actual_state="stopped"`；有 pid 未确认 → 保持 `failed`/`unknown`。`close()`（释放句柄）在确认**之后**。EOF 分支（worker 自然退出无结果行）语义不动——保持 `failed`/`unknown`（本批不改，是否升 stopped 留 B2.3 裁决） |
| D2 | 常驻 worker 超时即毒化 | `IsolationWorker.call` 返回 `unknown`（deadline 或 EOF 无结果）→ worker 置 poisoned：先 kill_and_confirm（timed_out 路径同 D1 升级），然后关闭；后续 `call()` 抛 `RuntimeError`。**不引入每调用请求 id 关联协议**——那是 B2.3 的协议演进，本批用毒化达成拒收保证 |
| D3 | 试点形态 = pytest | `tests/test_isolation_pilot.py` 四场景 S1-S4（Windows 真机跑 + POSIX skip-with-reason），与 3a「原型五实验 → 适配器测试」同构：可重复、进 gate，不落散装脚本 |
| D4 | 确认口径（诚实边界） | kill_and_confirm 确认的是**根 pid**；树内其余成员由 Job Object 语义覆盖（`TerminateJobObject` 原子杀全树 + `KILL_ON_JOB_CLOSE` 兜底；taskkill `/T` 同为树杀）。handle 不追踪后代 pid——记录此口径，**不宣称全树逐 pid 确认** |

### 8.2 场景 → 实现映射

| 场景 | 驱动 | 断言 |
|---|---|---|
| S1 写文件成功 | 真实 `write` 工具 + tmp workspace（transient） | `completed`/`stopped`，文件落盘、绝对路径被写根拒绝（3b 已证，复跑一次作试点基线） |
| S2 截止终止 | 新增 `probe_sleep` 探针（worker 脚本内，仅测试用）睡 8s，deadline 1.5s | `terminated`/`stopped`；wall-clock < 6s（4x 余量） |
| S3 迟到结果拒收 | (a) S2 结果 payload 无工具输出；(b) 常驻 worker 超时后置 poisoned，后续 call 抛 RuntimeError | 迟到行结构性与协议性双重拒收 |
| S4 lease 确认后释放 | stub lease 记录事件序包裹 S2 调用 | `acquire` < （`actual_state=="stopped"` 即确认完成）< `release` |

### 8.3 不做

shell 工具不接入；不改 BaseTool.ainvoke / AgentLoop / 生产路径；不做
per-call id 协议演进（B2.3）；POSIX 不实现（禁启用不变）；EOF 无结果
分支语义不动。

## 9. 子批 3c 执行记录（2026-11-19 · 已实施）

按 §8 方案实施（D1-D4 全部落地）：

- `apps/chat/isolation.py`：`IsolationResult` 增 seam 内部驱动字段
  `timed_out`；`_execute_on_channel` 区分 deadline 触发与 EOF 退出（
  payload 消息分开）；新增 `_confirm_termination()`（杀 + 逐 pid 确认，
  全部确认死 → `terminated`/`stopped`，未确认保持 `failed`/`unknown`；
  确认口径按 D4：根 pid 确认，树由 Job Object 语义覆盖）。transient
  路径先杀+确认再在 finally 释放句柄（确认先于释放）。`IsolationWorker`
  增加 `_poisoned`：`unknown` 结束的调用 → 杀+确认（timed_out 路径升级
  词表）→ 毒化关闭，后续调用抛 RuntimeError（检查顺序：毒化先于已关闭
  ——毒化是更具体的死因；首版顺序反了，试点 S3b 当场抓住）。
- `apps/chat/_isolation_worker.py`：新增 `probe_sleep` 探针（仅测试用，
  驱动 deadline 与迟到结果场景）。
- `tests/test_isolation_pilot.py`（新）5 用例：S1 真实 write 写临时
  工作区成功；S2 截止终止（terminated/stopped，wall-clock < 6s）；
  S3a 迟到结果不归因（payload 无工具输出）；S3b 常驻毒化后拒绝复用；
  S4 lease 事件序 acquire < confirmed < release。Windows 真机跑。

验证：隔离三文件 19 passed（试点 5 + 通道 10 + 适配器 4，11.1s）；
全量 `-n 12` 7874 passed / 106 skipped / 2 xfailed / 0 failed（95.5s）；
batch_gate ALL GREEN；数字同步（455 文件 / 95,562 行，L2 +68 行）。

下一子批 3d（平台测试矩阵：Windows 必过 / POSIX skip-with-reason 进
gate 记录）需单独批准。

## 10. seam 层级迁移记录（2026-11-20 · 已实施）

roadmap §6 #2 裁决落定：通用 worker/平台机制归 kernel——本迁移是
**回归 §2 的原定落位**（当时 L2 落位是偏差，不是设计变更）。三文件
`isolation.py` / `_isolation_win32.py` / `_isolation_worker.py` 由
`apps/chat/`（L2）迁至 `kernel/agent/tools/`（L3），保持同目录（两条
硬耦合：`isolation.py` 延迟 import `_isolation_win32`、
`_WORKER_SCRIPT = Path(__file__).with_name("_isolation_worker.py")`）。

- 改写面：src 1 处延迟 import + worker usage 字符串 + 测试 7 条 import
  （test_isolation_adapter / channel / pilot）；**零生产接线状态不变**
  （src 内无任何其他模块 import seam）。
- 门禁：layer_matrix 由 `agentnodes.kernel.agent` 前缀覆盖（不加显式
  行，与 kernel.agent 子包既有风格一致）；semantics_placement /
  kernel_nouns / path_literals 零改动即绿；数字文档 L2 45 文件 10,563
  行 / L3 226 文件 50,299 行（-758/+758，总数不变）。
- §6/§7 执行记录中的 `apps/chat/` 路径为**历史记录**，不回写（惯例同
  M13 文档的回退留痕）；本节之后的设计引用一律用 kernel 路径。

## 11. 平台矩阵记录（2026-11-20 · WP-3 / 子批 3d）

| 平台 | 载体 | 覆盖 | 结论 |
|---|---|---|---|
| Windows（本机真机） | batch_gate.cmd 前的全量 pytest | adapter E1/E5 + IPC/审批 6 + 试点 5 真跑（真 worker / Job Object / taskkill 降级路径） | 启动 / 通信 / 取消终止 / 退出确认 / lease 顺序逐项过 |
| POSIX（ubuntu CI） | `.github/workflows/tests.yml` unit job（3.10-3.12 × 4 shard） | 平台守卫 5+2 条真跑（词表 / fail-closed / POSIX 显式拒绝 / 平台承诺 / 读超时回归 + adapter Protocol/禁启用）；行为类 win32-gated skip-with-reason | 未验证平台显式 unsupported，不静默降级 |
| Windows CI | **不存在**（全 workflow 仅 ubuntu runner） | 已知局限：Windows 矩阵记录落本机 batch_gate 运行 + 本表 | 显式记录，不冒充 CI 覆盖 |

- 平台守卫测试独立成文件 `tests/test_isolation_platform_matrix.py`
  （无 skipif，全平台跑）——修正此前守卫被整文件 win32 gate 误伤、
  在 POSIX CI 上从未运行的缺口；`test_isolation_adapter.py` 的
  per-test gate 样板保持。
- POSIX 生产启用前置：平台矩阵未验证前 `build_worker_adapter()` 在
  POSIX 返回禁启用 adapter（守卫测试钉住，3d 前置裁决维持）。