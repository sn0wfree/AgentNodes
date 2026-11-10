# 批 E · 旧名兼容层审计命令（设计）

> **本文档先行**：实现前把"要做什么、为什么、边界在哪"写清楚，再写代码。
> 上游文档：`param-naming-unification-20261109.md`（批 A/B/B' 已完成，批 C/D 待做）。
> 本文档是批 E 的**第一步**：**只审计、不改行为**。

---

## §0 背景

批 B' 把 `AN_*` / `QNODES_*` / `SR_*` 改名成 `AGENTNODES_*`，旧名进
`aliases` 保持生效（读时归一 + warn 一次）。这层兼容是**批 E 的删除对象**。

删掉它之前必须知道一件事：**这台机器上还有哪些旧名在用**。
否则删完是**静默失效**——不报错，只是行为回落默认。

实测规模（2026-11-10，`settings.py` 真实 API）：

| 指标 | 数量 |
|---|---|
| canonical 声明数 | 58 |
| 带旧名 alias 的 canonical | 46 |
| 旧名总数 | **49** |

---

## §1 目标

提供一条**只读、零行为变更**的命令，回答：

> 这台机器上还有哪些旧名环境变量被设置了？它们分别对应哪个新名？
> 如果现在删掉兼容层，哪些行为会变？

**不做什么**（本批明确排除）：
- 不删除任何 alias
- 不修改任何环境变量
- 不改任何运行时行为
- 不动 `llm.json`（那是批 D）

---

## §2 命令设计

```
agentnodes config audit-legacy
```

**输出**（三段）：

```
=== 1. 本进程环境中的旧名（正在生效）===
  AN_WORKSPACE_PATH              -> AGENTNODES_WORKSPACE
    当前值: /data/proj
    风险: 高 —— 工作区路径；删兼容层后回落 cwd，agent 会写到别处
  AN_LOG_LEVEL                  -> AGENTNODES_LOG_LEVEL
    当前值: DEBUG
    风险: 低 —— 仅影响日志详细度

=== 2. 本进程环境中的 canonical（已改名）===
  AGENTNODES_WORKSPACE = /data/proj
  ...

=== 3. 外部变量（不归本表管，不受影响）===
  HOME, NO_COLOR, TERM_PROGRAM, ...

=== 结论 ===
  发现 2 个旧名仍在使用 —— 删除兼容层前请先改名。
  建议：把 AN_WORKSPACE_PATH 改为 AGENTNODES_WORKSPACE
```

**退出码**：
- `0` — 无旧名在用（可以安全删兼容层）
- `1` — 有旧名在用（**删除前必须先改名**）
- `2` — 审计本身失败

这个退出码让命令能进 CI：**"有旧名"直接红**，防止兼容层被无声删掉。

---

## §3 风险分级

不是所有旧名都同等危险。按"删掉后行为变化"分三档：

| 档 | 判据 | 旧名（节选）|
|---|---|---|
| **高** | 路径/密钥/安全开关——回落默认会导致**写到别处**或**认证失效** | `AN_WORKSPACE_PATH` `AN_SESSIONS_DB` `AN_DATA_PROFILE` `AN_ADMIN_PASSWORD` `JWT_SECRET` `CORS_ORIGINS` `STATIC_DIR` `AN_PERMISSIONS_PATH` `AN_DATA_DIR` |
| **中** | 超时/预算/路径——回落默认改变行为但不致命 | `AN_CHAT_ATTEMPT_TIMEOUT` `QNODES_CHAT_WAIT_TIMEOUT` `AN_HYPOTHESES_PATH` `AN_WORKFLOWS_DIR` `AN_CHECKPOINTS_DIR` `AN_MODEL_CATALOG_DIR` `AN_LOG_FILE` `AN_EVENT_LOG_READ` |
| **低** | 缓存/引擎调参——回落默认只是性能或细节差异 | `AN_CACHE_*`（13 个） `AGENTNODES_THEME` `AN_TOOL_RESULT_MAX_CHARS` `AN_L4_INCLUDE_MESSAGES` `QNODES_PLATFORM_STORE` |

**高危档必须在删兼容层前逐个改名**（不是"建议"，是**前置条件**）。

---

## §4 实现

### 4.1 审计逻辑（`foundation/settings.py`）

新增**纯函数**，不改任何现有行为：

```python
def legacy_usage(env: Mapping[str, str] | None = None) -> list[LegacyUsage]:
    """扫出当前环境里仍在用的旧名 + 风险分档。

    返回的每条含：old_name / canonical / value / risk / note。
    """
```

风险分档来自**显式清单**（`_LEGACY_RISK`），不靠启发式——启发式猜风险
必然出错，而这份清单是要指导人改配置的，必须准确。

### 4.2 CLI 命令

新命令组 `config`，子命令 `audit-legacy`：

```
agentnodes config audit-legacy [--json]
```

`--json` 输出机器可读（给 CI / 脚本用）。

### 4.3 边界

- **只读** `os.environ`，不写
- 不触发任何 settings 读取（避免副作用/告警噪声）
- 进程级审计（本进程环境）。**不扫** shell 配置文件——那要读用户家目录
  且可能含密文；命令输出的是"当前生效"的，比"某文件里写过"更有意义

---

## §5 验证

| 测试 | 断言 |
|---|---|
| 无旧名 | 退出码 0，输出"可安全删除" |
| 有旧名（monkeypatch env）| 退出码 1，列出旧名→canonical 映射与风险档 |
| 风险分档 | 高危名单里的旧名被判为 high |
| canonical 不误报 | `AGENTNODES_*` 不被列为旧名 |
| 外部变量不误报 | `HOME` / `NO_COLOR` 不出现在旧名列表 |
| `--json` | 输出可 `json.loads`，字段齐全 |
| **零行为变更** | 命令执行前后 `os.environ` 完全一致 |

---

## §6 与后续批次的关系

```
批 E-1（本批）：审计命令            ← 零风险，只读
      ↓  （用户按报告改名）
批 E-2：删兼容层（49 个 alias）      ← 破坏性，删前要求审计干净
      ↓
批 D：config.yaml 取代 llm.json      ← 动真实配置，最后做
      ↓
批 C：CLI --an- 前缀（保留旧名作 argparse alias）  ← 可与其它批并行
```

**顺序理由**：E-2 的前置条件是"审计干净"，所以 E-1 必须先做；
D 动真实配置风险最高，放最后；C 保留别名所以破坏性最低，可并行。

---

## §7 未决（待用户拍板）

1. **审计范围是否包含 shell 配置文件**（`.bashrc` / `.zshrc` / PowerShell profile）？
   - 包含 → 能发现"下次开终端才会生效"的旧名，但可能读到密文、跨平台麻烦
   - 不包含 → 只报"当前生效"，够用且零副作用
   - **默认建议**：不包含；需要时另加 `--scan-profiles`
2. **高危档是否要"自动改名"**（审计命令顺便改环境变量）？
   - 建议**不做**——审计命令应纯只读；改名交给用户显式执行
