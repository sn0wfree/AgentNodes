# 物理分层迁移计划 — core/ → foundation · kernel · apps · interfaces

日期：2026-11-03　分支：`refactor/v6-gateway`　状态：**已批准，分 5 批执行**
前置：`docs/layered-skeleton-design-20261103.md`（层定义与依赖矩阵）·
`docs/adr/ADR-000/002/003`（基线口径与处置决策）

---

## 1. 动机

架构图与 `test_layer_matrix` 门禁已用 L1–L4 表达依赖方向，但**物理目录仍是 `core/` 一坨**。
层归属只存在于一个 60 条的手写字典里，带来三个具体问题：

| 问题 | 现状证据 |
|---|---|
| 新人加文件要查表才知该放哪层 | `tests/test_layer_matrix.py:34-99` 的 `LAYER_MEMBERSHIP` 手工维护 |
| 覆盖率分档与层不对齐 | `scripts/cov_gate.py:30-59` 用 `core/gateway/` 这类路径前缀手写匹配 |
| 「层」是叙事而非结构 | `ls src/strategy_research` 只看到 `core/`，看不到四层 |

迁移后：**顶层目录即层**，门禁成员表可退化为派生规则，`cov_gate` 的 band 前缀天然对齐。

## 2. 目标结构

```
src/strategy_research/
├── __init__.py                  # _TEMPLATES_DIR 不变（templates 留顶层）
├── foundation/                  # L4 · 零业务知识
│   ├── __init__.py
│   ├── llm/ storage/ observability/ utils/ web/
│   └── registry.py config_loader.py errors.py git.py
├── kernel/                      # L3 · 平台机制
│   ├── __init__.py
│   ├── gateway/ orchestration/ runtimes/ workflow/ context/
│   ├── agent/ swarm/ events/ session/ memory/ skills/
│   ├── hooks/ permission/ scheduled_research/
│   └── seq_generator.py
├── apps/                        # L2 · 研究域 IP
│   ├── __init__.py
│   ├── study/ goal/ hypothesis/ alpha_zoo*/ backtest*/ validation/ portfolio/
│   ├── data_source/ data_import.py data_readiness.py compute_factor.py
│   ├── engine/ tools/ db.py config_runner.py factor_validate.py
│   ├── strategy_acceptance/ autoresearch.py export/ run_card.py
│   └── workspace_setup.py preflight.py
├── interfaces/                  # L1 · 哑适配器
│   ├── __init__.py
│   └── api/ cli/ mcp/
├── templates/                   # 保持原位（package-data，避免 5 处配置连锁）
└── scripts/                     # dev 脚本不动（门禁已 prune "scripts"）
```

`core/` 在批 4 后**整体消失**（含 `core/__init__.py`）。

### 2.1 为什么 templates 留在顶层

`templates/` 是 package-data 不是 Python 包，动它就牵动 5 处配置：

| 位置 | 内容 |
|---|---|
| `pyproject.toml:81` | `strategy_research = ["templates/**/*", "templates/.prompts/**/*"]` |
| `MANIFEST.in:1-3` | `recursive-include src/strategy_research/templates ...` ×3 |
| `src/strategy_research/__init__.py:10` | `_TEMPLATES_DIR = Path(__file__).parent / "templates"` |
| 5 处 loader | `prompt_builder` / `bootstrap` / `builtin` / `workspace_tools` / `mcp/server` 的 `parents[N]/"templates"` |
| `templates/` 无 `__init__.py` | 移入 `apps/` 后需补，或改 `py-modules` 显式声明 |

逻辑上它仍属 L2（门禁成员表照旧 `templates → L2`），只是不参与物理分层。

## 3. 实测爆破面

数据源：graphify AST 全量图（45,877 节点 / 88,873 边）+ 逐文件核查。

| 项 | 数量 | 说明 |
|---|---:|---|
| 生产代码跨层 import 语句 | **396** | 122 文件；前 15 个占 47% |
| 测试 → 生产 import 语句 | **811** | 476 测试文件，约 2:1 —— **真正的大头** |
| 点号字符串（monkeypatch / uvicorn / importlib / `__import__`） | ~200 | 静默失败源 |
| 深度变化文件 | **117** | `api/`(37) `cli/`(70) `templates/`(3) 各 +1 层 |
| 深度保持文件 | **1,281** | 全部 `core/*`（`core/X/...` → `layer/X/...`） |

**关键事实**：`core/X/...` → `<layer>/X/...` 深度不变 ⇒ 包内相对导入与
`Path(__file__).parents[N]` 全部不受影响；**只有跨层引用需要改写**。

### 3.1 头部集中度（改写工作量分布）

| 文件 | 跨层语句 | 层 | 流向 |
|---|---:|---|---|
| `core/mcp/server.py` | 27 | L1 | L2:12 · L3:14 · L4:1 |
| `cli/__init__.py` | 23 | L1 | L2:20 · L3:2 · L4:1 |
| `api/routers/study.py` | 16 | L1 | L2:13 · L3:1 · L4:2 |
| `api/routers/workflow.py` | 12 | L1 | L2:3 · L3:9 |
| `cli/commands/slash_goal.py` | 12 | L1 | L2:12 |
| `core/agent/loop.py` | 10 | L3 | L2:2 · L4:8 |

52 个文件只有 1 条跨层语句——尾部很长但单点小。

## 4. 执行方式：5 批，自顶向下

自顶向下的理由：`interfaces` 零入边（实测 L2→L1=0、L4→L1=0），第一批改动面最小、
方法论可验证；越往下入边越多。每批 = `git mv` + 全库改写指向该层的 import + 门禁同步。

| 批 | 层 | 移动 | 待改写（按目标层计） | 特别处理 |
|---|---|---|---:|---|
| **1** | interfaces | `api/` `cli/` → `interfaces/`；`core/mcp/` → `interfaces/mcp/` | 测试→L1 **149** | 深度+1 的路径常量、`pyproject` console target、uvicorn target 字符串 |
| **2** | apps | 25 个包/文件 → `apps/`（含 `alpha_zoo` 876 文件 / 408 yaml） | L1→L2 109 · L3→L2 38 · L4→L2 3 · 测试→L2 **307** | `core.tools` vs `core.agent.tools` 必须完整前缀匹配 |
| **3** | kernel | 15 包 + `seq_generator.py` → `kernel/` | L1→L3 86 · L2→L3 26 · L4→L3 3 · 测试→L3 **215** | 3 处 `"core"` 字面量路径 |
| **4** | foundation | llm/storage/observability/utils/web + registry/config_loader/errors/git | L1→L4 44 · L2→L4 46 · L3→L4 39 · 测试→L4 **140** | 删 `core/`；`.coveragerc` omit |
| **5** | — | 门禁与配置收口 | — | 见 §4.5 |

### 4.1 批 1 细节（interfaces）

移动：
- `api/` → `interfaces/api/`（37 文件，深度 +1）
- `cli/` → `interfaces/cli/`（70 文件，深度 +1）
- `core/mcp/` → `interfaces/mcp/`（2 文件，深度不变）

**深度敏感路径修正**（因 +1 层必须调整，否则坏）：
- `api/routers/agents.py:21-24` `parents[3]` → `parents[4]`
- `api/app.py:233` 4 个 `.parent` → 5 个
- `cli/commands/skills.py:18,45,71` 3 个 `.parent` → 4 个
- `cli/_auto_onboard.py:41` `parents[2]` → `parents[3]`
- `cli/commands/swarm.py:19,38,76` 3→4（`"core"` 字面量批 3 再改）

配置：
- `pyproject.toml:73-75` 两个 console target → `strategy_research.interfaces.cli.__main__:main`
- uvicorn target 字符串：`cli/commands/server.py:100,146` · `tests/conftest_e2e.py:122` ·
  `tests/test_chat_e2e_playwright.py:69` · `tests/test_chat_real_llm.py:80,366` · `tests/test_sse_e2e.py:65`

### 4.2 批 2 细节（apps）

移动清单（25 项）：`study` `goal` `hypothesis` `alpha_zoo` `alpha_zoo_adapter.py`
`alpha_zoo_convert.py` `alpha_zoo_ops.py` `alpha_zoo_yaml.py` `backtest.py`
`backtest_engine` `backtest_models.py` `validation` `portfolio` `data_source`
`data_import.py` `data_readiness.py` `compute_factor.py` `factor_validate.py`
`engine` `tools` `db.py` `config_runner.py` `strategy_acceptance` `autoresearch.py`
`export` `run_card.py` `workspace_setup.py` `preflight.py`

**重点风险**：`core/tools`（数据工具）与 `core/agent/tools`（BaseTool 框架）是
两个不同包，改写必须完整前缀精确匹配，否则会把框架引用误改成研究工具路径。

### 4.3 批 3 细节（kernel）

移动：`gateway` `orchestration` `runtimes` `workflow` `context` `agent` `swarm`
`events` `session` `memory` `skills` `hooks` `permission` `scheduled_research`
`seq_generator.py`

**3 处 `"core"` 字面量路径**（不是 import，纯字符串拼接）：
- `interfaces/api/routers/study.py:1642` — `parents[3] / "core" / "swarm" / "presets"`
- `interfaces/cli/commands/swarm.py:19,38,76` — 同上
- `apps/study/dag_planner.py:74-76` — 同上

### 4.4 批 4 细节（foundation）

移动：`llm` `storage` `observability` `utils` `web` `registry.py`
`config_loader.py` `errors.py` `git.py`；完成后删除 `core/`。

配置收尾：
- `.coveragerc:11-13` 三条 `*/core/data_source/*` omit → `apps/data_source/*`

### 4.5 批 5 细节（门禁与配置收口）

| 文件 | 改动 |
|---|---|
| `tests/test_layer_matrix.py` | `LAYER_MEMBERSHIP` 60 条字典 → **派生规则**（顶层目录 = 层）+ 例外表（`templates`→L2）；`SKIP_MODULES` 调整；基线数值不变 |
| `tests/test_architecture_gates.py` | `SRC/"core"/pkg` → 层路径；**修掉 `:137` `split(".")[2]` 位置索引**（改按层前缀判定） |
| `tests/test_print_governance.py` | `EXEMPT_FILES` 键改 `kernel/engine/base.py` 等 |
| `scripts/cov_gate.py` | band 前缀全量改层名（**不改会让 `"core/"` catch-all 吞掉一切**） |
| `pyproject.toml` | `[tool.ruff.lint.per-file-ignores]` 4 条路径 glob；`package-data` **不动** |

## 5. 迁移工具

新增 `scripts/migrate_layers.py`（一次性工具，保留可审计）。

```
python scripts/migrate_layers.py --batch interfaces --dry-run   # 预演
python scripts/migrate_layers.py --batch interfaces             # 执行
python scripts/migrate_layers.py --verify                       # 残留检查
```

三阶段：`git mv` → AST import 改写（绝对导入 + 相对导入跨层判定 + 字符串字面量）
→ 报告与残留检查。

**安全设计**：
- 前缀表按长度降序匹配，`core.agent.tools` 不会被 `core.tools` 规则命中
- 相对导入（`level > 0`）**不改写**——同深度移动后仍然有效；跨层引用本就写成绝对导入
- 裸前缀 key（`api` / `core.agent`）只在整段匹配时生效，不误伤 `api_client` 这类标识符
- `--verify` 扫 `src/` `tests/` `scripts/` `evals/` `examples/` + 配置文件，任何
  `strategy_research.core.` 残留即退出码 1

## 6. 不在本次范围（用户决定：单独开工作项）

3 个**本来就已经断**的路径 bug，机械调整层数会把它变成另一种 bug，因此原样保留、
只记录为 **W2-6**：

1. `core/agent/prompt_builder.py:364` — `parent.parent` 少一层，指向不存在的
   `core/templates/.prompts`（auto-discovery 静默返回 `{}`）
2. `core/agent/builtin_tools/workspace_tools.py:308,395` — 从 4 深文件
   `parent.parent.parent` 多算一层 → `core/templates/.skills`（被 `.is_dir()` 静默吞掉）
3. `tests/test_print_governance.py:88` — `_py_files("study")` 解析到
   `src/strategy_research/study`，该目录不存在（门禁相当于空转）

## 7. 验证（每批完成后）

```bash
# 1. 零残留
python scripts/migrate_layers.py --verify

# 2. 冒烟
python -c "import strategy_research"
python -c "import strategy_research.interfaces.api.app"

# 3. 门禁（基线不变：L2>L1 1 · L3>L1 2 · L3>L2 46 · L4>L2 2 · L4>L3 4）
pytest tests/test_layer_matrix.py tests/test_architecture_gates.py \
       tests/test_print_governance.py -q

# 4. 全量（失败集不劣于迁移前）
pytest tests -q

# 5. 覆盖率四段仍 ≥90
python scripts/cov_gate.py
```

全部 5 批完成后：
```bash
# 6. graphify 重扫，核对跨层矩阵与门禁一致
python -m graphify extract . --code-only --no-cluster --out tmp/graphify2
```
7. 更新 `docs/layered-skeleton-design-20261103.md` §2（成员表 → 目录结构）与架构图 v8.5。

## 8. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 改写脚本误伤（`core/tools` vs `core/agent/tools`） | 完整前缀精确匹配 + `--dry-run` 逐条 review + `--verify` |
| 探针漏改（`__import__` / uvicorn target / entry points） | 显式点位清单（§4.1），批 1 一次性处理 |
| 覆盖率分档静默失效（`cov_gate` 的 `"core/"` catch-all） | 每批同步改 band 前缀；批 5 实跑核对 |
| 中间态红灯 | 每批独立 commit；`git reset --hard <batch>^` 回到上一批绿点 |
| 门禁在批 1-4 期间误判 | `LAYER_MEMBERSHIP` 每批增量添加新层前缀（旧 key 保留到批 5 统一清理） |

## 9. 进度追踪

| 批 | 状态 | commit |
|---|---|---|
| 工具 | 进行中 | — |
| 1 interfaces | 待执行 | — |
| 2 apps | 待执行 | — |
| 3 kernel | 待执行 | — |
| 4 foundation | 待执行 | — |
| 5 门禁收口 | 待执行 | — |
