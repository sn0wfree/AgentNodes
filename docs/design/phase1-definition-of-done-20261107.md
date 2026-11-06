# 第一阶段完成标志与权限设计（v1.0 · 2026-11-07）

> **本文档地位**：三步走**第一步的真正验收标准**。
> 取代 `three-phase-roadmap-20261106.md` §3.2 的 A1–A4——
> A1–A4 只覆盖代码层静态检查，实测证明它们**不足以判定「可用」**。
>
> **触发原因**：用户指出「我不认为三步走第一步已经完成了」，实测确认
> `write` 只能写 4 个量化目录（`strategies/templates/memory/logs`）→
> **coding 能力实际不存在**；`shell` 默认关闭 → **执行代码默认不可用**。
>
> **依据**：两份业界调研（5 个 coding agent 产品 + 8 个产品的权限模型），
> 结论见 §2 / §3，出处见 §8。

---

## §0 结论摘要

**第一步完成 = 一个通用 agent 开箱可用：能对话、能执行代码、能写代码文件。**

三条判定线：

| # | 判定 | 当前 |
|---|---|---|
| **D1** | 14 条可测 checklist 全过 | 实测 3/5 核心项失败 |
| **D2** | 权限分级 L0–L4 落地，默认 L1 | 无分级；有 4 个权限缺陷 |
| **D3** | 删掉 `apps/` 后 D1/D2 仍全过 | 未验证 |

**不做的事**（明确排除）：
- OS 级沙箱（bubblewrap / Seatbelt）——跨平台成本高，Windows 是短板
  （Claude Code 在原生 Windows 上都不沙箱）。留 P2。
- 网络白名单代理——同上，且需要内核级能力。
- LSP / AST / repo map——调研证实**没有一家用 LSP**做代码理解，
  Aider 的 tree-sitter 只用于选文件。不进入第一阶段。

---

## §1 现状：实测的三个阻塞点

### 1.1 write 只能写 4 个量化目录

```
$ python -c '...write("hello.py")...'
{"status":"error",
 "error":"write path 'hello.py' is not under any allowed write root
          (strategies, templates, memory, logs)"}

$ python -c '...write("<abs>/abs.py")...'
{"status":"error","error":"absolute paths are not allowed"}
```

根因：`kernel/agent/sandbox/legacy.py:137`

```python
DEFAULT_WRITE_ROOTS = ("strategies", "templates", "memory", "logs")
DEFAULT_READ_ROOTS = ("strategies", "templates", "memory", "logs",
                      "data", "docs", ".")
```

这是**量化研究工作区约定**。通用 coding agent 要写 `src/main.py` /
`tests/test_x.py` / `README.md` / `pyproject.toml` —— 全部被拒。

> **结论：coding 能力在当前系统里不存在。** 不是弱，是没有。

### 1.2 shell 默认关闭

`build_chat_agent_loop(allow_shell_tools=False)` —— 默认值。
`chat_loop.py:38` 的注释说明这是有意的（研究 agent 不该随便跑命令），
对通用 agent 是致命的。

### 1.3 权限系统的 4 个缺陷

| # | 缺陷 | 位置 | 后果 | 严重度 |
|---|---|---|---|---|
| **P1** | last-match-wins 让「永远允许」盖掉默认 deny | `rules_io.py:112`（追加末尾）+ `evaluator.py:173`（最后匹配胜出）| 用户在删除提示点"永远允许"→ 生成的 allow 规则盖掉 `DELETE_FILE=deny` | 🔴 高 |
| **P2** | 无路径规范化 | `evaluator.py:233` 只做 `** → *` | 无 `realpath`、无 symlink 解析 → 符号链接绕过 | 🔴 高 |
| **P3** | shell 只取第一个 token | `evaluator.py:227` `cmd.split()[0]` | `rm -rf /` 与 `rm foo.txt` target 都是 `rm`；复合命令失配 | 🟡 中 |
| **P4** | `StaticSandbox` 是 stub | `static_sandbox.py:71` | 执行/网络都 `NotImplementedError`，无真正的隔离 | 🟡 中（本阶段不修，靠 L0–L4 分级兜） |

**P1 是最严重的**——它让安全默认值形同虚设。

---

## §2 第一阶段 checklist（D1）

14 条，全部可测。每条标注**当前状态**。

| # | 验收 | 测法 | 当前 |
|---|---|---|---|
| C1 | 多轮记忆 | 同一 session 说"记住 42"→ 问"数字"→ 答 42 | ⬜ 待验 |
| C2 | 会话恢复 | 杀进程 → resume → 仍答 42，历史条数一致 | ⬜ 待验 |
| C3 | 流式输出 | 首 token 在 T 秒内出现 | ⬜ 待验 |
| C4 | 读文件带行号 | 读 3000 行文件 → 有行号 + 标注截断范围；offset 能读到第 3000 行 | ⬜ 待验 |
| C5 | 搜索 | grep 返回 `file:line:content`；`.gitignore` 的文件不出现 | ⬜ 待验 |
| C6 | **编辑唯一性** | 非唯一 `old_string` → 工具报错，**文件 sha256 与编辑前一致** | ⬜ 待验 |
| C7 | **读后写保护** | 未读文件直接编辑 → 拒绝；非 UTF-8 → 拒绝且不截断 | ⬜ 待验 |
| C8 | 命令三元组 | `echo out; echo err 1>&2; exit 3` → 分别拿到 stdout/stderr/exit_code=3 | ✅ 已验证 |
| C9 | **超时与清理** | `sleep 600` → 默认超时被杀，**无孤儿进程** | ⬜ 待验 |
| C10 | 后台任务 | 返回 handle；能轮询增量输出；能 kill | ⬜ 待验 |
| C11 | **权限闸门** | 默认模式 `rm -rf` 必须弹审批；拒绝后**文件系统无变化**；allowlist 模式免问 | ⬜ 待验 |
| C12 | 自动压缩 | 塞满上下文至阈值 → 触发压缩 + 事件；**压缩后项目指令文件仍在上下文** | ⬜ 待验 |
| C13 | 中断 | Ctrl+C → 2s 内停止、无孤儿、session 仍可 resume | ⬜ 待验 |
| C14 | 可脚本化 | `-p "任务" --output-format json` → 合法 JSON（final text + tool calls + usage），退出码正确 | ⬜ 待验 |

**另外三条「能力可用性」验收**（用户原话的直接翻译）：

| # | 验收 | 测法 |
|---|---|---|
| **A-chat** | 能对话 | C1 + C2 + C3 |
| **A-exec** | 能执行代码 | C8 + C9 + C10 |
| **A-coding** | 能写代码 | C4 + C6 + C7 + **写任意工作区路径** |

---

## §3 权限分级设计（D2）

### 3.1 五级（L0–L4），不加 L5

调研建议 L0–L5，但 **L5（完全放行）在第一阶段不提供** —— 它要求
「无凭据的一次性 VM + egress 代理 + 审计日志」三个前提，我们都不具备。
**没有前提的 bypass 模式 = 事故制造机。**

| 级别 | 名称 | 自动放行 | 需确认 | 禁止 |
|---|---|---|---|---|
| **L0** | 只读观察 | read / list / grep / glob（工作区内）| — | 写、执行、联网 |
| **L1** | 工作区编辑（**默认**）| L0 + 工作区内 write/edit | shell | 工作区外、`.git`/`.env`/`.ssh` |
| **L2** | 沙箱执行 | L1 + shell（**限工作区内命令**）| 联网、工作区外路径 | 提权、改自身配置 |
| **L3** | 受控联网 | L2 + 白名单域名 | 任意域名、`git push` | 上传、生产发布 |
| **L4** | 人工确认 | 无（全问） | 一切（展示完整命令 + diff）| 提权、改配置、force push |

**默认 L1** 的理由：与 Claude Code 的 `default`、Codex 的 `read-only` 对齐；
开箱即用（能改代码），但 shell 需确认（能力爆炸）。

### 3.2 三条任何级别都不放开（hard_deny）

| # | 禁止 | 理由 |
|---|---|---|
| **H1** | agent 写自己的权限配置 / hooks / MCP 配置 | 否则等于自我授权（业界 CVE 高发区）|
| **H2** | 提权（`sudo` / `su` / `doas` / `pkexec`）| 提权后一切规则失效 |
| **H3** | 读云 metadata（`169.254.169.254`）与私钥目录（`~/.ssh` / `~/.aws` / `~/.kube`）| 凭据外泄 |

**hard_deny 不是规则表里的一行，是决策流水线的第 1 步**——它不可被
任何后续规则覆盖。这是修 P1 的方法。

### 3.3 决策流水线（修 P1/P2/P3 的核心）

```
1. hard_deny 检查（不可覆盖）              → deny
2. 路径规范化 + symlink 双重解析            → 越界则 deny
   - realpath(strict=False) 后再比对
   - 请求路径 OR 解析后路径，任一命中 deny 即拒
3. 规则匹配：deny > ask > allow（类别优先）  → 命中规则
4. 无命中 → 默认 ask（非交互场景 → deny）
5. ask 批准 → 仍需过分级约束（人批准 ≠ 解除限制）
6. 记审计（工具 / 参数 / 决策 / 规则来源）
```

**与现状的关键差别**：

| 项 | 现状 | 新设计 |
|---|---|---|
| 优先级 | last-match-wins | **deny > ask > allow**（类别优先）|
| hard_deny 层 | 无 | **有**（第 1 步，不可覆盖）|
| 路径处理 | `fnmatch` 原始字符串 | **realpath + symlink 双重解析** |
| shell target | `cmd.split()[0]` | **保留首个 token，但标注为已知局限**（§3.5）|
| 兜底 | ask | ask（非交互 → deny）|

### 3.4 为什么不改 last-match-wins 为 first-match

调研显示业界两派：Claude Code 是 `deny > ask > allow` 类别优先，
opencode/我们是 last-match-wins。**last-match-wins 本身不是错的**
（它允许"窄 allow 覆盖宽 deny"），错的是**没有不可覆盖的 deny 层**。

所以最小改动是**加 hard_deny 层**，而不是换整个匹配语义。
这样：
- 用户已有的 `permissions.yaml` 全部继续有效
- 「永远允许」仍然能用（覆盖 DEFAULT_RULES 的普通规则）
- 但 hard_deny 里的三条永远拦得住

### 3.5 shell 的已知局限（诚实记录）

`_extract_target` 取 `cmd.split()[0]` 的局限（P3）：

```
"rm -rf /"        → target="rm"
"rm foo.txt"      → target="rm"
"cd / && rm -rf x" → target="cd"    ← 复合命令完全失配
"echo x | sed -i" → target="echo"   ← 管道失配
```

**本阶段不修复合命令解析**（需要用真 shell 解析器，工作量大）。
处置：
1. **所有 `shell` 调用默认走 L1 = ask**（不自动放行）——这是真正的防线
2. 在 `permission` 配置里**不允许写基于 pattern 的 shell allow 规则**
   （arity 检查：`permission: shell` 只能配 `pattern: "*"`），
   否则会给人"我已经限制了"的错觉
3. 文档写明：**shell 的安全边界靠分级（L1=全问），不靠规则匹配**

---

## §4 write/read 路径白名单改造

### 4.1 现状问题

`DEFAULT_WRITE_ROOTS = ("strategies", "templates", "memory", "logs")`
是量化研究工作区约定。

### 4.2 方案：默认开放工作区根，`write_roots` 保留收紧能力

```python
class WorkspaceScope(Enum):
    """写盘范围。默认 WORKSPACE（通用 agent），量化插件可收紧到 PATHS。"""
    WORKSPACE = "workspace"   # 整个 workspace（`..` 逃逸仍拒）
    PATHS = "paths"           # 只允许 write_roots 里的子目录（旧行为）
```

- `ToolContext.write_scope` 默认 `WORKSPACE`
- `write_scope=PATHS` 时用 `write_roots`（量化研究插件显式传）
- **绝对路径**：允许，但必须 `resolve()` 后仍在 workspace 内
- `..` 逃逸：仍拒（`resolve()` 后越界即拒）

**为什么不是「加 src/tests/docs 到白名单」**：那还是在猜用户要写哪。
通用 agent 的边界应该是**工作区本身**，不是某个目录清单。

### 4.3 安全边界不变

| 检查 | 现状 | 新设计 |
|---|---|---|
| `../` 逃逸 | 拒 | **拒**（不变）|
| 绝对路径 | 一律拒 | **允许但必须在 workspace 内** |
| symlink 穿越 | 未处理 | **realpath 后比对**（新增）|
| `.git` / `.env` / `.ssh` | 未特殊处理 | **hard_deny**（新增）|

---

## §5 shell 默认开启

`build_chat_agent_loop(allow_shell_tools=False)` → **改为 True**。

但配套：
- shell 在 **L1 下仍需确认**（默认级别 L1）
- 用户显式设 L2 才自动
- `allow_shell_tools=False` 保留为**显式关闭**选项（供需要纯对话的场景）

---

## §6 门禁与验收扩展

### 6.1 `check_kernel_nouns.py` 扩展

当前只扫 `.py`。扩展到：

| 类型 | 扫描方式 |
|---|---|
| `.py` | AST（现状）|
| `.yaml` / `.yml` / `.json` | 文本 + 词表切词 |
| **目录名** | 目录名切词（`scheduled_research` → `scheduled` + `research`）|

**首轮会抓到**：
- `kernel/swarm/presets/*.yaml` —— 37 个量化编排模板
- `kernel/scheduled_research/` —— 目录名本身

### 6.2 新增 `check_capability_e2e.py`

14 条 checklist 的可测形式。每条一个断言，不通过即 FAIL。
**这个门禁是「第一步完成」的唯一判据**——静态检查全绿但 e2e 挂，
就不算完成。

### 6.3 新增「删 apps 测试」

```
tests/test_kernel_standalone.py
  - 临时把 sys.modules 里的 agentnodes.apps.* 全部移除
  - 构建 chat agent → 能读能写能执行
  - 断言：不依赖任何 apps 模块
```

**这是「通用平台」的唯一硬证明。**

---

## §7 执行顺序

| 序 | 动作 | 依赖 |
|---|---|---|
| 1 | 修 P1（hard_deny 层）+ P2（路径规范化）| — |
| 2 | `WorkspaceScope` 改造（write/read 白名单）| — |
| 3 | shell 默认开启 + L0–L4 分级落地 | 1 |
| 4 | `check_kernel_nouns` 扩展到 yaml/json/目录名 | — |
| 5 | 37 个 presets + `scheduled_research` 迁 L2 | 4 |
| 6 | 14 条 checklist 写成 `check_capability_e2e.py` | 2, 3 |
| 7 | `test_kernel_standalone.py`（删 apps 测试）| 5, 6 |
| 8 | 全量验证 + batch_gate + 架构图 | 全部 |

**批内可并行**：1 + 2（独立）∥ 4（独立）。

---

## §8 调研出处（关键几条）

| 结论 | 出处 |
|---|---|
| Claude Code 权限模式（`default`/`acceptEdits`/`plan`/`auto`/`dontAsk`/`bypassPermissions`）| code.claude.com/docs/en/permission-modes |
| `deny > ask > allow` 类别优先，deny 在任何模式都生效 | code.claude.com/docs/en/permissions |
| Codex `read-only`/`workspace-write`/`danger-full-access` × `on-request`/`never` | github.com/openai/codex docs/sandbox.md |
| 沙箱实现：Seatbelt / bwrap+seccomp / Landlock | 同上 + linux-sandbox/README |
| **没有一家用 LSP**做代码理解 | 5 产品功能矩阵实测 |
| 黑名单不安全，白名单也不够，真正安全的是「能力最小化 + 沙箱」| Claude Code security 页原话 |
| 所有公开 CVE 几乎都是解析器差分绕过文本规则 | NVD 检索 Claude Code / Codex |
| `dontAsk`：凡会弹窗的一律拒绝（CI 该抄的语义）| permission-modes |
| 人批准 ≠ 解除沙箱（`dangerouslyDisableSandbox` 需单独确认）| sandboxing 文档 |
| 超时 deny / 非交互 ask 降级为 deny | Gemini policy-engine + Claude PermissionGateway |

**查不到的**（调研明确标注）：
- Claude Code 自动压缩的触发百分比（官方只说"depends on model and configuration"）
- Codex 命令输出的默认截断字节数（源码有常量但未查到值）
- meta "Agents Rule of Two" 原文（网络不可达）

---

## §9 明确不做（第一阶段）

| 项 | 理由 |
|---|---|
| OS 级沙箱（bwrap / Seatbelt）| 跨平台成本高，Windows 无成熟方案；靠 L0–L4 分级兜 |
| 网络白名单代理 | 需内核级能力，P2 |
| LSP / AST / repo map | 调研证实无一家需要 |
| checkpoint / undo | 加分项，非可用必需 |
| hooks 系统 | 加分项 |
| MCP | 加分项 |
| 会话 fork | 加分项 |
| `bypassPermissions` / L5 | 缺少前提（一次性 VM + egress 代理 + 审计）|
