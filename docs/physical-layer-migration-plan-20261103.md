# 物理分层迁移计划 v2 — 逐模块搬迁（tests-green 每 step）

日期：2026-11-03　分支：`refactor/v6-gateway`　状态：**已批准 · 逐模块执行**
v1（大爆炸批次）已于同日废弃——首次试运行批 1 即失败：AST 改写工具把多行
import 压成单行（20 处语法损坏），暴露出「一次性改写 1,200 处」的工具风险。
已回滚至 `07e1e6d5`，工作树干净。教训与本 v2 方案见 §1。

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
| `errors.py` | W0 | foundation | 待执行 | — |
| 其余 54 包 | W1–W6 | — | 待执行 | — |

## 8. 回滚

每 step 一 commit：`git revert <sha>` 即回到上一步；波次结束跑全量做整波验证。
`core/` 空壳在 W7 前始终保留，任何一步回滚都不会产生悬空路径。
