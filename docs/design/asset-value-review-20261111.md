# 三项资产价值评估：权限系统 / 架构门禁 / 引擎-decider（2026-11-11）

> **性质**：调研（实测评估，不改代码）
> **触发**：用户要求「权限系统 + 架构门禁 8 道 + 引擎/decider 架构 这三者评估一下是否有价值」
> **方法**：所有结论都来自**注入违规**或**真实调用链**的实测，不采信「测试全绿」「代码存在」
> **结论摘要**：权限系统有价值但有 3 个实测可达的绕过（1 个 P0）｜门禁 7/8 真拦，1 处可验证盲区｜
> 引擎/decider **价值存疑**（规模倒挂，收益不可度量）

---

## §0 三项资产的基本盘

| 项 | 代码规模 | 落地时间 | 生产接线 |
|---|---|---|---|
| 权限系统 | `kernel/permission/` 4 文件 **1086 行** | 2026-11-07 ~ 11-10（5 个 commit）| `enforce_permission_sync` 在 `loop_tools.py:170`、`tools.py:909` 真实调用 |
| 架构门禁 | `scripts/arch/` **13 个脚本**，batch_gate 串 8 步 | 持续演进 | `scripts/batch_gate.cmd` |
| 引擎/decider | `loop_engine.py` 339 + `loop_engine_adapters.py` 371 + `deciders.py` 281 + `modes.py` 159 = **1150 行** | 批次 9–13（2026-11-07 ~ 11-09）| `arun()` 默认走引擎；`src/` 有 4 处 `.arun(` |

作为对照：被引擎取代的 legacy 引擎 `_run_loop_core` 是 **246 行**（`loop.py:1006-1251`，AST 实测）。

---

## §1 权限系统

### 1.1 架构上确实对的地方

1. **`hard_deny` 独立于 ruleset**（`evaluator.py:128-132`）——用户 YAML、永久允许都改不了它。
2. **读取层/诊断层分离**——同 E-2 的设计一致。
3. **ASK fail-closed**（`c0836ee4`）——旧行为「无 gateway 时当 allow」是安全洞，已封。
4. **sync 路径补闸**（`30505c9f`，P0）——旧实现 sync 直调 `tool.invoke`，权限闸只在 `ainvoke` 里，
   实测「evaluator 明确 deny shell，sync 仍执行了 `echo hi`」。这条是**真实漏洞的修复**。
5. **`shell` 的 target 用整条命令串**（`_extract_target`，批次 13 附录 P0）——旧实现取
   `cmd.split()[0]`，导致 `**/.ssh/**` 对 `cat ~/.ssh/id_rsa` 永不匹配。

**结论：架构方向是对的，且修过真漏洞。**

### 1.2 实测出的 3 个绕过

#### 缺陷 1（P0）：`read .env` 读出密钥

实测（`tmp/probe/probe_env_glob.py`，真实 `tool.ainvoke` 调用链）：

```
path='.env'        -> allow   ← 漏，执行了
path='./.env'      -> deny    hard=True
path='sub/.env'    -> deny    hard=True
path='<abs>/.env'  -> deny    hard=True
```

真实工具调用结果（带 workspace 的 ToolContext）：

```
path='.env'    执行了  泄漏=True   out='{"status": "ok", "path": "C:\\...\\perm-env-...\\.env"'
path='./.env'  拦下    PermissionDeniedError: Permission denied: read pattern=./.env
```

**根因**：`hard_deny` 的规则是 `**/.env`，但 `_glob_match("**/.env", ".env") == False`
——`**/` 要求目录前缀。

同样的漏洞影响 ssh key：

```
'**/.ssh/**' vs '.ssh/id_rsa'         -> False   ← 漏
'**/.ssh/**' vs '/home/u/.ssh/id_rsa' -> True
```

**为什么危险**：`read .env` 是模型最自然的写法，而它恰好是唯一被放过的形态。

#### 缺陷 2：`webfetch` 与枚举名 `web_fetch` 不一致

```
Permission.WEB_FETCH.value = 'web_fetch'
registry 真实工具名         = 'webfetch'

evaluate('web_fetch', {url: metadata}) -> deny  hard=True    ← 枚举名才拦
evaluate('webfetch',  {url: metadata}) -> ask   hard=False   ← 真实名只 ASK
```

云 metadata 的 hard_deny（`169.254.169.254`）**对真实工具名不生效**。

#### 缺陷 3：`run_bg_command` 不在 `Permission` 枚举里

registry 18 个工具中，**12 个不在枚举**：

```
registry:  git_diff, list, list_history, list_skills, options_pricing, read,
           read_document, run_bg_command, shell, show_chart, show_report,
           skill, task, todowrite, tool_help, webfetch, websearch, write
枚举值:    read, write, edit, delete_file, shell, web_fetch
```

`_extract_target("run_bg_command", {"command": "sudo rm -rf /"})` 返回 **`"run_bg_command"`**
（不是命令串），于是 `sudo` 规则完全不生效，只落到 `ask`。

`run_bg_command` 是能起后台进程的工具——正是最该拦 `sudo` 的地方。

### 1.3 测试为什么没抓到

`tests/test_permission_hardening.py` / `test_permission_evaluator.py` / `test_permission_gateway.py`
共 **64 passed**，但它们测的 target 全是 `/abs/path/.env` 或 `config/.env`，**没有一条测裸相对路径
`.env`**，也没有一条用 registry 里的真实工具名。

**这与 `.rules/lessons.md` 记录的 V4 教训同构**：测试照假设的调用形态写，不照真实调用形态写。

### 1.4 判词

**有价值，但必须修缺陷 1。** 架构保留；缺陷 1 是 P0，缺陷 2/3 是 P1。

---

## §2 架构门禁 8 道

### 2.1 实测方法

不只看「现在 0 违规」，而是**注入一条违规，看门禁会不会红**。

### 2.2 结果

| 门禁 | 注入内容 | 基线 | 注入后 | 判定 |
|---|---|---|---|---|
| `check_env_access` | kernel 层 `os.environ.get("AGENTNODES_X")` | rc=0 | **rc=1**（报文件行号）| ✅ 真拦 |
| `check_kernel_nouns` | `"run_backtest_probe"` | clean | **rc=1**（3 hits）| ✅ 真拦 |
| `check_dangling_imports --layers` | L2 反向 import L4 | 0 | **rc=1** | ✅ 真拦 |
| `check_path_literals` | `Path(__file__).parent / "core" / "x"` | 0 | **rc=1** | ✅ 真拦 |
| `check_path_literals` | `"src" / "agentnodes" / "api"` | 0 | **rc=1** | ✅ 真拦 |
| `check_path_literals` | `".quantnodes"`（精确字符串）| 0 | **rc=1** | ✅ 真拦 |
| `check_path_literals` | **`".quantnodes/probe"`** | 0 | **rc=0** | ❌ **盲区** |
| `check_path_literals` | **`".quantnodes/research/x.db"`** | 0 | **rc=0** | ❌ **盲区** |

（`check_capability_requires` / `check_semantics_placement` / `check_tool_refs` / `graph_layer_audit`
本次只跑了 `--selftest` 与基线，未逐条注入。）

### 2.3 盲区根因

```python
# scripts/arch/check_path_literals.py:54-57
PATTERN_BRAND_DIR = re.compile(
    r'["\']\.quantnodes(-research)?["\']'      # 要求字符串**恰好**是 .quantnodes
    r'|["\']\.quantnodes_[a-z_]*\.db["\']'
)
```

`".quantnodes/probe"` 里 `.quantnodes` 后面还有 `/probe`，正则不匹配。

**规模**：`src/` 里 `.quantnodes/` 共命中 **56 处**（其中非注释非 docstring 的 **18 处**——
多为 CLI help 文本与用户提示，但门禁对它们一律无感）。

### 2.4 判词

**8 道门禁中 7 道真正能拦，1 道有可验证盲区。整体价值成立。**
`check_path_literals` 需要补 `\.quantnodes/` 形态（一行正则）。

**这次评估的方法本身也值得沉淀**：不看「0 违规」，看「注入违规后会不会红」。

---

## §3 引擎 / decider

### 3.1 支持它的证据

1. `arun()` 默认走引擎（`loop.py:944-962`），`src/` 有 4 处 `.arun(` 调用，
   其中 `interfaces/api/session/service.py:1008` 是生产主路径。
2. `deciders_for` 的闸位映射表（`modes.py:100-113`）来自**实测**（批次 10 §3），
   7 个槽位 → 3 个闸位，与 legacy 一一对齐。
3. `tests/test_engine_parity.py` + `test_loop_engine_equivalence.py` + `test_agent_loop.py`
   共 **56 passed**。

### 3.2 反对它的证据

1. **规模倒挂**：四件套 **1150 行**，它替代的 legacy 引擎 **246 行**。多写 **904 行**。

2. **收敛从未完成**：`run()`（sync）走 legacy、`arun()`（async）走引擎，两条路径并存至今。
   V4 想删 legacy，整体回退。**现状是两套引擎都在，都要维护。**

3. **`loop_engine_adapters.py` 371 行**几乎全是对 `AgentLoop` 方法的转发。
   引擎不知道业务，这不是抽象而是**接线层**——它意味着引擎没有真正「接管」循环，
   只是把循环体搬进了适配器。

4. **`LoopSpec` 284 行 + `to_legacy_kwargs` 零调用**——这条抽象线上有死代码。

5. **`_finalize` 语义「两处都不能删」**（`loop_engine_adapters.py:356-359` 注释自己承认）
   ——抽象的成本落在「记住哪里不能删」上。

### 3.3 判词

**价值存疑。** 设计意图（可测的闸位、可插拔的停止策略）是对的，
但**没有带来可度量的收益**：

| 维度 | 预期 | 实测 |
|---|---|---|
| 减少代码 | — | 1150 vs 246，**净增 904 行** |
| 减少路径 | 收敛到一条 | 两条并存 |
| 修已知缺陷 | — | EXIT 路径仍缺 `trace_path`/`metrics` |
| 可测性 | 闸位可插拔 | `deciders` 映射表只 60 行，不需要 1150 行脚手架 |

**它更像一个已投入但未验证的中间态，而不是成果。**

---

## §4 三者对比

| 项 | 有价值？ | 证据强度 | 处置建议 |
|---|---|---|---|
| 权限系统 | **是，但有 3 个绕过** | 强（实测读到密钥）| 保留 + 立即修缺陷 1（P0）|
| 架构门禁 | **是** | 强（注入测试 7/8 拦住）| 保留 + 补 `check_path_literals` 正则 |
| 引擎/decider | **存疑** | 弱（无量化收益，规模倒挂）| **不动**，记录「未收敛」状态 |

---

## §5 建议的处置顺序

按 R-014（Loop 结构变更前先给方案），下列每一项动手前都要先出方案。

| 优先级 | 项 | 性质 | 触发规则 |
|---|---|---|---|
| **P0** | `hard_deny` 裸相对路径不匹配（缺陷 1）| 安全修复 | 不触发 R-014（非 loop 结构）|
| P1 | `run_bg_command` 不在枚举 + `_extract_target` 无分支（缺陷 3）| 安全修复 | 同上 |
| P1 | `web_fetch` / `webfetch` 名字归一（缺陷 2）| 安全修复 | 同上 |
| P2 | `check_path_literals` 补 `\.quantnodes/` 形态 | 门禁补强 | 同上 |
| — | 引擎/decider 收敛与否 | 架构决策 | **R-014 + R-015**（需先定「删哪条」，且接线+测试齐备）|

---

## §6 本次评估的元教训

1. **「测试全绿」不能证明安全**：权限 64 条测试全绿，仍有 3 个绕过——因为测试的调用形态
   与真实的调用形态不同（绝对路径 vs 裸相对路径、枚举名 vs registry 名）。

2. **门禁的有效性要注入验证**：只看「0 违规」无法区分「没问题」与「看不见」。
   本次注入 8 次，7 次拦住、2 次（同一门禁）未拦住。

3. **代码量与收益要对照**：1150 行 vs 246 行是个必须回答的问题。
   如果答案是「为了可测试性」，那要说清测出了什么 246 行版本测不出的东西。

---

## §7 修复记录（2026-11-11 当日回填）

§1.2 的三个缺陷 + `.aws/.kube` 同根问题**已全部修复**。流程按 R-010：
先落 `tests/test_permission_real_shapes.py`（13 条，7 条 strict xfail 钉红），
再修实现，XPASS 强制摘 marker。

| 缺陷 | 修法 | 落点 |
|---|---|---|
| 1（P0）裸相对路径 | `_glob_match` 主匹配失败且模式以 `*/` 开头时，回退匹配去前缀形态 | `evaluator.py` |
| 1 同根 | `.aws/credentials` / `.kube/config` 裸形态随之修复（通用修，非逐条手补） | 同上 |
| 2 `webfetch` 名字 | `evaluate()` 入口 `_TOOL_ALIASES = {"webfetch": "web_fetch"}` 归一 | `evaluator.py` |
| 3 `run_bg_command` | ① 枚举加 `RUN_BG_COMMAND`；② `_extract_target` 命令分支纳入；③ H2 提权 6 条规则为 `run_bg_command` 镜像 | `schema.py` + `evaluator.py` |

**修正一处评估错误**：§1.2 原文说 `**/.ssh/**` 对裸 `.ssh/id_rsa` 也有洞——**不准确**。
`schema.py:112` 早有裸形态规则 `.ssh/**`（当年手工补的），ssh 一直是拦的。
这条恰好证明逐条手补补不完（.ssh 补了，.env/.aws/.kube 漏了），通用修法在 `_glob_match`。

**修复后实测**：

- 新测试文件：**13 passed / 0 failed**（4 条前提 + 7 条原 xfail 转正 + 2 条 E2E 回归网）
- 全量：**7167 passed / 114 skipped / 0 failed**（94.8s，`-n 12`；净增 13 条）
- batch_gate：**ALL GREEN**（7281 collected ≥ anchor 7178）
- 架构图：kernel 40.3k → 40.4k（+36 行），`sync_architecture_diagram.py --write` 已回写

**未修的相邻问题（记录，不在本批）**：

- `shell` 整串 target 对无斜杠的裸命令（`cat .env`）仍不拦——需要子串/分词语义，
  是另一个设计决策（H2 的 `sudo *` 形态已覆盖首词提权）；
- `run_bg_command` 在 L2 预设里仍走 ASK（shell 是 ALLOW）——预设行为变更，超出安全修复范围；
- `check_path_literals` 的 `.quantnodes/probe` 盲区（§2.3）——待补正则。
