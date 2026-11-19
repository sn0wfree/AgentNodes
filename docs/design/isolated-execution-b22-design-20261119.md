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