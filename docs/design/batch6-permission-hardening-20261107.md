# 批次 6 设计 · 权限加固（P1/P2/P3 + L0-L4 分级）

> **上位设计**：`phase1-definition-of-done-20261107.md` §1.3 / §3。
> 本文件是**实施设计**：精确到 file:line 的改动与验收。
> 顺序：用户拍板「先权限，后 unified-loop 收敛」。

---

## §0 结论摘要

| 项 | 修复方式 | 关键事实 |
|---|---|---|
| **P1** 永久允许盖掉默认 deny | **hard_deny 层**（决策第 1 步，不可覆盖）| bug 链：`approvals.py` respond(permanent) → `rules_io.save_rule` 追加末尾 → `evaluator` last-match-wins |
| **P2** 路径变体绕过 deny 规则 | evaluator **双匹配**（raw + normpath）| **定性收窄**：文件安全边界已被 `PathWhitelist` 把守（`legacy.py:261-273` 已有 `.resolve()` + `relative_to` 双保险，symlink 逃逸到 workspace 外会被拒）——P2 修的是**规则语义**，不是边界 |
| **P3** shell 只取首 token | **不修解析**；save_rule 拒绝 pattern 化 shell allow + L1 下 shell 全问 | 解析器差分是 CVE 高发区（调研结论），文本规则有上限 |
| 分级 | **L0-L4 模式预设**，默认 **L1** | L1 下 write/edit 允许（为 checklist E3 开路）、shell 全问 |

---

## §1 hard_deny 层（P1）

### 1.1 数据结构（schema.py 追加）

```python
@dataclass(frozen=True)
class HardDenyRule:
    """不可覆盖的拒绝规则——决策流水线第 1 步，先于一切规则匹配。"""
    permission: str          # 工具名或 "*"
    pattern: str             # fnmatch（对 target 与规范化 target 双匹配）
    reason: str              # 审计用

class PermissionMode(str, Enum):
    L0_READONLY = "L0_readonly"
    L1_WORKSPACE_EDIT = "L1_workspace_edit"   # 默认
    L2_SANDBOX_EXEC = "L2_sandbox_exec"
    L3_NETWORKED = "L3_networked"
    L4_CONFIRM_ALL = "L4_confirm_all"
```

### 1.2 三条硬约束（DEFAULT_HARD_DENY）

| # | 规则 | 覆盖 |
|---|---|---|
| **H1** 自我配置 | write/edit/delete/read on `.agentnodes/**`、`**/permissions.yaml`、`.claude/**`、`.git/hooks/**`、`.mcp.json` | agent 改自己的权限 = 自我授权（CVE 高发区）|
| **H2** 提权 | shell on `sudo`/`sudo *`/`su`/`doas`/`pkexec` | 提权后一切规则失效 |
| **H3** 凭据/元数据 | read/write on `**/.ssh/**`、`**/.aws/**`、`**/.kube/**`、`**/.env*`；web_fetch target 含 `169.254.169.254` | 凭据外泄 / 云 metadata |

**诚实边界**：H1/H3 对 **shell 重定向**（`echo x > .agentnodes/permissions.yaml`）
拦不住——那是解析器差分（P3 同源）。文本 hard_deny 是纵深防御的一层，
真正的强制要靠沙箱（P4，本阶段不做，已在 phase1 doc §9 排除）。

### 1.3 evaluate() 流水线改造（evaluator.py）

```
1. hard_deny 双匹配（raw target + normpath target）     → DENY（reason 入 decision）
2. R0 只读快路径（现状保留）
3. 规则匹配：last-match-wins（现状保留——用户规则兼容）
4. 无命中 → ASK；非交互模式（interactive=False）→ DENY
```

- `PermissionEvaluator.__init__` 新参：`hard_deny: list[HardDenyRule] | None = None`（None=DEFAULT）、
  `interactive: bool = True`、`on_decision: Callable[[dict], None] | None`（审计）
- `PermissionDecision` 加字段 `hard: bool = False`、`reason: str = ""`（向后兼容：默认值）

### 1.4 与用户规则的关系

用户 YAML 追加在 **模式预设之后**（last-match-wins 可覆盖预设的 allow/ask/deny），
但 **hard_deny 独立于 ruleset**，任何来源都改不了。这正是修 P1 的最小改动：
「永远允许」仍然有效（覆盖预设），但 H1/H2/H3 永远拦得住。

---

## §2 L0-L4 模式预设（schema.py `MODE_PRESETS`）

| 模式 | allow | ask | deny |
|---|---|---|---|
| **L0_readonly** | read/list/list_history/show_chart/show_report/tool_help | — | write/edit/shell/task/websearch/web_fetch/delete |
| **L1_workspace_edit（默认）** | L0 读组 + **write/edit** | shell/task/websearch/web_fetch | delete |
| **L2_sandbox_exec** | L1 + **shell** | task/websearch/web_fetch | delete |
| **L3_networked** | L2 + **websearch/web_fetch** | task | delete |
| **L4_confirm_all** | 读组 | 其余全部（含 delete） | — |

- `DEFAULT_RULES` = `MODE_PRESETS[L1]`（现有常量保留为别名，兼容旧 import）
- 解析链：`AGENTNODES_PERMISSION_MODE` env → `PermissionGateway(mode=...)` → 预设规则 → 用户 YAML 叠加
- **L1 下 write 允许是行为变更**（原 ask）——这是 checklist E3 的前提；
  workspace 边界仍由 PathWhitelist 把守，风险可控

---

## §3 P2 · evaluator 双匹配

`_matching_rules` 对每个规则同时匹配 `target` 与 `os.path.normpath(target)`
（反斜杠归一、`./` 前缀去除、`a/../b` 折叠）。**deny 在任一形态命中即拒；
allow 需两形态都命中**（与 Claude Code CVE-2025-59829 修复后的语义一致）。

配套（`rules_io.py`）：`save_rule` 新增校验——
**`permission == "shell"` 且 `action == allow` 时 pattern 必须为 `"*"`**，
否则 `ValueError`（附 P3 说明）。

---

## §4 非交互降级 + 审计

- `interactive=False`（构造参数）：一切 ASK → DENY（CI / `-p` 脚本模式语义，
  对齐 Gemini「非交互 ask_user 按 deny」）
- `on_decision` 回调：每次 evaluate 发
  `{"tool", "target", "action", "rule", "hard", "reason"}`；
  PermissionGateway 默认接 `logger.info`（审计留痕，流水线第 6 步）

---

## §5 测试计划（新增/改写）

| 组 | 用例 |
|---|---|
| **P1 回归** | 用户永久 allow delete（模拟 gateway respond(permanent)）→ 仍 DENY 且 `hard=True` |
| **H1** | write `.agentnodes/permissions.yaml` / `.git/hooks/pre-commit` → DENY（即使存在匹配 allow 规则）|
| **H2** | L2 模式下 `shell sudo rm` → DENY |
| **H3** | read `.ssh/id_rsa` / write `configs/.env` → DENY；web_fetch `http://169.254.169.254/...` → DENY |
| **P2** | deny `secret/**` 时，`./secret/x`、`a/../secret/x`、`secret\\x` 全部 DENY；allow `src/**` 时 `./src/a` 仍 ALLOW |
| **模式** | 五模式各断言一行矩阵（write / shell / webfetch / delete）|
| **save_rule** | shell allow + pattern `"git *"` → ValueError；pattern `"*"` → 通过 |
| **非交互** | interactive=False → 原本 ask 的全部 DENY |
| **审计** | on_decision 收到 hard deny 与规则命中两类记录 |
| **既有回归** | test_permission_evaluator / gateway / ainvoke / b1d 全绿（L1 下 write 变 allow 会破坏原「write 默认 ask」断言 → 按新契约改写）|

---

## §6 执行顺序

1. schema.py：HardDenyRule / PermissionMode / MODE_PRESETS / DEFAULT_HARD_DENY
2. evaluator.py：流水线改造 + 双匹配 + interactive + on_decision
3. rules_io.py：save_rule 校验
4. approvals.py：mode 参数接线 + 审计默认
5. 测试（§5）
6. 全量 + batch_gate + 提交

**明确不做**（本批）：P4 沙箱、shell 复合命令解析、网络域名白名单代理、
write 白名单 WorkspaceScope 改造（属 E3 能力批，下一批做）。
