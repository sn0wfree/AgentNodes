# LLM 配置构造入口收敛（设计，R-014）

> **性质**：实施设计（**未实施，待确认**）· **日期**：2026-11-16
> **上游**：会话生命周期下沉批次 B 的前置（方案 `session-lifecycle-kernel-migration-20261116.md` §4 批次 B3 的 R1）
> **本文档只出方案，不动代码。** 确认后才实施。

---

## §0 结论摘要

**要做什么**：让 `LLMConfig.load()` 成为 **LLM 配置的唯一构造入口**，删除调用方各自加的
`load_dotenv(override=True)`。

**为什么**：实测发现 `LLMConfig.load()` 内部已经处理了 dotenv（`_try_load_dotenv()`，
`override=False`），调用方额外那层 `override=True` **不只是冗余，还会覆盖用户显式 export 的
API key**（实测证据见 §2.2）。

**规模**：`LLMConfig.load()` 共 **20 处调用**（interfaces 12 / kernel 5 / foundation 2 / evals 1）；
额外的 dotenv 处理有 **4 处**（其中 3 处 `override=True`、1 处复用 `_try_load_dotenv`）。

**改动面**：4 个文件（3 处删 dotenv + 1 处删包装函数），**零新增抽象**。

**一个重要的历史澄清**：用户记得「之前做过配置中心统一化、还做了 L1/L4 分层」——
**属实**，但那次是 **ConfigHub**（`kernel/orchestration/config_hub.py`），
它只覆盖**平台组件参数**（gateway / mailbox / budget），**不覆盖 LLM 配置**。
所以现在有三个配置体系并存（§1.4），本方案只收敛 LLM 那一支。

---

## §1 现状实测

### 1.1 `LLMConfig.load()` 的调用分布（20 处）

| 层 | 处数 | 位置 |
|---|---:|---|
| **interfaces** | **12** | `api/app.py:346` · `cli/llm_config.py:84` · `cli/llm_config_check.py:91` · `cli/__main__.py:109` · `cli/commands/llm.py:186` · `cli/tui/app.py:200` · `cli/tui/session.py:337` · `api/routers/chat.py:199,258` · `api/routers/system.py:74,119,165` |
| **kernel** | **5** | `agent/executor.py:378` · `agent/role_factory.py:77,154` · `gateway/singleton.py:176` · `agent/builtin_tools/subagent_tool.py:258` |
| **foundation** | **2** | `llm/model_hub.py:91,105`（内部，`env=` 注入）|
| **evals** | **1** | `run_evals_llm.py:59` |

> **关键观察**：`LLMConfig.load()` 是**唯一入口这件事已经成立** —— 20 处全部走它，
> 没有任何地方直接构造 `LLMConfig(...)`。所以本方案**不是**「统一入口」，而是
> 「**删掉入口之外多余的 dotenv 处理**」。

### 1.2 额外的 dotenv 处理（4 处）

| # | 位置 | 形态 | 判定 |
|---|---|---|---|
| 1 | `interfaces/api/routers/chat.py:194` | `load_dotenv(Path.home()/".agentnodes"/".env", override=True)` | ❌ **删除**（冗余 + 覆盖用户值）|
| 2 | `interfaces/cli/__main__.py:103` | `load_dotenv(_QUANTNODES_DOTENV_PATH, override=True)` | ⚠️ **保留**（CLI 启动早期就要有 key，且路径常量在此定义）|
| 3 | `interfaces/cli/__init__.py:101` | 同上 | ⚠️ **保留**（同 #2，另一入口）|
| 4 | `interfaces/cli/_auto_onboard.py:118` | 同上 | ⚠️ **保留**（onboard 向导自身需要）|
| 5 | `interfaces/cli/commands/llm.py:28` | `_try_load_dotenv()`（复用 config 的） | ❌ **删除**（`load()` 内部会调，完全重复）|

> CLI 那 3 处保留的理由：它们是 **CLI 启动契约**（`_QUANTNODES_DOTENV_PATH` 在
> `cli/__init__.py` 定义），且发生在「还没有 LLMConfig 实例」的更早阶段。
> 但它们的 `override=True` 也有同样的覆盖问题 —— **列为待议（§6-2）**。

### 1.3 `LLMConfig.load()` 内部的 dotenv 语义

`foundation/llm/config.py:251-252`：
```python
if load_dotenv:          # 默认 True
    _try_load_dotenv()
```

`_try_load_dotenv()`（L345-370）逐个加载：
1. `_ld()` —— 从调用帧搜索 `.env`
2. `Path.cwd() / ".env"` —— 显式加载 workspace 的
3. `Path.home() / ".agentnodes" / ".env"` —— 规范密钥文件

**全部 `override=False`**（docstring L350 明确：`Explicit process env vars always win`）。

### 1.4 三个配置体系并存（本方案只动第三个）

| 体系 | 文件 | 行数 | 覆盖范围 | 分层装载 | 消费方 |
|---|---|---:|---|---|---|
| **ConfigHub** | `kernel/orchestration/config_hub.py` | 193 | **平台组件参数**（gateway / mailbox / budget）| ✅ 默认 < 文件(yaml/json) < env | 3 处（gateway / invariants / budget_gate）|
| **settings** | `foundation/settings.py` | 943 | **进程级环境变量**（56 条声明）| ❌ 只有 env / default 两级 | 全仓 62 处 |
| **LLMConfig** | `foundation/llm/config.py` | 760 | **LLM 配置**（4 层合并：默认 < 文件 < env < cli）| ✅ 4 层 | 20 处 |

> **用户记忆的核实**：ConfigHub 那次统一化**确实做过**（`roadmap-plain-20261026.md:15`
> 「平台所有参数统一收口 ConfigHub」；`architecture-discussion-record-20261008.md:107`
> 标记 ✅ 完成，88 测试全绿）。但它的范围是**平台组件**，LLM 从未纳入。
> 本方案不是重做那件事，而是补上 LLM 这一支。

---

## §2 行为差异实测（本方案的核心依据）

### 2.1 两条路径解析结果是否一致

测 `~/.agentnodes/.env` 存在、进程环境**没有** `MINIMAX_API_KEY` 的情况：

| 路径 | 结果 |
|---|---|
| 只调 `LLMConfig.load()`（靠内部 `_try_load_dotenv`）| `api_key` 解析成功，等于 .env 里的值 ✅ |
| 外部先 `load_dotenv` 再 `load()` | 同样成功，值相同 ✅ |

⇒ **在「进程无该 key」时两者等价。**

### 2.2 但「进程已有该 key」时不等价 —— 这是真差异

| 场景 | `override=True`（chat.py 现状）| `override=False`（`load()` 内部）|
|---|---|---|
| **S1** 进程有用户显式值 + .env 也有 | ❌ **用 .env 的值覆盖用户值** | ✅ **保留用户值** |
| S2 进程无值，只有 .env | 读 .env | 读 .env（等价）|
| S3 进程有值，.env 里没这个 key | 保留 | 保留（等价）|

**实测输出（S1）**：
```
override=True  -> 文件值(from .env)
override=False -> 用户显式值 ✅
```

⇒ **`override=True` 违反 R-006 的精神**（「显式 > 文件」）。这不是纯重构，
是**行为修正**：用户 `export MINIMAX_API_KEY=xxx` 后跑服务，当前会被 `.env` 覆盖。

### 2.3 为什么当初会写成 `override=True`

`_build_llm_config` 的 docstring 写着「Same logic as TUI's `__main__.py`」——
它是**从 TUI 启动代码复制过来的**，而 TUI 那个场景（CLI 启动）确实需要 override
（先载入密钥，后面才构造配置）。复制到 web 服务后，语义就错了。

---

## §3 方案

### 3.1 目标形态

```
LLMConfig.load()   ←  唯一构造入口（已成立）
   └─ 内部 _try_load_dotenv()  ←  唯一 dotenv 入口（override=False，进程 env 赢）
        ├─ cwd/.env
        └─ ~/.agentnodes/.env

调用方（20 处）     ←  只需 `LLMConfig.load()`，不碰 dotenv
```

### 3.2 具体改动（3 处，零新增抽象）

| # | 文件 | 改动 | 行为变化 |
|---|---|---|---|
| **A** | `interfaces/api/routers/chat.py:188-203` | `_build_llm_config()` 删掉 `load_dotenv(override=True)` 那段，只留 `LLMConfig.load()` + 异常兜底（**函数本身保留**，见 §6 决策 4）| ✅ **修正**：不再覆盖用户显式 key |
| **B** | `interfaces/cli/commands/llm.py:26-28` | 删掉 `_load_dotenv()` 调用（`load()` 内部已做）| 无（等价）|
| **C** | `interfaces/api/session/service.py:607,1233` | `from ..routers.chat import _build_llm_config` → `from agentnodes.foundation.llm.config import LLMConfig`；`_build_llm_config()` → `LLMConfig.load()` | 无（等价）+ ✅ 顺带解掉 B 批的 R1 前置 |

> ~~**D** `routers/slash_common.py` 改直接调 `LLMConfig.load()`~~ —— **作废**（§6 决策 4）：
> `slash_common` 与 `chat` 同在 `routers` 下（**同层，不违规**），无消除依赖的必要；
> 继续用 `_build_llm_config` 还能保留兜底语义。

> **为什么 C 是本方案的一部分**：它是会话迁移批次 B3 的 R1 阻塞项。
> 做掉 C，`service.py` 对 `routers` 的 3 处反向依赖就少 2 处（剩 `_get_db` 两处）。
>
> **注意 C 与 A 的语义差异**：`service.py` 改用 `LLMConfig.load()` 后**没有** `_build_llm_config` 的
> 「失败返回 None 不抛」兜底 —— 但 `service.py:613` 原本就有 `if cfg is None:` 分支，
> 改为直接 `load()` 抛异常时会走外层 except。**实施时必须确认这条路径的异常处理仍完备**
> （验证判据见 §4-6）。

### 3.3 明确不做

| # | 不做 | 理由 |
|---|---|---|
| 1 | 不新建 `foundation/llm/factory.py` | `LLMConfig.load()` 已经是工厂；再包一层是「单次抽象」 |
| 2 | 不动 CLI 的 3 处 `load_dotenv`（`__main__` / `__init__` / `_auto_onboard`）| 它们是 CLI 启动契约，且在 LLMConfig 实例存在之前 —— 列为待议 §6-2 |
| 3 | 不把 LLMConfig 纳入 ConfigHub | 那是更大的重构（ConfigHub 现在是 gateway/mailbox 专用），且 LLMConfig 已有自己的 4 层合并 —— 重复造轮子 |
| 4 | 不改 `_try_load_dotenv()` 的加载顺序或 override 语义 | 它已经是对的（`override=False`）；改了会影响 CLI |
| 5 | 不动 `kernel/` 的 5 处调用 | 它们只是读配置，没有额外 dotenv —— 无需改 |
| 6 | 不动 `foundation/llm/model_hub.py` 的 2 处（`env=` 注入）| 那是测试/预览用途，保留注入能力 |

---

## §4 验证判据

| # | 判据 | 方向 |
|---|---|---|
| 1 | `grep -rn "load_dotenv" src/agentnodes/interfaces/api/` 命中数：**1 → 0** | 红→绿 |
| 2 | `grep -rn "_build_llm_config" src/` 命中数：**6 → 2**（只剩定义 + chat.py 自身调用）| 红→绿 |
| 3 | `grep -rn "from ..routers.chat import" src/agentnodes/interfaces/api/session/service.py` 命中数：**2 → 0** | 红→绿（B 批前置）|
| 4 | **S1 场景行为**：进程有 `MINIMAX_API_KEY` + .env 也有 → `LLMConfig` 解析出的是**用户值** | 红→绿（行为修正）|
| 5 | **S2 场景行为**：进程没有 + .env 有 → 解析出 .env 的值 | 绿→绿（不能回退）|
| 6 | 全量 pytest | **绿→绿**（0 failed）|
| 7 | `batch_gate.cmd` | **绿→绿**（ALL GREEN）|
| 8 | `check_dangling_imports.py --layers` 上行违规 | **绿→绿**（0）|

> **判据 1–3 是「门禁化」的候选**：实施后可以加 `check_env_access.py` 的扩展或新门禁，
> 禁止 `interfaces/api/` 里出现 `load_dotenv`。**本方案不含该门禁**（另议 §6-3）。

---

## §5 回滚

全是删减 + 改 import，无新增结构：

```
git revert <commit>   # 一步回滚
```

**唯一需要留意的**：判据 4 是**行为修正**。如果有人依赖「.env 覆盖进程 env」的旧行为
（例如 CI 里故意 export 一个错 key、靠 .env 纠正），回滚后需要显式调整。
实测本仓无此用法（`grep -rn "override=True" src/` 只有那 3 处 CLI + 1 处 chat）。

---

## §6 决策记录（2026-11-16 已拍板）

| # | 事项 | 拍板 | 实施影响 |
|---|---|---|---|
| **1** | C 项（改 service.py）是否纳入本批？ | **纳入**（选项 a）| 本批同时解掉 B 批 R1 前置：`service.py` 对 `routers` 的 3 处反向依赖减 2 处 |
| **2** | CLI 那 3 处 `override=True` 是否也改？ | **不改**；后续可考虑**参数化定义**（把 override 语义做成可配置项）| 本批保留 CLI 现状；文档标注这处不一致（CLI 用 override=True / web 用 override=False）|
| **3** | 是否加门禁禁止 `interfaces/api` 里出现 `load_dotenv`？ | **暂时不加** | 本批纯删减；门禁另议 |
| **4** | `_build_llm_config` 函数本身是否保留？ | **不加改动 = 保留**（选项 a）| 函数保留（含「失败返回 None 不抛」的兜底语义）；chat.py / slash_common.py 继续用它 |

> **决策 4 的推论**：既然保留 `_build_llm_config`，原方案 **D 项（改 slash_common.py 直接调 `LLMConfig.load()`）作废** ——
> `slash_common` 与 `chat` 同在 `routers` 下（**同层，不违规**），没有消除依赖的必要；
> 继续用 `_build_llm_config` 还能保留兜底语义，比直接调 `load()` 更一致。
>
> **本批实际改动 = A + B + C 三项**（D 取消）。

> **决策 2 的技术备注**（供后续参数化参考）：`override` 的语义差异只在「进程已 export 同名 key」时体现。
> CLI 场景（`--profile` 切换需要覆盖）与 web 服务场景（用户显式 export 应当赢）诉求不同，
> 因此参数化比统一更合理。可考虑加 `LLMConfig.load(dotenv_override: bool = False)`。

---

## §6.1 原待确认项（存档）

| # | 事项 | 选项 | 建议（已采纳状态见 §6）|
|---|---|---|---|
| 1 | C 项（改 service.py）是否纳入本批？ | (a) 纳入；(b) 只做 A/B/D | (a) ✅ 采纳 |
| 2 | CLI 那 3 处 `override=True` 是否也改？ | (a) 改；(b) 不改 | (b) ✅ 采纳 + 参数化方向 |
| 3 | 是否加门禁禁止 `load_dotenv`？ | (a) 加；(b) 不加 | (b) ✅ 采纳 |
| 4 | `_build_llm_config` 是否保留？ | (a) 保留；(b) 删掉 | (a) ✅ 采纳 |

---

## §7 证据索引

| 断言 | 复现 |
|---|---|
| 20 处 `LLMConfig.load()` 的分布 | `python tmp/probe/llm_calls_by_layer.py` |
| 4 处额外 dotenv | `python tmp/probe/llm_config_facts.py` §② |
| S1/S2/S3 行为差异 | `python tmp/probe/dotenv_override_diff.py` |
| 只调 `load()` 就能拿到 api_key | `python tmp/probe/load_order_test.py` |
| ConfigHub 历史与边界 | `python tmp/probe/trace_confighub.py` |
| `_build_llm_config` 的 6 处引用 | `git grep -n "_build_llm_config" -- src/` |
