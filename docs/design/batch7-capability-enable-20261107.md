# 批次 7 设计 · E2/E3 能力启用 + ASK fail-open 修复

> **上位设计**：`phase1-definition-of-done-20261107.md`（E1–E6 验收）。
> 本批 = 步 1-A（write 白名单）+ 步 1-B（shell 入 registry）
> + 顺带修实现中发现的第三个权限洞。
> 权限分级地基（批次 6，`8300a557`）已就绪。

---

## §0 结论摘要

| 项 | 改动 | 验收 |
|---|---|---|
| **E3 coding** | `DEFAULT_WRITE_ROOTS` 4 目录 → `(".",)` 整工作区 | `write("hello.py")` 成功 |
| **E2 执行代码** | chat 默认 `allow_shell_tools=True`；AgentLoop 默认接权限评估器 | shell 在 registry 且经 L 分级判定 |
| **安全洞 #3** | ASK 无 gateway：**fail-open → fail-closed** | ask 且无提问通道 → DENY（可操作报错）|
| **模式默认** | CLI 无提问通道 → 默认 **L2**；web 路由保持 L1 + SSE gateway | 两条路径都可用且都有 hard_deny |

---

## §1 E3 · write 白名单（sandbox/legacy.py:137）

现状：`DEFAULT_WRITE_ROOTS = ("strategies", "templates", "memory", "logs")`
——量化工作区约定，`write("hello.py")` 被拒（已实测）。

**改动**：`DEFAULT_WRITE_ROOTS = (".",)`。

- 读根 `DEFAULT_READ_ROOTS` **本就含 `"."`**（读已是全工作区）——写的限制是孤例
- `..` 逃逸仍拒（`_resolve_under_workspace` 的 `relative_to` 检查，实测过）
- 绝对路径仍拒（`_validate_basic`）
- **收紧能力保留**：`ToolContext.write_roots` 显式传参仍生效
  （量化插件未来收紧到 `("strategies",...)` 与旧行为一致）
- 配套：`file_tools.py:351/106` 的错误文案同步（不再列 4 目录）

## §2 E2 · shell 入 registry（chat_loop.py）

- `allow_shell_tools` 默认 `False → True`（显式传 False 仍可关闭——纯对话场景）
- **权限不缺位**：AgentLoop 新增 `permission_mode` 参数，构造时建
  `PermissionEvaluator` 挂到 `self._permission_evaluator`
  （`loop_tools.py:75` 的 getattr 消费点**早已存在**，只是从来没人赋值）。

## §3 安全洞 #3 · ASK fail-open → fail-closed（tools.py:684-692）

现状：ASK 且 `ctx.permission_gateway is None` → **当 allow 放行** + warning。
且 CLI 路径从不接 gateway ⇒ **今天 CLI 的所有 ask 规则实际都在放行**。
（更糟的是这个行为有测试钉着：`test_ask_without_gateway_keeps_documented_fail_open`。）

**修复**：ASK 无 gateway → `PermissionDeniedError`，报错带操作指引
（设 `AGENTNODES_PERMISSION_MODE=L2_sandbox_exec`，或接入 gateway）。
与既有测试 `test_ask_with_gateway_denied_on_sync_path`（同步路径拒绝）同语义。

## §4 模式默认的两条路径（诚实标注差异）

| 路径 | 默认模式 | 提问通道 | 理由 |
|---|---|---|---|
| **CLI / TUI chat** | **L2_sandbox_exec** | 无（TUI prompter 未建）| ask 不可用（fail-closed 后会全拒）；L2 = shell 自动 + hard_deny 兜底，对齐 Codex trusted→workspace-write 先例 |
| **web chat 路由** | **L1_workspace_edit** | SSE gateway（已有）| 每次问真用户 |

- 解析链：`AGENTNODES_PERMISSION_MODE` env 显式设值 > 路径默认
- **TUI 交互式 prompter 是后续项**（C11 的 UI 半边）；落地后 CLI 可回 L1
- hard_deny 三约束在任何路径、任何模式都生效（批次 6）

## §5 执行顺序

1. legacy.py 写根 + file_tools 文案
2. tools.py fail-closed + b1d 测试改写
3. chat_loop allow_shell_tools + AgentLoop permission_mode 接线
4. `tests/test_capability_smoke.py`：E1–E5 五条端到端
5. 全量 + batch_gate + 提交

**不做**（本批）：TUI prompter、网络域名白名单、E6 删 apps 测试
（等 1-C/D 迁移后）、E7–E14 全 checklist（逐批）。
