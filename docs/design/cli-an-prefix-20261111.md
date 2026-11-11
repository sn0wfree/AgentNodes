# 批 C · CLI 参数改名（`--an-` 前缀，旧名 hidden alias 一个版本）

> **本文档先行**。上游：`param-naming-unification-20261109.md` §3 批 C 原设计
> （"77 个参数中识别平台配置子集约 20 个加 `--an-` 前缀"——数字是批 A/B 时代
> 的盘点，本批先重新盘点再定名）。

---

## §0 结论

| 项 | 数字 |
|---|---|
| CLI `add_argument` 总数（interfaces/cli/** 实测） | **70** |
| 加 `--an-` 前缀的参数名 | **4**（workspace / goal-db / hypotheses-path / static-dir）|
| 改动点 | **11 处**（4 个子命令） |
| 旧名处置 | **hidden alias**（help 里不可见、可解析、用时 stderr 告警一次）|
| 保留原名不动 | `--llm-*` 11 个（已有 llm- 命名空间）、`--host/--port/--reload/--transport/--api-only`（临时服务选项）、全部业务参数 |

一句话判据：**参数映射的是 `AGENTNODES_*` 环境变量 / 平台数据状态 → 加
`--an-`；参数只描述"这次进程怎么跑"或"业务上要什么" → 不动。**

---

## §1 现状盘点（70 处 add_argument 的三分法）

| 类 | 参数 | 处置 |
|---|---|---|
| **平台数据状态**（映射 env）| `--workspace`(-w) / `--goal-db` / `--hypotheses-path` / `--static-dir` | **改名**（§3）|
| **LLM 单次覆盖** | `--llm-model/base-url/temperature/max-tokens/top-p/timeout/max-retries/seed/stream/no-stream/profile/list-profiles/config`（12 个）| **保留**——已有 `llm-` 命名空间，批 C 要解决的问题（平台 vs 业务混淆）它不存在；再加 `--an-` 是纯 churn。**偏离原设计文档"约 20 个"的原因**：原数字把 llm 家族算进了平台子集 |
| **临时服务选项** | `--host` / `--port` / `--reload` / `--transport` / `--api-only` | **保留**——描述"这次进程怎么跑"，不映射任何平台状态 |
| **业务参数** | `session_id` / `query` / `name` / `run_id` / `task` / `--strategy` / `--seed` / `--hours` / `--limit` / `--recent` / `--category` / `--use` / `--add-key` / 位置参数 `path` / `input` … | **不动**（批 C 的保护对象）|

---

## §2 改名表（4 名字 × 4 子命令 = 11 处）

| 子命令 | 旧名（→ hidden alias）| 新名（canonical，进 help）| dest（不变）|
|---|---|---|---|
| `swarm run` | `--workspace`（`-w` 保留为 canonical 的可见短选项）| `--an-workspace` | `workspace` |
| `api serve` | `--workspace`(-w) / `--goal-db` / `--hypotheses-path` | `--an-workspace` / `--an-goal-db` / `--an-hypotheses-path` | 同名去连字符 |
| `webui serve` | 同上 3 个 | 同上 | 同上 |
| `serve` | 同上 3 个 + `--static-dir` | + `--an-static-dir` | 同上 |

**dest 全部保持旧名**（`workspace` / `goal_db` / `hypotheses_path` /
`static_dir`）——11 个 cmd 处理函数**零改动**。

---

## §3 hidden alias 机制（argparse 语义实测）

### 3.1 实测矩阵（`required=True` 会弄坏 alias）

同一 dest 注册两个 action（canonical `required=True` + alias
`default=SUPPRESS`）时：

```
run --an-workspace A              → workspace='A'        ✓
run --workspace B                 → exit 2 ✗  "arguments are required: --an-workspace"
                                     （required 检查按 action 是否"被见过"计，
                                      alias 设了值也救不了 canonical）
run --an-workspace A --workspace C → workspace='C'        ✓ argv 后出现者赢
run --workspace C --an-workspace A → workspace='A'        ✓
```

所以 **swarm run 不能保留 `required=True`**：canonical 改 `required=False,
default=None`，缺参校验移到派发处（`_dispatch_swarm` 的 run 分支，
缺时打印 usage 错误返回 2——与 argparse 原生报错语义一致）。

### 3.2 默认值不互相覆盖

alias 的 `default=argparse.SUPPRESS`：未提供时不写 namespace——否则两个
action 各自的 default 互相覆盖（后注册者赢），canonical 的默认值会丢。

### 3.3 弃用告警（不留静默生效）

alias 用自定义 Action：命中时 **stderr 打印一次**（进程级去重）：

```
[deprecated] CLI --workspace 已改名（本期仍生效）：请改用 --an-workspace
```

与批 E-2 的 env 告警同风格——旧名**继续工作**但**明确喊出来**。

---

## §4 验证（`tests/test_cli_an_prefix.py`）

| 测试 | 断言 |
|---|---|
| canonical 可解析 | 4 子命令 × `--an-*` → dest 拿到值 |
| alias 可解析 | 同上 × 旧名 → 同 dest 拿到值 |
| 双给后赢 | argv 后出现者赢（两序都测）|
| **help 快照** | `format_help()` 含 `--an-*`；**不含** `--workspace`/`--goal-db` 等旧名 |
| 告警 | alias 解析时 stderr 出现 `[deprecated]`（capsys，进程内去重需 reset）|
| swarm run 缺参 | 不给 workspace → 返回 2（派发处校验）|
| 默认值保持 | serve 不给 workspace → `'.'`（alias 不覆盖 canonical 默认）|

---

## §5 未做（记录）

1. **`--data-profile` 文档漂移**：`foundation/paths.py` docstring 写着
   "或 CLI ``--data-profile``"，但 CLI 里**没有**这个 flag（实测 0 处）。
   本批修 docstring（去掉不存在的 CLI 提法）；真要加 profile flag 是
   新功能，另立批。
2. **`backfill_event_log.py`**：自带 argparse 但属 ops 脚本入口（不在
   `agentnodes` 命令面上），不在本批范围。
3. **hidden alias 的删除时点**："一个版本后"——届时连同
   `_DeprecatedAliasAction` 一起删。

---

## §6 修正：`read_document` 的错误优先级（真 bug）

全量回归里 `tests/test_web_pdf.py` 两条红了，真因不是测试断言，是
`foundation/web/pdf.py` 的**错误优先级颠倒**：

```python
# 原实现：先查依赖，再查路径
if not path: ...                     # ① path 为空
if not _check_fitz(): return 未安装    # ② ← 在这里就返回了
file_path = Path(path).resolve()      # ③ 路径校验根本没机会跑
```

**后果**：在缺 PyMuPDF 的机器上，`read_document("/写错的路径.pdf")`
返回 "PyMuPDF not installed"——把**真正可修的原因**（路径写错）掩盖掉。
用户按提示装完 PyMuPDF，才看到 "file not found"——多一轮无谓排查。

**修法**：先校验**调用方给的东西**（path 非空 / 存在 / 是普通文件），
再校验**环境**（PyMuPDF 可用）。这类错误的优先级天然是：
**输入错 > 环境缺** —— 输入错是用户当下就能改的。

**测试侧**：`test_extract_missing_dependency` 原本用不存在的
`/some/file.pdf` 测"缺依赖"——按新优先级它拿到的是 "file not found"。
改为用 `tmp_path` 造一个真实文件，另加
`test_path_errors_take_priority_over_dependency` 作为回归防护。

> **为什么归到批 C**：不是范围蔓延——是本次全量回归**抓到**的既有
> 真 bug，且同属"错误信息把用户指向错误方向"这一族（与批 E 的
> "静默失效"同源：都是**错误的诊断信号**）。两行修复 + 一条回归测试，
> 不值得单开一批。

### §6.1 与 PyMuPDF DLL 拦截的关系（环境事件，非本批可修）

调查过程中确认：本机**应用程序控制策略**在 2026-11-11 开始同时拦截
3.12 与 3.13 环境里 PyMuPDF 的原生 DLL（`ImportError: DLL load failed:
应用程序控制策略已阻止此文件`）。这与 `batch_gate.cmd` 头注记录的
hypothesis DLL 被拦是同一类宿主策略行为。

- 对**产品**的影响：`read_document` 工具在 `check_available()` 里返回
  False → 不注册 → agent 看不到该工具（优雅降级，符合设计）。
- 对**门禁**的影响：已在批 E-3 §6 修好（`env_gated_tools()` 区分
  "环境缺失"与"未实现"）。
- 本条**不在本批修**——是宿主环境问题，代码侧已正确降级。
