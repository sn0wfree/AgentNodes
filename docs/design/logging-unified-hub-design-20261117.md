# 统一日志中枢设计 —— 能力合并（不 copy 参考文件）

> **日期**: 2026-11-17 · **性质**: 实施设计 · **分支**: `refactor/session-lifecycle-kernel`
> **用户决策（两轮）**：
> ① 「能否直接 copy 过来 logger.py 然后复用，可以根据需要升级 logger.py，但不要自己造轮子」
> ② 「`configure_logging` 和参考 `logger.py` 如果已经有类似的功能了，**可以将功能合并**，不一定非要 copy」
> **前置设计**: `docs/guides/logging-unified-entry-design-20261102.md`（装配层契约，**本文不推翻它**）

---

## 0 · 结论摘要

1. **能力合并，不复制文件**。参考实现（`pf_analysis/utils/logger.py`，与 CodersWheel `QuickTool/logger.py` 同源形态）
   的 6 项能力逐项对照本仓：2 项**本仓已有更强实现**（作废）、2 项**本仓无约定**（不采纳）、**2 项真正缺**（合并进来）。
2. 本仓**已有日志中枢的装配层**：`configure_logging`（4 个进程入口全接线）+ `TraceFilter` + `dual_log`，契约网 6 个测试文件齐全
   ——**不重建、不改其语义**。
3. 真正缺的是**获取层**：164 处 `logging.getLogger(...)` 散落在 159 个文件，绕过了中枢。
4. 合并的落点由**依赖方向**决定（不是审美）：`foundation/settings.py` 与 `foundation/storage/sqlite.py`
   **自己也要用 logger**，而中枢的装配层依赖它们 → 中枢必须拆成
   **零依赖内核（`foundation/logger.py`）+ 既有装配层（`foundation/observability/logging_setup.py`）**，
   能力互补不重复，对外**一个获取入口**。
5. 收编后 **164 处调用点每处只改一行**，`logger.info(...)` 等调用形态一字不改 → `caplog` 77 处断言不受影响。

---

## 1 · 与既有设计的关系（先定「以谁为准」）

| 层 | 归属 | 事实源 | 本文动它吗 |
|---|---|---|---|
| 装配层（handler / 级别 / 文件 / surface 流定向） | `foundation/observability/logging_setup.py` | `docs/guides/logging-unified-entry-design-20261102.md` §4 | **不动**（被复用） |
| 上下文层（trace_id / session_id / study_id / round_num） | `foundation/observability/trace.py` | 同上 | **不动**（被复用） |
| **获取层（`getLogger` 入口 + 门面 + 计时装饰器）** | **本轮新增 `foundation/logger.py`** | **本文** | **新增** |

> 2026-11-02 设计 §3 已借鉴过 CodersWheel logger.py 的「双写在 handler 层」；
> **本轮合并的是它没被取用的另一半**：单一获取入口 + 模块门面 + `deco` 计时装饰器。

---

## 2 · 现状实测（2026-11-17 工作区 AST 扫描）

### 2.1 已经统一的部分（不要重复造）

| 项 | 实测 |
|---|---|
| handler 装配集中度 | `src/` 内**零** `basicConfig` / `dictConfig` / `Formatter`；全部 `addHandler`(2) / `StreamHandler`(1) / `FileHandler`(1) / `RotatingFileHandler`(1) / `setLevel`(2) 都在 `foundation/observability/logging_setup.py` |
| 进程入口接线 | 4 个入口都调 `configure_logging`：`interfaces/api/app.py`、`interfaces/cli/__main__.py`、`interfaces/mcp/server.py`、`kernel/session/backfill_event_log.py` |
| console 流定向 | 按 surface 策略表：web/cli/mcp → stderr，script → stdout（`logging_setup._STREAMS`） |
| trace 上下文 | 4 个 `ContextVar` + `TraceFilter`（挂 handler 层）+ `JsonFormatter` |
| call-site 双写收敛 | `dual_log()`（仅在「进程从未装配 handler」时 print 兜底） |
| 契约网 | `tests/test_logging_setup.py`（17KB）+ `tests/test_trace.py` / `test_trace_context.py` / `test_trace_context_fallback.py` / `test_trace_ownership.py` / `test_trace_projection.py` |

### 2.2 缺口：获取层散落

```
164 处 logging.getLogger(...)   ← 159 个文件
  ├ 160 处  getLogger(__name__)
  ├   3 处  getLogger()          ← 无参 = 拿 root logger（3 处全在 observability 包内，属合法用法）
  └   1 处  getLogger('agentnodes')
按层：kernel 92 · interfaces 29 · foundation 26 · apps 16 · 根 1
模块级变量名：149 个叫 `logger`（+1 个 `_dual_logger`）—— 命名已统一，只是**获取方式**没统一
```

### 2.3 调用形态（决定中枢要提供什么）

| 观测 | 数值 | 含义 |
|---|---|---|
| level 分布 | warning 244 · debug 128 · info 108 · exception 43 · error 22 | 主力是 warning/debug → 需要**按包调级别**（全局一档不够） |
| `exc_info=` | 61 处 | 异常栈要保留 → 中枢不得改写 `exc_info` 语义 |
| `extra=` | **0 处** | **不做通用结构化字段扩展**（无诉求） |
| 消息形态 | 500 处常量字符串，f-string 0 处 | 不需要惰性格式化 |
| 测试侧 | `caplog` **77 处** · `logging.getLogger(` 16 处 | **logger 名必须与模块名一致**，否则 caplog 断言红 |

### 2.4 依赖方向的事实（决定落点，见 §3.3）

| 事实 | 实测 |
|---|---|
| `foundation/settings.py` 自己有 logger | 1 处 `getLogger(`；该文件**零项目内 import**（纯 stdlib） |
| `foundation/storage/sqlite.py` 自己有 logger | 1 处 `getLogger(`；import `agentnodes.foundation`（settings） |
| 装配层依赖它们 | `logging_setup.py` → `agentnodes.foundation`（settings）+ `agentnodes.foundation.storage.sqlite` |
| `observability` 包的 `__init__` 会连带装配层 | `observability/__init__.py` → `.logging_setup` → 上面两个 |
| 层归属 | `scripts/arch/layer_matrix.py::LAYER_MEMBERSHIP` 已登记 `"agentnodes.foundation": "L4"`（最长前缀匹配 → `foundation/` 下新文件自动判为 L4，**无需新增登记**） |

### 2.5 明确不在本轮的范围

- `print(` **401 处 / 37 文件** —— 需先做 subprocess-feed / TUI / 真错误三分法再治理，单独排期（`dual_log` 已是收敛手段）。
- 通用结构化字段（`extra=`）—— 实测 0 处诉求。

---

## 3 · 能力合并（逐项对照，不复制文件）

### 3.1 参考实现的 6 项能力 → 本仓处置

参考实现：`D:\Github\pf_analysis\pf_analysis\utils\logger.py`（96 行）= `Singleton` + `LoggerHelper`
（FileHandler + StreamHandler 双挂 + `deco` 计时 + `sql`/`status` 前缀助手）。

| # | 参考实现的能力 | 本仓现状（实测） | **合并动作** |
|---|---|---|---|
| M1 | 一个 logger 挂 FileHandler + StreamHandler（同 formatter，调用点只调一次） | `configure_logging` **已有且更强**：console 按 surface 定向 + 轮转文件 + `_HANDLER_MARK` 幂等重挂 | **作废**（不合并，重复即漂移） |
| M2 | `Singleton` 防重复挂载 | 同上，`_HANDLER_MARK` 幂等重挂支持测试重复调用，**比 Singleton 更强** | **作废** |
| M3 | `getLogger(app_name)` 取 logger + `self.warn/.info/.critical/.debug` 属性别名 | 164 处裸 `logging.getLogger(__name__)`；别名风格本仓已在用（`logger.warning(...)`） | **合并为 `get_logger(name)`**（唯一入口）+ `Logger` 门面（转发 stdlib logger） |
| M4 | `deco(level, timer, extra_name)` 计时装饰器 | 无 | **合并为 `Logger.deco(...)`**，升级：异常路径也记耗时 + 栈（原实现异常时 start 泄漏且无异常日志） |
| M5 | `sql()` / `status()` 前缀助手 | 本仓**无 SQL 日志约定**（存储走 event_log / sessions 表） | **不采纳**（记否决理由，不留空壳） |
| M6 | 固定 `sys.stdout` + 默认落 `test.log` | 本仓：console 按 surface 定向（**mcp 的 stdout 是 JSON-RPC 帧**）；文件日志默认关闭（`AGENTNODES_LOG_FILE` 显式开启，路径走数据根 `logs/`） | **沿用本仓策略**（内核不碰 handler 配置） |

**净增代码**：一个零依赖内核模块（`get_logger` + `Logger` 门面 + `deco`），约 80 行；
**净删**：参考实现里 M1/M2/M5 三块（重复 + 无用）——合并是**减法**，不是加法。

### 3.2 与参考实现的**行为差异**（合并后必须保持本仓语义）

| 项 | 参考实现 | 合并后（本仓） | 依据 |
|---|---|---|---|
| logger 名 | `getLogger(app_name)`，`app_name=None` → **名为 `"None"`** | `get_logger()` 缺省取调用方 `__name__`；`agentnodes.` 之外的第三方名字原样放行 | 名字错会让 JSON 的 `logger` 字段与 caplog 失真 |
| 模块粒度 | 全局单例 `Logger.info(...)`（**丢模块名**） | **按模块取**：`get_logger(__name__)`，每模块一个门面，**共用同一套装配** | `caplog` 77 处按模块名断言；JSON 的 `logger` 是排障主键 |
| 上下文 | 无 | **零成本继承** `TraceFilter`（trace_id / session_id / study_id / round_num 自动进每条 record） | 既有实现 |
| 文件日志 | 默认建 `test.log` | 默认**不落文件**；开启时走 `configure_logging` 的 RotatingFileHandler（10MB × 5） | 本仓约定 + R-005（路径从 `PROFILES` 取） |

### 3.3 落点：能力合并，但**必须按依赖方向拆两层**（关键约束）

**约束来源（实测，见 §2.4）**：

```
foundation/settings.py ──(自己要用 logger)──┐
                                            │  若反向 import 中枢
foundation/storage/sqlite.py ──(自己要用 logger)─┤  就成环：
                                            │  settings → 中枢 → logging_setup
                                            │            → storage.sqlite → settings（半初始化）
                                            ▼
foundation/observability/logging_setup.py（装配层，依赖 settings + storage.sqlite）
```

结论：**中枢不能是一个"依赖项目代码"的模块**，否则 `foundation` 最底层的两个文件永远收编不了。

因此合并的落点：

```
src/agentnodes/foundation/
  logger.py                    ← 新增：零依赖内核（纯 stdlib，零项目内 import）
                                  get_logger / Logger 门面 / deco 计时   ← M3 + M4 合并进来
  observability/
    logging_setup.py           ← 既有：装配层（configure_logging / dual_log）—— 不动
    trace.py                   ← 既有：上下文层 —— 不动
```

| 层 | 依赖 | 谁用 |
|---|---|---|
| `foundation/logger.py`（内核） | **仅 stdlib** | 任何模块都能 import，**含 settings / storage.sqlite 本身**（无环） |
| `foundation/observability/`（装配 + 上下文） | settings / storage.sqlite | 进程入口、测试 |

**对外一个获取入口**：`from agentnodes.foundation.logger import get_logger`
（不放在 `observability` 包里 —— 该包的 `__init__` 会连带装配备层 → 对 `settings.py` 成环）。

**否决的备选**：

| 备选 | 否决理由 |
|---|---|
| 全部塞进 `logging_setup.py` 一个文件 | `settings.py` / `storage/sqlite.py` 收编即成环（要么留 2 处豁免，要么制造隐性环） |
| 把 `observability/__init__.py` 改成 PEP 562 懒加载 | 要改 17 个消费方的 import 语义 —— 违反「精准修改」，收益不匹配 |
| 保留 `foundation/logger.py` 但让内核 import `observability` | 同上成环 |

### 3.4 实例语义：同名同实例，**不是**全进程单实例

收编后的实例数与语义（实测）：

| 问 | 答 | 依据 |
|---|---|---|
| `get_logger("x")` 调两次是同一对象吗 | **是** | stdlib `LoggerManager.loggerDict` 注册表保证（实测 `a is b → True`）；内核还要**对门面再缓存一层**，保证 `get_logger("x") is get_logger("x")` 严格成立（不能每次新建门面壳） |
| 收编后一共多少个实例 | **158 个**（= 模块全名去重数；160 处取 logger 落在 158 个模块名上） | logger 对象是轻量壳，真实开销在 handler/formatter —— 158 个实例的成本可忽略 |
| 能做成**全进程一个**实例吗 | **不能**，三条硬约束 | 见下 |

**为什么不能全进程单一实例**（每条都有实测依据）：

| # | 约束 | 实测证据 |
|---|---|---|
| 1 | `caplog` **指名 logger 名**过滤/设级别 | **37 处**（如 `caplog.at_level(logging.WARNING, logger="agentnodes.apps.goal.event_bus")`、`caplog.set_level(logging.INFO, logger="agentnodes.interfaces.api.session.service")`）—— 全进程同名后，这些过滤会全部命中同一对象，断言语义被破坏 |
| 2 | JSON 输出的 `logger` 字段是排障主键 | `JsonFormatter` 把 `record.name` 写进 payload；单实例后所有模块同名 → 日志失去来源 |
| 3 | 按包分级依赖层级名 | `AGENTNODES_LOG_LEVELS=kernel=DEBUG` 靠 `agentnodes.kernel.*` 前缀匹配 `setLevel` —— 单实例没有层级可言 |

> 参考实现本身也不是「全进程单实例」：`LoggerHelper(app_name)` 是**按 app 名**分实例的（它的「单一」= 每个 app 一个）。
> 本仓的 app 名 = 模块名 → 158 个 logger 名 = 158 个实例，**这正是它的语义**。

**进程级单例的落点**：内核提供 `APP_LOG = get_logger("agentnodes")`（单例，命名避开类名 `Logger`）
供库级/进程级消息使用；
模块级仍按 `get_logger(__name__)` 取 —— 两者共享同一套装配与同一套 handler。

---

## 4 · 目标形态

### 4.1 对外 API

```python
# foundation/logger.py（零依赖内核）
def get_logger(name: str | None = None) -> Logger:      # 唯一获取入口
class Logger:                                           # 门面（复用参考实现的 LoggerHelper 形态）
    name: str
    def debug/info/warning/error/critical/exception/warn(...)   # 转发给 stdlib logger
    def deco(self, level="info", timer=True, extra_name="")    # 计时装饰器（M4 升级版）
APP_LOG = get_logger("agentnodes")                       # 进程级门面（单例；M2 的用途由 stdlib logger 注册表承担；命名避开类名 Logger）

# foundation/observability/logging_setup.py（既有，不动）
configure_logging(surface, *, level, json_output, log_file, force, stream)
dual_log(module, msg, *args)
```

**调用点形态不变**：`logger.info("...")` / `logger.warning("...")` / `logger.exception("...", exc_info=True)` 全部照旧；
`caplog` 拿到的 `record.name` 仍是 `agentnodes.kernel.session.service` 这类模块名。

### 4.2 收编后的 diff 形态（164 处，每处一行）

```diff
- logger = logging.getLogger(__name__)
+ from agentnodes.foundation.logger import get_logger
+ logger = get_logger(__name__)
```

### 4.3 包级日志级别（补齐 warning/debug 主力场景）

- 现状：`AGENTNODES_LOG_LEVEL` 只有全局一档，想开 `kernel` 的 DEBUG 得把全局开到 DEBUG（噪音）。
- 方案：**一个** env 承载多档，避免 ENV_TABLE 膨胀：
  `AGENTNODES_LOG_LEVELS=kernel=DEBUG,session=INFO`（需登记进 `foundation/settings.py::ENV_TABLE`，R-002）。
- 落地位置：`configure_logging` 内按前缀应用（`logging.getLogger("agentnodes.kernel").setLevel(DEBUG)`）。
- 备选（否决）：配置文件 `~/.agentnodes/config.yaml` —— 要动 LLM 三路径链那条配置链，代价远大于收益。

---

## 5 · 收编策略（分批 + 纪律）

### 5.1 批次

| 批 | 范围 | 处数 | 说明 |
|---|---|---|---|
| 1 | 内核 `foundation/logger.py` + 单测 + 门禁 + 棘轮基线 | 新增 | 不改既有行为；现有 `test_logging_setup.py` / `test_trace*.py` 必须全绿 |
| 2 | `foundation/` | 26 | 含 `settings.py` / `storage/sqlite.py`（**验证无环**：本批是最容易踩循环依赖的一批） |
| 3 | `kernel/` | 92 | 主体量；`kernel/session` 是 R-014 刚动过的，单独跑其测试 |
| 4 | `apps/` + `interfaces/` | 45 | 入口层；4 个 `configure_logging` 调用点重点回归 |

### 5.2 机械替换纪律（R-011 · 本仓已有明确失败记录）

每批必须做三件事，缺一不可：

1. **逐文件 `git diff` 复核**（不靠 grep 找残留）——本仓 U5 批曾用脚本批量改测试，出了 3 类错，其中 2 类症状是**假绿**；
2. `python -m pytest --collect-only -q` 确认**收集数没有意外下降**（锚点只查下限，语法错误会体现为 error）；
3. 跑该批测试**看耗时**——命中真网络时耗时会暴涨 10 倍以上。

### 5.3 收编风险点

| 风险 | 处置 |
|---|---|
| **循环 import**（本轮头号风险） | 内核零项目依赖（§3.3）；批 2 先收编 `foundation/settings.py` + `storage/sqlite.py` —— 若这批 import 报错，说明内核有依赖，立刻回退 |
| `caplog` 77 处按 logger 名断言 | **模块名保持 `__name__`**；模块级变量名保持 `logger`（现有测试 0 处 monkeypatch logger） |
| 3 处无参 `getLogger()`（root logger） | 语义与「模块 logger」不同 → **不改**，门禁豁免 `observability` 包内 |
| 门面 vs 直取的唯一行为差异 | 门面在获取时抓住 stdlib logger 对象并转发 → **替换 `logging.Logger.manager` 里的同名条目不影响已获取的门面**；测试要 monkeypatch 时替换模块变量 `logger`（单测要钉住这条） |
| `src/` 行数变化 → 架构图漂移 | 每批后跑 `scripts\arch\sync_architecture_diagram.py --write`（R-007 §2.3） |
| 新增 env `AGENTNODES_LOG_LEVELS` 触发 R-002 门禁 | 同步登记 `foundation/settings.py::ENV_TABLE` |

---

## 6 · 门禁（棘轮，防回流）

新增 `scripts/arch/check_logging_entry.py`，形状照 `scripts/arch/check_*.py`：

| 检查 | 判定 |
|---|---|
| `src/` 内裸 `logging.getLogger(` / `getLogger(`（未经内核） | 报错并给出文件:行号 |
| 豁免 | `foundation/logger.py` 自身 + `observability` 包内 3 处 root logger 用法 |
| 棘轮基线 | `tests/baselines/logging_entry.json` 存当前残留数，**只许减不许增**（照 `check_framework_numbers.py` 的形状） |
| 自检 | 按 R-019，注入一条违规 → 门禁必须红（不能只看「当前 0 违规」） |
| 接线 | 挂进 `scripts\batch_gate.cmd`（第 10 步） |

---

## 7 · 验收标准（逐批）

| 批 | 验收 |
|---|---|
| 1（内核 + 门禁 + 棘轮） | `get_logger` 单测（命名规范化 / 前缀放行 / **同名同实例：`get_logger("x") is get_logger("x")`** / `deco` 成功与异常路径 / 门面转发不吞 `exc_info`）；**内核零项目 import 的断言**（AST 检查）；门禁注入违规必红；**`tests/test_logging_setup.py` + `tests/test_trace*.py` 全绿** |
| 2（foundation） | `foundation/` 内 0 处裸 `getLogger`；**`settings.py` / `storage/sqlite.py` import 后无环**（能单独 import）；collect 数不降 |
| 3（kernel） | `kernel/` 内 0 处裸 `getLogger`；该批测试全绿 |
| 4（apps + interfaces） | `src/` 内 0 处裸 `getLogger`（除豁免）；全量 pytest 0 failed（`-n 12 --dist loadgroup`）；`scripts\batch_gate.cmd` ALL GREEN；架构图一致 |

**全批通用**：全量 pytest + batch_gate + 架构图同步（R-007 三件套），顺序不可省。

---

## 8 · 风险与已知代价

| 风险 | 量级 | 处置 |
|---|---|---|
| 循环 import | **高**（本轮头号风险） | 内核零依赖；批 2 优先收编最底层的两个文件，先证无环 |
| 159 个文件被机械改动，diff 巨大、review 困难 | 大 | 按批拆分，每批一个 commit；只改获取那一行，不碰调用体 |
| 收编后**行为**变化（若门面吞了 kwargs） | 中 | 门面只做**转发**，不吞 `exc_info` / `stacklevel` / `extra`；U5 单测钉住 |
| 新增 env `AGENTNODES_LOG_LEVELS` 触发 R-002 门禁 | 小 | 同步登记 `foundation/settings.py::ENV_TABLE` |
| 「中枢」分两个文件（内核 + 装配层） | 小 | 能力互补不重复；对外一个获取入口；文件头 docstring 互相指路 |
| 401 处 print 未治 | — | 明确不在本轮；另行排期 |

---

## 9 · 明确不做的事

- 不重写 `configure_logging` / `TraceFilter` / `JsonFormatter`（已有契约与 4 入口接线）。
- 不改 logger 命名规则（`__name__` → 保持）。
- 不做通用结构化字段（`extra=` 实测 0 处诉求）。
- 不治理 `print`（401 处，另行排期）。
- 不引入第三方日志库（rules.md §7 精神）。
- 不做 per-module logger 类（每个模块一个独立实例）——只统一**获取入口**。
- 不引入参考实现的 `sql()` / `status()` / `Singleton` / 自带 handler 装配（M1/M2/M5 作废）。

---

## 10 · 回滚

- 批 1（内核）：`git revert` 即回到现状（新增文件，不改既有行为）。
- 批 2–4（收编）：每批独立 commit，`git revert <batch>` 只回退该批的获取行；棘轮基线在同一 commit 内回退。
- 门禁：门禁是最后接入 `batch_gate.cmd` 的一步 —— 若误报，先摘接线再修实现。

---

## 附录 A · 实测口径

```bat
:: 获取层散点（164 处 / 159 文件）+ 其它 logging 用法
python tmp/probe/logging_inventory.py
:: 写法形态（160 __name__ / 3 无参 / 1 'agentnodes'）+ 门禁脚本形态
python tmp/probe/logging_shapes.py
:: 调用形态（level 分布 / exc_info 61 / extra 0）
python tmp/probe/logging_extra.py
:: 收编风险点（无参 getLogger 落点）
python tmp/probe/logging_risks.py
:: 合并落点的循环依赖核查（settings / storage.sqlite / 中枢依赖链）
python tmp/probe/logging_cycle.py
:: 层归属（foundation 前缀已登记 → 新文件自动 L4）
python tmp/probe/layer_matrix_foundation.py
```

> 探针脚本在 `tmp/`（gitignored）。若要长期留存，应提升到 `scripts/arch/` 或 `tests/` 下。
