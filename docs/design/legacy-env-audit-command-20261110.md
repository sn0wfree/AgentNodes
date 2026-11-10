# 批 E · 旧名兼容层审计命令（设计）

> **本文档先行**：实现前把"要做什么、为什么、边界在哪"写清楚，再写代码。
> 上游文档：`param-naming-unification-20261109.md`（批 A/B/B' 已完成，批 C/D 待做）。
> 本文档是批 E 的**第一步**：**只审计、不改行为**。
>
> **⚠ 批 E-2（2026-11-10）已落地**——兼容层已删除，读取层只认规范名。
> §0–§7 是 E-1 的原始设计（仍然成立）；**§8 是实施记录**，说明了
> "旧名从可执行的输入降级为可诊断的事实"这个转向，以及为什么
> 审计命令必须活得比兼容层久。

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

> **实现前核对（2026-11-10）**：上表数字已用 `settings` 真实 API 复核一致；
> §3 名单中的 `AN_PERMISSIONS_PATH` 实际**没有旧名 alias**（canonical
> `AGENTNODES_PERMISSIONS_PATH` 从未有过旧名），已从高危名单移除。
> 另确认：alias 与 canonical 无撞名、`EXTERNAL_ENV_VARS` 与两者无交集、
> canonical 中无残留旧前缀——`_LEGACY_RISK` 清单可以按 §3 分档照抄。

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
| **高** | 路径/密钥/安全开关——回落默认会导致**写到别处**或**认证失效** | `AN_WORKSPACE_PATH` `AN_SESSIONS_DB` `AN_DATA_PROFILE` `AN_ADMIN_PASSWORD` `JWT_SECRET` `CORS_ORIGINS` `STATIC_DIR` `AN_DATA_DIR` |
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

---

## §8 批 E-2 落地记录（2026-11-10）

> 本节是**批 E-2 的实施记录**——E-1（§0–§7）的设计已落地为代码，
> 下面是"兼容层删掉之后"的世界是什么样。

### 8.1 核心设计转向：读取层 vs 诊断层

批 E-1 的原始设想是「E-2 把 `aliases` 删干净，审计命令随之失效」。
**实施时发现这条路走不通**——审计命令是删兼容层的**安全网**，
把它跟被保护对象一起删掉，等于拆掉了刹车。

所以批 E-2 的核心转向是**把旧名在架构上分成两层**：

| 层 | 认旧名吗 | 谁在用 | 实现 |
|---|---|---|---|
| **读取层** | **不认** | 全部配置读取 | `ENV_TABLE`（无 `aliases` 字段）+ `_resolve()` |
| **诊断层** | 认 | `audit-legacy` / `resolve_canonical()` | `_LEGACY_NAMES` → `_LEGACY_CATALOG` |

一句话：**旧名从"可执行的输入"降级为"可诊断的事实"**。

具体落到代码：

- `Setting.aliases` 字段**删除**——声明表只描述规范名；
- `_LEGACY_NAMES`（规范名 → 旧名）**保留**，成为诊断层的单一事实源；
- `_LEGACY_CATALOG`（旧名 → 规范名）由它派生，46 组 / 49 个旧名；
- `_ALIAS_INDEX`（读取层的归一索引）**删除**——读取层不再归一；
- 新增 import 期防线：旧名与规范名**撞名即炸**（否则兼容层被偷偷加回来
  且无任何告警）。

### 8.2 三个额外机制（都是为了让"删"不变成"静默失效"）

**① 读取时告警**（`_resolve`）：读某个规范名时若发现环境里还留着它的旧名，
warn 一次并指名该改成什么。**不回落旧值**（兼容层已删），但也不沉默：

```
[deprecated] env AN_WORKSPACE_PATH is no longer read (it was renamed to
AGENTNODES_WORKSPACE) — this value is IGNORED, update your environment/config
```

**② 未登记名告警**（`_default_of`）：调用点传了 `ENV_TABLE` 里没有的名字
→ warn 一次。这条防线直接抓到了批 E-2 实施中的真 bug
（`cache.py` 还在读 `AN_CACHE_COMPACTION_LINKED`）。

**③ 仍然生效的历史名**（`LEGACY_LIVE_ENVS`，4 个）：这几个指向的是
**"用户的数据在哪"**，删掉不会让程序报错，只会让用户找不到自己的数据：

| 旧名 | 规范名 | 谁在直读 |
|---|---|---|
| `AN_DATA_PROFILE` | `AGENTNODES_PROFILE` | `foundation/paths.active_profile` |
| `SR_WORKSPACE_PATH` | `AGENTNODES_WORKSPACE` | `api/routers/workflow._definition_workspace` |
| `SR_ENFORCE_STUDY_IDOR` | `AGENTNODES_ENFORCE_STUDY_IDOR_LEGACY` | `api/routers/goal._session_owned` |
| `STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR` | `AGENTNODES_CHECKPOINTS_DIR` | `apps/goal/checkpoint_store.CheckpointStore` |

它们由各模块**显式**直读（`settings.get_legacy()`，且该入口对非登记名
直接抛 `KeyError`——防止兼容层无声回流）。审计命令把这两类分开报：
**已失效**（值被忽略，必须改名）vs **仍生效**（已废弃，建议改名）。

### 8.3 实施中抓到的 5 个真 bug

删兼容层的价值不亚于"少 49 行 alias"——它逼出了一批**静默失效**：

| # | 位置 | 问题 | 后果 |
|---|---|---|---|
| 1 | `apps/goal/checkpoint_store.py:49-50` | 两个分支**读同一个规范名**（改名时把历史分支也一起改了） | 老部署的 checkpoint 目录被静默忽略 → 恢复点丢在别处 |
| 2 | `interfaces/cli/commands/server.py:104,106,108,154,156` | CLI 把 `--workspace` / `--hypotheses-path` / `--static-dir` 写进**旧名** env | reload 模式下三个 CLI 参数**全部失效** |
| 3 | `interfaces/cli/llm_config.py:55` | `--llm-profile` 写 `LLM_PROFILE` | profile 选择失效 |
| 4 | `kernel/agent/cache.py:116` | 读 `AN_CACHE_COMPACTION_LINKED` | 该开关**永远读不到**（一直走默认 true） |
| 5 | `interfaces/api/routers/goal.py` | `AGENTNODES_ENFORCE_STUDY_IDOR_LEGACY` 声明了却**没有读取点** | 旧 IDOR 开关的规范名形同虚设（死声明，被门禁抓到） |

> #1 是**既有 bug**（不是批 E-2 引入的）——改名时（批 B'）历史分支被一起
> 改成了规范名，分支从此同质。批 E-2 顺带把它修回去了。

### 8.3-b 顺带定位的一个既有 flaky 测试（不是批 E-2 引入的）

全量回归里 `tests/test_security_hardening.py::TestTokenForgery::
test_tampered_signature_rejected` 偶发失败（422 ≠ 401）。定位过程值得记录，
因为**失败信息具有误导性**——它看起来像"鉴权被绕过"，实际是**测试自己没
篡改到签名**。

**机制**：签名是 32 字节 HMAC-SHA256，base64url 无填充 = **43 字符 = 258 bit**，
而 32 字节只有 **256 bit**——**末位的低 2 bit 是填充位，解码时被丢弃**。
所以"翻转签名的最后一个字符"有约 **1/16（6.25%）** 的概率解出**完全相同
的字节**：签名没被真正篡改 → 中间件正常放行 → 请求落到路由 → 因为没带
`session_id` 而返回 **422**（FastAPI 请求校验）而不是 401。

实测（4000 次采样）：命中 **266 次 = 6.7%**；改翻中间第 5 位（6 bit 全部
有效）后 2000 次采样 **0 次**碰撞。

**历史**：这个测试的注释说它"偶发失败（依赖签名长度，故只在大顺序下出现）"，
并把翻转从"丢第一个字符"改成"只翻最后一位"——长度问题修好了，**填充位
问题还在**，所以它一直在以约 6% 的概率随机失败。

**修法**：翻中间一位（第 5 位），并把断言收紧到 `401` + 响应体含 token
字样（这样即使将来再出问题，失败信息也能直接指向鉴权而不是 422 谜语）。

> **教训**：一个"偶发失败"的测试，根因往往不在它看起来的地方。
> 这次如果按表面症状去查"鉴权为什么没拦住篡改的 token"，会一路查到
> 鉴权中间件里去——而中间件完全正确。**先量概率，再下结论。**

### 8.4 落地规模

| 指标 | 数字 |
|---|---|
| canonical 声明数 | 58（不变） |
| 旧名（诊断层）| 46 组 / 49 个 |
| 仍然生效的历史名 | 4 |
| 读取层删除的字段 | `Setting.aliases` |
| 读取层删除的索引 | `_ALIAS_INDEX` |
| 调用点改名 | src 31 文件 / tests 48 文件 |
| 注释/docstring 旧名清理 | src 35 文件 |
| 新增/改写测试 | `tests/test_legacy_env_audit.py`（24 条）|

### 8.5 验证

| 项 | 结果 |
|---|---|
| 全量回归 | 见提交记录（`tests/`，attic 除外）全绿 |
| 架构门禁 | 上行违规 0 条；架构图数字已用 `sync_architecture_diagram.py --write` 回写 |
| 8 步 batch_gate | ALL GREEN |
| 读层硬切换 | `test_old_name_is_not_read_by_the_settings_layer` |
| 读层告警 | `test_reading_canonical_warns_when_old_name_lurks` |
| 仍生效历史名 | `test_live_legacy_env_is_still_honored` + `test_get_legacy_rejects_names_that_are_not_live` |
| 审计命令仍可用 | `test_audit_still_works_after_compat_layer_removed` |
| 既有 flaky 测试 | `test_tampered_signature_rejected` 定位并修掉（见 §8.3-b） |

### 8.6 批 E 的最终状态

```
批 E-1：审计命令（只读）              ✅ 已提交
批 E-2：删兼容层（读取层硬切换）        ✅ 本节
      ↓
批 D：config.yaml 取代 llm.json       ← 动真实配置，最后做
      ↓
批 C：CLI --an- 前缀（保留旧名作 argparse alias）  ← 可与其它批并行
```

**批 E-1 的 §7 两个未决项**保持原判：审计不扫 shell 配置文件（不读用户
家目录、不碰密文）、审计不自动改名（纯只读，改名交给用户显式执行）。
批 E-2 落地后，审计命令的语义从「删兼容层前的闸门」变成
「**兼容层已删，告诉你还剩哪些旧名在骗你**」——功能没变，语境变了。
