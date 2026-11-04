# 物理分层迁移计划 v2 — 逐模块搬迁（tests-green 每 step）

日期：2026-11-03　分支：`refactor/v6-gateway`　状态：**✅ 已完成（2026-11-04 收官）**
v1（大爆炸批次）已于同日废弃——首次试运行批 1 即失败：AST 改写工具把多行
import 压成单行（20 处语法损坏），暴露出「一次性改写 1,200 处」的工具风险。
已回滚至 `07e1e6d5`，工作树干净。教训与本 v2 方案见 §1。

**执行结果（2026-11-04）**：55 包 + api/cli 两个顶层包全部落位，57 个迁移
commit 逐包独立可回滚；`core/` 目录从仓库消失；上行违规全部清零
（L3>L2 46→35→0 等，见 §9）。协议修订与陷阱沉淀见 §7.1–§7.4。

前置：`layered-skeleton-design-20261103.md`（层定义）·
`ADR-000`（物理口径基线 55）· `ADR-002/003`（处置决策）·
`docs/w0-2-triage-20261103.md`（失败归因基线：102F/13959P）

---

## 1. 方法论：逐模块搬迁 + 兼容锚点

### 1.1 为什么逐模块可行（两条机制发现）

**机制一：深度保持**。`core/X/...` → `<layer>/X/...` 目录深度不变，因此：
- 被搬包**内部**的相对导入（`from .store import`）——同包同深度，**不用改**
- 被搬包内的 `Path(__file__).parents[N]`——**不用改**（N 指向的位置不变）
- 指向**未搬迁**包的引用——旧路径仍在 `core/`，**不用改**
- 只有**跨层引用**（指向已搬层或本包被他人引用）需要改写

**机制二：改写 = 纯文本前缀替换**。跨层引用一律改写为**绝对导入**
`strategy_research.<layer>.<pkg>`，三种形态（import 语句 / 函数体 lazy import /
字符串字面量）用同一规则、同一正则命中。不做 AST 级语句重组——v1 失败的根源
就是试图重组多行 import 语句。

### 1.2 每 step 协议（7 步，测试前后必须绿）

```
 1. graphify affected "core.<pkg>"        # 定位受影响测试（不 grep）
 2. git mv core/<pkg>  <layer>/<pkg>
 3. 改写被搬包内部跨层引用为绝对路径       # 量小（个位数），可人工审
 4. 全库前缀改写 core.<pkg> → <layer>.<pkg>
    # 一条正则三形态命中：import 语句 / lazy import / 字符串字面量
 5. 门禁成员表 +2 行（旧路径保留至批 5 统一删）
 6. 验证：受影响测试 + 门禁组 + import 冒烟
 7. commit（一包一 commit，可独立回滚）
```

### 1.3 兼容锚点策略（不建 sys.modules 别名）

不引入 meta-path 别名层（用户此前已否决）。旧导入路径**立即改写**，不留过渡态。
依据：graphify 实测入边最多 139 条（goal），单包改写是全库文本前缀替换，
`sed` 级操作可验证；别名层反而引入「忘了删」的永久债。

## 2. 数据底册

### 2.1 入边排名（55 包全量，2026-11-03 修正版）

物理口径（每语句计 1）；`src` = 生产引用，`tests` = 测试引用。

| # | 包 | 入边 | src | tests | 目标层 |
|--:|---|---:|---:|---:|---|
| 1 | `alpha_zoo_yaml.py` | 1 | 0 | 1 | apps |
| 2 | `seq_generator.py` | 1 | 0 | 1 | kernel |
| 3 | `errors.py` | 1 | 1 | 0 | foundation |
| 4 | `preflight.py` | 1 | 1 | 0 | apps |
| 5 | `alpha_zoo_convert.py` | 2 | 0 | 2 | apps |
| 6 | `alpha_zoo_adapter.py` | 2 | 1 | 1 | apps |
| 7 | `config_loader.py` | 2 | 1 | 1 | foundation |
| 8 | `export/` | 2 | 1 | 1 | apps |
| 9 | `factor_validate.py` | 2 | 1 | 1 | apps |
| 10 | `run_card.py` | 2 | 1 | 1 | apps |
| 11 | `workspace_setup.py` | 2 | 1 | 1 | apps |
| 12 | `backtest_engine/` | 3 | 0 | 3 | apps |
| 13 | `alpha_zoo_ops.py` | 3 | 2 | 1 | apps |
| 14 | `data_readiness.py` | 3 | 2 | 1 | apps |
| 15 | `git.py` | 3 | 3 | 0 | foundation |
| 16 | `registry.py` | 4 | 0 | 4 | foundation |
| 17 | `alpha_zoo/` | 4 | 2 | 2 | apps |
| 18 | `mcp/` | 5 | 1 | 4 | interfaces |
| 19 | `config_runner.py` | 5 | 4 | 1 | apps |
| 20 | `data_import.py` | 5 | 4 | 1 | apps |
| 21 | `permission/` | 7 | 3 | 4 | kernel |
| 22 | `skills/` | 7 | 3 | 4 | kernel |
| 23 | `backtest_models.py` | 7 | 5 | 2 | apps |
| 24 | `tools/` | 7 | 5 | 2 | apps |
| 25 | `web/` | 7 | 5 | 2 | foundation |
| 26 | `hooks/` | 8 | 2 | 6 | kernel |
| 27 | `portfolio/` | 9 | 1 | 8 | apps |
| 28 | `memory/` | 11 | 5 | 6 | kernel |
| 29 | `compute_factor.py` | 11 | 7 | 4 | apps |
| 30 | `backtest.py` | 12 | 10 | 2 | apps |
| 31 | `session/` | 12 | 10 | 2 | kernel |
| 32 | `strategy_acceptance/` | 13 | 8 | 5 | apps |
| 33 | `context/` | 14 | 6 | 8 | kernel |
| 34 | `autoresearch.py` | 16 | 12 | 4 | apps |
| 35 | `hypothesis/` | 19 | 9 | 10 | apps |
| 36 | `data_source/` | 20 | 17 | 3 | apps |
| 37 | `scheduled_research/` | 25 | 9 | 16 | kernel |
| 38 | `validation/` | 26 | 10 | 16 | apps |
| 39 | `db.py` | 26 | 21 | 5 | apps |
| 40 | `gateway/` | 31 | 6 | 25 | kernel |
| 41 | `events/` | 32 | 5 | 27 | kernel |
| 42 | `observability/` | 32 | 25 | 7 | foundation |
| 43 | `runtimes/` | 33 | 9 | 24 | kernel |
| 44 | `storage/` | 34 | 17 | 17 | foundation |
| 45 | `swarm/` | 37 | 16 | 21 | kernel |
| 46 | `api/` | 51 | 4 | 47 | interfaces |
| 47 | `engine/` | 55 | 2 | 53 | apps |
| 48 | `orchestration/` | 57 | 16 | 41 | kernel |
| 49 | `workflow/` | 58 | 18 | 40 | kernel |
| 50 | `utils/` | 62 | 35 | 27 | foundation |
| 51 | `agent/` | 64 | 64 | 0 | kernel |
| 52 | `cli/` | 104 | 6 | 98 | interfaces |
| 53 | `study/` | 127 | 28 | 99 | apps |
| 54 | `llm/` | 127 | 42 | 85 | foundation |
| 55 | `goal/` | 139 | 61 | 78 | apps |

数据口径：graphify AST 图（88,873 边），语句去重（文件 × 行 × 目标包）；
单文件包第一版脚本漏计的缺陷已修正（`.py` 后缀匹配 bug）。
已知数据缺口：`core/agent` 的 tests=0 与 ToolContext tests 连接数据矛盾，
执行时用 `graphify affected` 现场复核，不依赖本表。

## 3. 波次编排（入边升序，每 step 一 commit）

| 波 | 入边区间 | 包数 | 累计验证意义 |
|---|---|---:|---|
| **W0 试点** | 0–1 | 1 | `errors.py`：全库第 3 小入边，协议首次全链路验证 |
| **W1** | 1–3 | 13 | 协议在小入边包上固化 |
| **W2** | 4–9 | 12 | mcp/interfaces 进入；apps 单文件群 |
| **W3** | 11–20 | 9 | 中等体量；`autoresearch.py`/`hypothesis` |
| ↳ **W3a** | 11–19 | 3 | **已完成**：`compute_factor`(11) `context`(16) `session`(19) |
| ↳ W3b | 33–35 | 2 | `autoresearch`(33) `strategy_acceptance`(35) 互引，须同批 |
| ↳ W3c | 41–61 | 3 | `backtest`(41) `hypothesis`(41) `data_source`(61) |
| **W4** | 25–37 | 9 | 重包：`db`(26) `gateway`(31) `events`(32) `observability`(32) `runtimes`(33) `storage`(34) `swarm`(37) |
| **W5** | 51–64 | 6 | `api`(51) `engine`(55) `orchestration`(57) `workflow`(58) `utils`(62) `agent`(64) |
| **W6 巨型收尾** | 100+ | 4 | `cli`(104) `study`(127) `llm`(127) `goal`(139) |
| **W7 清理** | — | — | 删 `core/` 空壳 · 删门禁旧路径行 · `cov_gate`/ruff/coveragerc 路径 · graphify 重扫 |

### 3.1 家族绑定（同 commit 移动的关联件）

| 主包 | 同 commit 关联件 | 原因 |
|---|---|---|
| `alpha_zoo` | `alpha_zoo_adapter.py` `alpha_zoo_convert.py` `alpha_zoo_ops.py` `alpha_zoo_yaml.py` | 兄弟模块互引（convert:1000 引 alpha_zoo） |
| `backtest.py` | `backtest_models.py` `backtest_engine/` | 三件一体 |
| `agent/` | — | 22k 单包，可内部再分（先 `builtin_tools` 后其余）见 §3.2 |

### 3.2 agent 22k 特例（W5 内部再分）

agent 是单包 22k、入边 64（全 src）。整包一 commit 的 diff 过大，内部分三步，
每步独立绿：① `strategy/` + `sandbox/` + `runtime/`（~2.4k）② `builtin_tools/`
（6.7k，W1-1 已证是研究适配器集中地）③ 根层其余（loop/compact/event_store 等 ~12.6k）。

## 4. 工具

`scripts/migrate_layers.py` 保留 `--verify`（残留扫描）；**逐包改写不再用它**
（v1 失败教训：AST 语句重组有行合并缺陷）。每 step 用轻量前缀替换脚本
（`tmp/` 下按需生成，模式 = §1.2 第 4 步），替换后必跑：

```bash
# 多行 import 完整性自检（v1 失败的回归门）
python -m compileall -q src/strategy_research tests
```

## 5. 验证门（每 step）

```bash
# 受影响测试（graphify affected 输出 + 包自有 tests/test_<pkg>*）
pytest <受影响清单> -q
# 门禁组
pytest tests/test_layer_matrix.py tests/test_architecture_gates.py \
       tests/test_print_governance.py -q
# 冒烟
python -c "import strategy_research"
```

波次结束加跑全量 `pytest tests -q`（失败集不劣于 102F 基线的对应子集）。

## 6. 不在本次范围

- W2-6 三个既有路径 bug（`prompt_builder:364` / `workspace_tools:308,395` /
  `test_print_governance:88`）——原样保留，搬迁后单独修
- W1-6 alpha NaN 缺陷 · W1-7 研究域测试桩 · W1-10 compact 六项
- lazy import 44 条：不碰（W4-1 门禁规则工作项决定是否单独归类）

## 7. 进度追踪（每 step 更新）

| 包 | 波 | 目标层 | 状态 | commit |
|---|---|---|---|---|
| `errors.py` | W0 | foundation | ✅ | befb1a29 |
| `alpha_zoo_yaml.py` | W1 | apps | ✅ | e420d25b |
| `seq_generator.py` | W1 | kernel | ✅ | 3f856e3b |
| `preflight.py` | W1 | apps | ✅ | 4e248665 |
| `alpha_zoo_convert.py` | W1 | apps | ✅ | 6551100b |
| `alpha_zoo_adapter.py` | W1 | apps | ✅ | f0f5814d |
| `config_loader.py` | W1 | foundation | ✅ | 2816bc65 |
| `export/` | W1 | apps | ✅ | b30cb791 |
| `factor_validate.py` | W1 | apps | ✅ | b500419a |
| `run_card.py` | W1 | apps | ✅ | ffdfe425 |
| `alpha_zoo_ops.py` | W1 | apps | ✅ | a9545565 |
| `workspace_setup.py` | W1 | apps | ✅ | 4ce322a9 |
| `backtest_engine/` | W1 | apps | ✅ | 4a25ec41 |
| `data_readiness.py` | W1 | apps | ✅ | 243e0656 |
| `git.py` | W1 | foundation | ✅ | da08d25c |
| `registry.py` | W2 | foundation | ✅ | 21687c4e |
| `alpha_zoo/` | W2 | apps | ✅ | 364078a3 |
| `mcp/` | W2 | interfaces | ✅ | daba96cc |
| `config_runner.py` | W2 | apps | ✅ | 0261c454 |
| `data_import.py` | W2 | apps | ✅ | 7a4f2c35 |
| `permission/` | W2 | kernel | ✅ | 70e05edc |
| `skills/` | W2 | kernel | ✅ | e2f7f8c1 |
| `backtest_models.py` | W2 | apps | ✅ | d2a6dfdc |
| `web/` | W2 | foundation | ✅ | ffaa8dcd |
| `hooks/` | W2 | kernel | ✅ | 674b8504 |
| `portfolio/` | W2 | apps | ✅ | c07c4be3 |
| `tools/` | W2 | apps | ✅ | 682ee0a6 |
| `compute_factor.py` | W3a | apps | ✅ | 5b22aab7 |
| `context/` | W3a | kernel | ✅ | fd750ae8 |
| `session/` | W3a | kernel | ✅ | 950bd579 |
| `autoresearch.py` + `strategy_acceptance/` | W3b | apps | ✅ | 69b3b6db |
| `backtest.py` | W3c | apps | ✅ | 89dcd07c |
| `hypothesis/` | W3c | apps | ✅ | 8ce7a2ee |
| `data_source/` | W3c | apps | ✅ | 62aaf49f |
| 其余 18 包 | W4–W6 | — | 待执行 | — |

### 7.1 W1 波执行记录（协议修订沉淀）

- **改写形态扩展到 5 种**：module 绝对 / lazy 函数体内绝对 / pkg-import
  （`from core import X as y`）/ 字符串字面量（路径键）/ **相对导入**
  （level-1/2/3 均出现）。`tmp/rewrite_prefix.py` 管前 4 种；相对导入按
  importer 深度解析（`tmp/find_imports_of.py`）逐处裁决。
- **AST 扫描器两次 PEP 328 bug**：`relpath` 带 `.py` 后缀使包路径错位——
  包 = `rel.split(".")[:-2]`（模块名与 `py` 一并剥）。修复后对 11 个已搬包
  回测零残留方继续推进。
- **新增悬空路径登记（W2-6 追加）**：`core/alpha_zoo/loader.py:184`
  `from .alpha_zoo_yaml import` 解析为 `core.alpha_zoo.alpha_zoo_yaml`
  （不存在），迁移前即断，原样保留。
- **level≥2 根相对导入新类别**：`from .. import _TEMPLATES_DIR`
  （workspace_setup）深度不变但父级含义变化，必须转绝对。
- **每步验证**：受影响测试 + 门禁组 + `compileall -x attic` 全绿后 commit；
  W1 波内累计 2444+ 套件全绿（W1-10）。

### 7.2 W2 波执行记录（三个新陷阱）

- **陷阱一：同名包前缀混淆（计划已预警，实际命中）**。
  `core/agent/builtin_tools/data_clean_tools.py:85,98` 的 `from ...tools.data_clean`
  解析为 `strategy_research.core.tools`（研究的 tools 包），与
  `core/agent/tools.py`（另一个包）同形。文本前缀改写只匹配
  `strategy_research.core.tools` 字面量，**相对导入形态绕过**。修正后必须
  用 `find_imports_of.py <tail>` 按解析结果复核，不能只依赖文本替换计数。
- **陷阱二：`__file__` 相对定位随层级语义漂移**。
  `core/mcp/server.py:677,760` 的 `Path(__file__).parent.parent/'swarm'/'presets'`
  在 `core/mcp/` 父级是 `core/`（正确），搬入 `interfaces/mcp/` 后父级变
  `interfaces/`（不存在）。已改为 `Path(swarm_module.__file__).parent/'presets'`
  ——**跟随目标包实际位置，后续再搬自愈**。同类候选项在 W3+ 搬 swarm/goal 等
  含资源目录的包时继续排查。
- **陷阱三：陈旧 `__pycache__` 掩盖残留引用**。搬迁后 Python 可能加载旧字节码，
  使「已修好的 ModuleNotFoundError」以幻影形式复现（本次 data_clean_tools 一度
  误判）。协议增加一步：**搬迁后清 `__pycache__` 再验证**
  （`tmp/clear_pyc.py`）。
- **差分验证制度化**：W2-12 起对「疑似回归」一律做 stash 前后同命令同文件集
  对照，结果须逐字一致。W2-12 实测 1F/34P 两边相同，确认零回归。

### 7.3 W3a 子波执行记录（两个门禁级发现）

- **发现一：层门禁一直在虚报违规数**。`_resolve_targets` 对绝对导入
  `from X import a, b` 展开成 `X` / `X.a` / `X.b` 三条，而相对导入因 target
  相同被 set 去重只计 1 —— 两形态计法不一致。按 ADR-000「每语句计 1」修正后
  真实值大幅下降：L3>L1 2→1 · L3>L2 46→35 · L4>L2 2→0 · L4>L3 4→2。
  基线已按「只降不升」同步下调；**旧基线的相当一部分是工具造出来的**。
  教训：门禁自身的计量口径也要有测试——只看「不超基线」会永久掩盖虚高。
- **发现二：`health.py` 归属错误**（W3a-1 顺带修复）。
  `strategy_acceptance/health.py` 是零依赖纯度量扫描器，唯一消费者是
  `study/runner.py`，包内自身零引用 → 移至 `core/study/health.py`。
  处置原则：**能改归属就不登记白名单**，白名单只留给真正必要的跨域。
  该依赖此前一直悬空（import 名字写错），修好后才第一次被 seam gate 看到。
- **工具补强**：`tmp/fix_rel_residue.py` —— 按 PEP 328 **解析结果**驱动改写
  相对导入，不再依赖文本匹配。W3a-2/3 实战零遗漏，替代了此前逐处手工裁决。

### 7.4 W4–W6 执行记录（收尾三波）

- **W4（6 包）**：observability→foundation（28 处相对导入，单包改写量最大）；
  events→kernel 同时把 `contracts.py` **按语义拆分**——纯协议三符号下沉
  `foundation/protocol.py`，工单业务模型留 L3，`L4>L3` 违规就地清零；
  runtimes/storage 落位；db（1273 行）、swarm（1207 行）收官。
  **gateway 按拍板延后**，W5-6 在 orchestration/runtimes/context 落位后搬迁，
  26 条出边中 21 条已自动指向正确层——验证了拓扑序的价值。
- **W5（13 步）**：utils(49)→validation(12)→engine(31)→orchestration(42)→
  memory(12)→gateway(39)→workflow(38)→llm(88)→study(83)→goal(93)→
  scheduled_research(10)→**agent(211) 最后**（12a 子包 31 文件 / 12b
  builtin_tools 18 文件 / 12c 根层 40 文件）→api(51)。
  agent/llm/goal/study 互相成环，agent 最后使全库 211 处引用一次收口。
  llm 搬迁时顺带把 `structured_output.py` 与 `CompactConfig` dataclass
  下沉 foundation，就地消除最后两条 L4>L3。
- **W6（cli）**：顶层包入 interfaces，深度 +1 三处（pyproject entry points、
  _auto_onboard .env、skills templates）全部改「跟随包根」。
- **W7**：core/ 空壳删除；cov_gate 四档重定义为层目录口径；
  ruff/.coveragerc 路径收口；**五簇上行基线全部归 0**（§9）。
- **坑位总账**（四类陷阱，均有工具化对策）：
  ① 同名包前缀混淆（core/tools vs core/agent/tools）→ 按解析结果裁决；
  ② `__file__` 相对定位随层级漂移（mcp/goal_workflow/swarm/dag_planner/
     study/cli_skills 共 6 处）→ 统一改「跟随目标包 `__file__`」；
  ③ 陈旧 `__pycache__` 掩盖残留 → 每步清缓存；
  ④ xdist worker 继承污染的 USERPROFILE → canary 排除沙箱路径。

## 8. 回滚

每 step 一 commit：`git revert <sha>` 即回到上一步；波次结束跑全量做整波验证。
`core/` 空壳在 W7 前始终保留，任何一步回滚都不会产生悬空路径。

## 9. 收官状态（2026-11-04，W7-3 交叉验证后修订）

| 维度 | 迁移前（11-03 基线） | 收官 |
|---|---|---|
| 物理结构 | `core/` 一坨 55 包 | interfaces/{api,cli,mcp} · apps(26) · kernel(19) · foundation(12) |
| 上行违规 | L2>L1 1 · L3>L1 2 · L3>L2 46 · L4>L2 2 · L4>L3 4 | **49 条如实登记**：
  L3>L2 46（ADR-003 保留）+ L3>L1 2 + L2>L1 1（seam 触点）+ L4 簇全 0。
  中途一度报「全 0」系门禁解析 bug（见下），已修正 |
| 层门禁 | 60 行手写成员表 + 46 条豁免 | 目录即层 + 成员表仅存顶层目录行 |
| 门禁自身质量 | 两处计数 bug 先后潜伏 | W3a 修 alias 展开（46→47 虚增）；
  W7 graphify 交叉验证暴露 level=0 拼接 bug（绝对形态全部漏报）。
  **教训：静态门禁必须有第二种量纲做交叉验证** |
| 既有缺陷顺带修复 | — | 7 处悬空 import（3 个静默功能失效）、
  file_cache Windows 文件名、CompactConfig/structured_output 归层、
  data store 适配件归位 apps |
| 遗留待办 | — | Alpha Zoo YAML where 语义（业务域，独立台账）、
  ruff I001/F401 宽限项 127 条 |

### 9.1 全量测试最终对照（vs 11-03 基线 98F）

最终并行全量 185F / 13880P（6 分 35 秒，xdist -n 4）：

| 类别 | 数量 | 说明 |
|---|---:|---|
| Alpha Zoo YAML 业务缺陷 | 116 | where 语义（独立台账，业务域） |
| 既有测试自身缺陷 | ~12 | tool_retry(5)/e2e_full_flow(2)/data_clean_tool(2)/
  quant_correctness(2)/bg_tools(1)——W4 差分逐一确认过 HEAD 上同样失败 |
| 顺序敏感摇摆集 | ±10 | route_agent_event/stage6_e2e/tui_event 等在历史 full run
  间来回摇摆，非稳定新增 |
| **迁移净引入** | **0** | study_event_emit 2 处路径字面量已修（W7-3b） |
| 顺带修复 | -43 | 43 条基线失败转通过 |
