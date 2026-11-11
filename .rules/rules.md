# AgentNodes L1 项目规则

> **层级**：L1（本项目规则）。上位：`../AGENTS.md`（L0 通用原则）。
> **审查**：2026-11-11 · 下次 2027-02-11
> 只写「换仓库就不成立」的内容：路径、命令、框架专名、本仓教训。

---

## 1. 权威路径与分层

### 1.1 四层单向依赖（L1→L2→L3→L4）

**R-001. 四层单向依赖** · MUST

- **触发**：新增/搬动 `src/agentnodes/` 下的任何文件。
- **做法**：`foundation`（L1）← `kernel`（L2）← `apps`（L3）← `interfaces`（L4），只许向下 import。在 `scripts/arch/layer_matrix.py::LAYER_MEMBERSHIP` 登记新文件的层级。
- **失败**：门禁 `check_dangling_imports.py --layers` 报上行违规；未登记的文件判不出层级归属。
- **来源**：L2 2026-11-11（架构图同步）；设计 `layered-skeleton-design-20261103.md`。

`src/agentnodes/` 按四层组织：

| 层 | 目录 | 职责 |
|---|---|---|
| **L1** | `foundation/` | 零业务依赖的基础设施（paths / settings / llm 客户端 / 存储）|
| **L2** | `kernel/` | 框架内核（agent loop / gateway / session / workflow / runtimes）|
| **L3** | `apps/` | 业务能力（goal / agent_plugins）|
| **L4** | `interfaces/` | 交付界面（cli / api / webui）|

**已知例外**：`L4→L3` 的 import 由 `check_dangling_imports.py --layers` 放行（interfaces 消费 apps 是设计意图）。上行（L4→L1/L2、L3→L1/L2）一律为 0。

### 1.2 配置读取唯一入口

**R-002. 环境变量只经 settings 读** · MUST / 禁止直读

- **触发**：任何需要读环境变量的代码。
- **做法**：① 在 `foundation/settings.py` 的 `ENV_TABLE` 声明（`_s`/`_i`/`_f`/`_b`/`_flag`）；② 经 `settings.get_str()`/`get_int()`/`get_float()`/`get_bool()` 读取。
- **失败**：`check_env_access.py`（batch_gate 第 8 步）报错。
- **来源**：L2 2026-11-09（批 A/B'）；设计 `param-naming-unification-20261109.md`。

**禁止**在 `src/` 里直接 `os.environ.get("AGENTNODES_...")`。

门禁判定规则：
- 已登记的变量被直接读 → 报错（应走 settings）；
- 未登记但带项目前缀（`AN_` / `AGENTNODES_` / `QNODES_` / `QUANTNODES_` / `SR_`）→ 报错；
- 生态变量（`OPENAI_API_KEY` / `NO_COLOR` / `HOME` / `LANGCHAIN_*`）在 `EXTERNAL_ENV_VARS` 里，放行。

> **门禁只认三种形态**（字面量直读 / 常量间接 / 常量经函数参数流转）。以下**逃过扫描**：映射参数化（`f(env={"AN_X": "1"})`）、跨模块参数传递。**门禁绿 ≠ 无直读**——详见 §3.6。

### 1.3 环境变量命名与旧名

**R-003. 旧名降级为诊断事实，不做读取回退** · MUST

- **触发**：改名 / 删除兼容层 / 新增开关。
- **做法**：规范名进 `ENV_TABLE`；旧名进 `_LEGACY_NAMES`（→ `_LEGACY_CATALOG`，**诊断层**）。仍生效的历史名登记 `LEGACY_LIVE_ENVS`，由各模块经 `settings.get_legacy()` 显式直读（传未登记的名字会 `KeyError`）。
- **失败**：旧名静默回落默认值（设了不生效）。**删兼容层前必须先有只读审计命令**——见 R-004。
- **来源**：L2 2026-11-09（E-1/E-2）、2026-11-11（V4-3）。

- **规范名**：`AGENTNODES_<DOMAIN>_<PARAM>`。
- **旧名不进读取层**：`_resolve()` 只认规范名。
- **审计**：`agentnodes config audit-legacy` 只读，零副作用。
- 新增旧名时同步更新 `legacy_risk()` 的风险档和 `_LEGACY_REMEDY`（**取值需要翻译的必须给精确改法**，如 `SKELETON=0` → `ENGINE=legacy` 而不是 `ENGINE=0`）。

**R-004. 删兼容层前先建只读审计** · MUST

- **触发**：要删除任何兼容层 / 回退路径。
- **做法**：先落地只读的审计/诊断能力，并确认它**不依赖被删的那一侧**；再删。
- **失败**：删完之后出问题没有观测手段——「删掉 X」与「删掉能发现 X 的机制」是两件事。
- **来源**：L2 2026-11-09（批 E-1 的设计依据）。

### 1.4 数据目录

**R-005. 目录名只从 PROFILES 取** · 禁止硬编码

- **触发**：任何需要拼 `.agentnodes` / `.quantnodes` 之类目录的地方。
- **做法**：从 `foundation/paths.py::PROFILES` 取 `home_dir_name`。
- **失败**：`check_path_literals.py`（batch_gate 第 3 步）拦下；profile 改名时链不跟随。
- **来源**：L2 2026-11-09（批 D 初版被门禁拦下）；设计 `data-dir-unification-20261109.md`。

### 1.5 LLM 配置读取链

**R-006. LLM 配置三路径链，坏文件不跳过** · MUST

- **触发**：读写 LLM 配置。
- **做法**：按下列顺序，**第一个存在的赢**，不做字段深合并。
- **失败**：跨文件合并会让"值从哪来"不可诊断；坏文件被跳过会看起来像"没配"。
- **来源**：L2 2026-11-11（批 D：`~/.quantnodes/llm.json` 曾完全读不到）。

```
AGENTNODES_LLM_CONFIG（单文件覆盖，命中即不建链）
  ↓ 否则
~/.agentnodes/config.yaml      ← 新格式，优先
~/.agentnodes/llm.json         ← agentnodes 老位置（写入目标）
~/.quantnodes/llm.json         ← quantnodes 契约
```

文件存在但解析失败 → 返回 `{}` + warn，**不跳过**。

`api_key` 在配置文件里只写 `env:VAR` 引用，**永不内联明文**。

---

## 2. 验证命令（改完必须跑）

**R-007. 提交前三件套：全量 pytest + batch_gate + 架构图** · MUST

- **触发**：任何 `src/` 或 `tests/` 改动，提交前。
- **做法**：见 §2.1–§2.3；顺序是先全量（慢但全），再 batch_gate（快但只查结构），最后架构图。
- **失败**：`tests/test_scripts_arch.py::test_architecture_diagram_matches_code` 会在全量里红；门禁违规会让 CI 红。
- **来源**：L2 2026-11-11（每批都因架构图漂移红过一次）。

### 2.1 单文件→全量

```bat
:: 受影响文件（改完一处就跑）
C:\Users\1\AppData\Local\Programs\Python\Python313\python.exe -m pytest tests\test_xxx.py -q -p no:hypothesispytest -p no:hypothesis

:: 全量
C:\Users\1\AppData\Local\Programs\Python\Python313\python.exe -m pytest tests -q -p no:hypothesispytest -p no:hypothesis
```

**`-p no:hypothesispytest -p no:hypothesis` 不是可选的**：本机 Application Control 策略拦 hypothesis 的原生 DLL（`_native`），不加这两个参数 pytest 在插件加载阶段就死，**不打印任何 summary**。

### 2.2 batch_gate（提交前）

```bat
scripts\batch_gate.cmd
```

9 步（第 9 步可选）：

| 步 | 检查 | 失败含义 |
|---|---|---|
| 1 | pytest collect 锚点 | 收集数跌破 `tests/baselines/collect_anchor.json` 的 `min_collected`，或 errors 数变了 |
| 2 | `check_dangling_imports.py --layers` | 跨层 import 方向违规 |
| 3 | `check_path_literals.py` | 硬编码品牌目录 |
| 4 | `check_capability_requires.py` | capability 契约不一致 |
| 5 | `check_semantics_placement.py` | 语义放错层 |
| 6 | `check_tool_refs.py` | 工具名引用了不存在的工具 |
| 7 | `check_kernel_nouns.py` | kernel 层出现业务名词 |
| 8 | `check_env_access.py` | 环境变量绕过 settings |
| 9 | `graph_layer_audit.py`（需 `--graph`）| 符号级层级泄漏 |

**双解释器**：`PYTEST_PI` = Python 3.13（跑 pytest），`PROBE_PI` = `D:\tools\python312\python.exe`（跑 arch 门禁，3.13 缺 parser 依赖）。

**锚点更新**：`scripts\batch_gate.cmd --update-anchor`。只在**有意**增减测试数时用；锚点是"收集数只许涨不许跌"的方向性闸。

### 2.3 架构图同步

```bat
D:\tools\python312\python.exe scripts\arch\sync_architecture_diagram.py          :: 检查
D:\tools\python312\python.exe scripts\arch\sync_architecture_diagram.py --write  :: 修
```

**任何改变 `src/` 行数或文件数的改动都要跑**。`tests/test_scripts_arch.py::test_architecture_diagram_matches_code` 会拦漂移。

### 2.4 提交前清单

1. `git status` / `git log --oneline -5` —— 确认不覆盖他人改动；
2. 全量 pytest 0 failed；
3. `scripts\batch_gate.cmd` ALL GREEN；
4. 架构图一致（若动了 `src/`）；
5. 相关设计文档同步更新（见 §4）。

---

## 3. 测试约定

### 3.1 测试替身必须挂满所有入口

**R-008. LLM 替身要挂 chat + achat 两个入口** · MUST

- **触发**：任何给 `AgentLoop` 挂 LLM 测试替身的测试。
- **做法**：用 `tests/llm_client_mock.py::patch_llm_client(loop, mock.chat)`；流式用 `as_async_gen()`；异步替身必须 `await asyncio.sleep(...)` 而不是 `time.sleep(...)`。
- **失败**：未挂的入口**打到真实 HTTP**（带重试）——症状是"答案变空"或整条用例挂住，不是干脆的失败。
- **来源**：L2 2026-11-11（V4-2：14 个文件 / 57 处挂载点全中招）。

`AgentLoop` 的 LLM 客户端有 **`chat` / `achat` 两个入口**（流式另有 `stream` / `astream`）。只 mock `chat` 会让 async 路径走真客户端。

**异步替身要真异步**：`achat` 里用 `time.sleep` 会阻塞事件循环，取消信号送不进去，`asyncio.wait_for` 就不抛 `TimeoutError`——超时用例测不到东西。

### 3.2 引擎是 async-first，pytest 里要 subprocess

`ReActSkeleton` 是 async 引擎；pytest 的事件循环管理与 `asyncio.run()` 冲突（实测挂起）。需要跑真实引擎的测试用 **subprocess 模式**（参考 `tests/test_kernel_standalone.py`、`tests/test_agent_loop_async.py` 的 subprocess 写法）。

### 3.3 禁止空集合通过

**R-009. 断言必须有正向前提，且不许靠对照物** · MUST

- **触发**：写"某行为发生"的测试；要删除被对照的那一侧之前。
- **做法**：① 先断言场景**真的触发**了目标行为（否则是空集合）；② 删对照物**之前**把 `A == B` 改成对 A 的**绝对值**断言（含事件序列，不只是结果字段）。
- **失败**：两边同时错成一样的值也过；删掉 B 后 `assert X == X` 恒真。
- **来源**：L2 2026-11-09（批次 13：skeleton 丢 6 项能力全量仍绿）、2026-11-11（V4-1：只比 LoopResult 漏掉 3 条事件差异）、2026-11-11（V4-4a：删 legacy 前必须先转断言）。

两条硬规则：

- **断言必须有正向前提**。例：测"预算耗尽"必须先在别处断言预算真的生效了。`assert legacy == skeleton` 在两边都是 `0` 时也会过。
- **反对偶**：删掉对照物之前，把 `A == B` 改成对 A 的**绝对值**断言。否则删掉 B 之后测试变成"自己跟自己比"。
- **比事件序列，不只比结果字段**：`LoopResult` 是结果摘要，事件序列是过程记录。摘要对齐不代表过程对齐——V4-1 实测三条引擎差异**全是**只在事件层可见的。

### 3.4 已知差异要显式登记 + 反向断言

允许的差异写成清单，并**双向**钉：

- 清单外的差异出现 → 红；
- 清单内的差异被悄悄修掉 → 也红（否则清单变成"悄悄放过真实差异"的漏洞）。

参考实现：`tests/test_engine_parity.py`（双引擎逐字段对拍，带已知差异清单的写法）。**清单本身也要跟着被删的那一侧退休**——审计/差异清单不能只建不退。

### 3.5 目标状态用 `xfail(strict=True, raises=AssertionError)`

**R-010. 不提交红测试，用 strict xfail 钉目标状态** · MUST

- **触发**：需要钉"这还没实现/还没落地"的状态。
- **做法**：`@pytest.mark.xfail(strict=True, raises=AssertionError, reason="...")`；落地后 XPASS → 判失败 → 强制摘标记。
- **失败**：直接提交红测试（违反"不提交红色状态"）；或 `raises` 缺省时把"harness 坏了"（subprocess 崩溃 / `pytest.fail`）吞成 xfail。
- **来源**：L2 2026-11-11（V4-1 → V4-2 的落地流程）。

### 3.6 门禁类测试扫描的是"读的形状"，不是"读的事实"

**R-011. 门禁绿 ≠ 无违规，改名/批量替换后必须做 diff 上下文复核** · MUST

- **触发**：批量改名、机械替换、删名。
- **做法**：替换后做**确定性上下文比对**（改前 vs 改后逐文件的非预期变更），不靠 grep 找残留。
- **失败**：grep 匹配的是**文本**，替换改的是**语义位置**。当同一字符串既是环境变量名又是局部变量名时，文本层面无法区分——批 E-2 实测把 `STATIC_DIR` 误改成 `AGENTNODES_STATIC_DIR`，而那段是死代码路径，**测试全绿**。
- **来源**：L2 2026-11-11（E-2 扫尾）。

`check_env_access.py` 只认三种形态：字面量直读、常量间接、常量经函数参数流转。以下会**逃过扫描**：

| 逃逸形态 | 例子 |
|---|---|
| 映射参数化 | `LLMConfig.load(env={"AN_XXX": "1"})` |
| 函数参数流转（跨模块）| `def f(environ): environ.get(NAME)` |
| 常量间接（2026-11 已补）| `os.environ.get(_CONST)` |

**结论**：门禁绿 ≠ 没有直读。

---

## 4. 文档纪律（Doc First）

**R-014. Loop 结构变更前必须先给方案** · MUST

- **触发**：新增/删除任何 loop 相关的数据结构（Spec / Config / Context / 骨架 /
  Decider / 停止原因），或要动 `loop.py` / `skeleton.py` 的生命周期。
- **做法**：① **先出方案**（改什么、为什么、影响面、回滚方式），等确认；
  ② 方案里必须写清"这次改动的验收标准是什么"（哪个断言会从红变绿/从绿变红）；
  ③ 不要边改边发现——先摸清"有几个出口、几处调用点、谁在消费"。
- **失败**：越改越乱。已有实证（2026-11-11）：
  - `LoopStrategy` 的 L7–L8 从未执行，抽象空转；
  - `LoopSpec` 零消费者存活两个月；
  - `StopPolicy` 的唯一调用方变成孤儿；
  - 一次"统一 loop 规范"的尝试引入了 204 行协议 + 405 行**有缺陷**的契约测试
    （测试里 `phases.append(CLEANUP)` 伪造了实现没有的阶段，导致永远绿）。
- **来源**：L2 2026-11-11（用户要求：每次删减前先给方案）。

**R-016. 删 fallback 之前，先有覆盖「所有出口」的网** · MUST

- **触发**：删除一条执行路径（引擎、分支、兼容层）。
- **做法**：先穷举**旧路径的所有出口**（正常/异常/提前 return），逐个确认新路径
  在这些出口上的保证（资源释放、事件发射、结果字段）。不是"全量测试绿"就算过。
- **失败**：删掉有 6 个出口全部收尾的 legacy 引擎，换成一个**有 2 个 EXIT 出口
  不收尾**的骨架——而全量测试 7166 条全绿，因为它们只比结果字段、不比出口收尾。
- **来源**：L2 2026-11-11（V4-4b 的实测教训）。

**R-015. 删任何"接线对象"之前必须满足四项** · MUST

- **触发**：准备删除一个还在被引用/被依赖的结构（引擎、配置层、兼容层、抽象）。
- **做法**：四项**全部**满足才动手——
  ① 规范已定（谁接管、边界在哪）；
  ② 接线已完成（新结构真的在跑，不是"设计好了"）；
  ③ 完整测试（能力的**绝对值**断言，不是"两个实现一致"）；
  ④ 审计/诊断能力可独立发现"还有人在依赖它"。
- **失败**：删掉的是"还没有替代品的唯一路径"。批 E 的教训是"审计命令不能
  随兼容层一起死"；`StopPolicy` 的教训是"设计了但没接线就删，等于白设计"。
- **来源**：L2 2026-11-11（用户明确要求：接线 + 完整测试之后才删）。

**R-012. 先文档后代码，且必须回填实测数字** · MUST

- **触发**：任何改变行为的改动。
- **做法**：先落 `docs/design/<topic>-<YYYYMMDD>.md`；批次完成后回填实测数字（与预计并列，不等就写明差在哪）。
- **失败**：文档与代码两头各改一版，无所适从；估算当成实测传给下一批。
- **来源**：L2 2026-11-11（V4 各批的进度表）。

### 4.1 先文档后代码

**改行为之前**先落在 `docs/design/<topic>-<YYYYMMDD>.md`。文档里必须能回答：

- 现状**实测**（不是印象）——带文件行号或命令输出；
- 为什么这样改（含被否掉的替代方案与理由）；
- 每步的回滚方式；
- 明确不做的事。

**证据可追溯**：每个"已删除 / 已保留 / 行数"断言都要能指向一条 `git log` 或一次实测输出。

### 4.2 文档与代码冲突 → 先定「以谁为准」

不许两头都改一版。定完在文档里写明判定依据。

### 4.3 批次完成后回填实测数字

设计文档里的"预计"要和落地后的"实测"并列（例：`docs/design/param-naming-unification-20261109.md` 里各批次的"已执行结果"表格）。**估算与实测不符时，写明差在哪**——这是下一批不重复踩坑的唯一依据。

### 4.4 命名

`docs/design/<kebab-topic>-<YYYYMMDD>.md`。决策类文档标注**性质**（决策 / 实施设计 / 调研 / 复盘）。

---

## 5. 提交与分支

**R-013. 一批准一个 commit，message 写"为什么"** · MUST

- **触发**：每次提交。
- **做法**：分支 `platform-kernel`（不在 `main` 直接提交）；中文 message `<type>(<scope>): 说明`；body 写 现状实测 → 判断 → 改法 → 验证结果。
- **失败**：只写"改了什么"的 message 六个月后没人看得懂；一批拆成多个无关联 commit 无法整体回滚。
- **来源**：L2 2026-11-11（V4 系列 6 个 commit 的形状）。

- **分支**：工作分支 `platform-kernel`，主分支 `main`。
- **commit message 中文**，`type` ∈ `feat` / `fix` / `docs` / `test` / `refactor` / `chore`。
- **一批准一个 commit**（每批独立可回滚）。批次内部再拆步骤时，每步一个 commit。
- **不提交红色状态**（见 §3.5）。

---

## 6. 本仓专有陷阱

| 陷阱 | 症状 | 处理 |
|---|---|---|
| hypothesis 原生 DLL 被策略拦 | pytest 在插件加载阶段死，无 summary | 永远带 `-p no:hypothesispytest -p no:hypothesis` |
| PyMuPDF `_mupdf` DLL 被拦 | `read_document` 工具不可用 | `check_tool_refs.py` 用 `env_gated_tools()` 区分"环境缺失"与"未实现" |
| playwright chromium rev 1254 被拦 | `BrowserType.launch: spawn UNKNOWN`；rev 1234 正常 | 环境事件，非代码问题。用 stash 对照排除自身改动 |
| base64 填充位伪随机 | 篡改签名的测试偶发通过（43 字符 = 258 bit vs 256 bit）| 别改最后一个字符；改中间的（如 index 5）|
| `WindowsPath` vs `PosixPath` | 断言路径相等在 Windows 上失败 | 用 `Path(...)` 规范化或比较 `str()` |
| 中文输出乱码 | 控制台 GBK | 脚本里 `sys.stdout.reconfigure(encoding="utf-8")`；`findstr` 而非 `grep` |

---

## 7. 明确不做

- **不引入第三方配置库**（pydantic-settings 等）——手写声明表 + 类型转换足够。
- **不把构造参数并进 settings**——`AgentLoop.__init__` 的参数是运行时对象（registry / memory / hooks / strategy），不是可序列化的进程配置。判据见 `docs/design/param-naming-unification-20261109.md` §5.1。
- **不改外部生态约定的 env**（`OPENAI_API_KEY` / `NO_COLOR` / `HOME` / `LANGCHAIN_*`）。
- **不给业务 CLI 参数加品牌前缀**（`--strategy` / `--symbol` / `--metric` 等）。
- **不重建 GraphEngine / Ticket / fence**——编排层与执行层分离是对的。
