# scripts/ — 工具目录说明

物理分层迁移（2026-11-03 → 11-04，docs/design/physical-layer-migration-plan-20261103.md）
沉淀的工具，按「运行时机 × 使用者」分三个子目录。**新增工具必须落对目录、
按命名规范命名**——目录名即使用契约。

## 命名规范

| 目录 | 定位 | 命名规范 | 退出码约定 | 运行时机 |
|---|---|---|---|---|
| `arch/` | 架构守卫 | `check_<对象>.py` | 0 通过 · 1 发现问题 | 每次 commit（已接入 pytest 门禁） |
| `migration/` | 包搬迁 | 动词开头 `rewrite_*` `fix_*` `find_*` | 0 成功 · 2 用法错误 | 搬迁 step 协议内（见下） |
| `dev/` | 开发辅助 | 描述式（无固定前缀） | 常规 | 按需 |

规则：
1. `arch/` 的脚本必须**无人值守可跑**：不交互、不依赖网络、失败即非零退出。
2. `migration/` 的脚本必须**可 --check / 可 dry-run** 或输出逐文件计数——
   搬迁协议要求每一步可审。
3. 一次性修复脚本（当时的 fix_*.py 补丁）**不进本目录**——它们的生命周期
   等于那次搬迁，留在 git 历史里即可。

## arch/ — 架构守卫（已接入 tests/test_architecture_gates.py）

| 脚本 | 检查什么 | 为什么是解析级/第二种量纲 |
|---|---|---|
| `check_dangling_imports.py` | import 解析目标在 agentnodes/ 下不存在 | 文本前缀替换对相对导入天然盲区；曾抓出 7 处静默功能失效 |
| `check_path_literals.py` | 非 import 语句里的旧路径拼接（`"core"` 段 / 非法层段） | import 改写工具的双盲区；曾抓出 11 处 presets/skills 目录定位漂移 |
| `graph_layer_audit.py` | graphify 符号图的层向矩阵 + core 残留 + god-nodes | 与语句级门禁互为**第二种量纲**；曾暴露门禁 level=0 拼接 bug |
| `layer_matrix.py` | 层归属表 + 判定原语（**唯一事实源**） | `test_layer_matrix` 与 `--layers` 共用，防止两份表漂移 |
| `sync_architecture_diagram.py` | 架构图数字与 AST 实测是否一致 | 图上的层体量/边数是实测来的；**曾出现图写 46 违规、实际 0 且过时 6 版** |

### 悬空导入门禁的两种 import 形态（2026-11-05 补）

`check_dangling_imports.py` 必须同时看两种 AST 节点，缺一不可：

| 形态 | AST 节点 | 典型写法 |
|---|---|---|
| from-import | `ast.ImportFrom` | `from agentnodes.apps.db import get_conn` |
| plain import | `ast.Import` | `import agentnodes.interfaces.api.routers.workflow as wf` |

**为什么不能只看 `ImportFrom`**：批次 2 删掉 `interfaces/api/routers/workflow.py` 后，
两个测试文件用 `import ...routers.workflow as wf` 继续引用它。旧版 `scan()` 只遍历
`ast.ImportFrom`，门禁**显示 0 悬空**，而那 24 个用例在 fixture setup 期才炸——
收集期不报错，连 `pytest --collect-only` 都看不出问题。
现已补 `ast.Import` 分支，回归测试见
`tests/test_scripts_arch.py::test_dangling_detects_plain_import_alias`。

### Windows 控制台编码（GBK）

`arch/` 脚本必须能在 **Windows 默认 GBK 控制台**下跑完并给出正确退出码。
两个已踩的坑：

- `sync_architecture_diagram.py` 末行 `print("... ✅")` → `UnicodeEncodeError`
  → **在「全 [OK]」之后**崩、退出码 1，看起来像检查失败。现已
  `sys.stdout.reconfigure(errors="replace")` 兜底，且统一用 `[OK]` 标记。
- 新增脚本时不要在 `print()` 里放非 GBK 字符（✅ ⚠️ ❌ 等）。



运行：
```bash
python scripts/arch/check_dangling_imports.py
python scripts/arch/check_dangling_imports.py --layers   # 附层方向矩阵
python scripts/arch/check_path_literals.py
python scripts/arch/sync_architecture_diagram.py         # 架构图是否过时
python scripts/arch/sync_architecture_diagram.py --write # 把实测数字回写进图
python -m graphify extract . --code-only --no-cluster --out tmp/graphify2   # 先抽图
python scripts/arch/graph_layer_audit.py tmp/graphify2/graphify-out/graph.json
```

### `--write`：架构图自动回写（2026-11-05 落地）

「每次大改后自动更新架构图」这条规则此前只有**检查**没有**回写**——
数字得人手工改（错过一次就红一次）。现在 `--write` 把 AST 实测值直接写回：

- **只改数字，不动版式与文案结构**。每条替换都用带上下文的正则锚定
  （例：「112 文件」在图上出现两处，徽标与表行分别锚定），避免误伤同名数字。
- **写完立即复核**：`check()` 对**同一份文本**再跑一遍，不过就**还原原图**并报错。
  宁可不动，也不能写出一张半路坏掉的图。
- **零命中即报**：任何一条替换没命中，说明图的结构变了（版式改过），
  报告会点名让人核对，而不是静默跳过。
- **幂等**：写两次不产生差异（`tests/test_scripts_arch.py::
  test_architecture_diagram_write_is_idempotent` 断言）。

流程从「量实测 → 手改图 → 验证」变成：

```bash
python scripts/arch/sync_architecture_diagram.py --write   # 回写
python scripts/arch/sync_architecture_diagram.py            # 复核（应全 [OK]）
```

### ⚠ 架构图更新规则（用户要求，2026-11-04）

**每次大的修改后必须更新 `docs/architecture-diagram.html`。** "大的修改"指：

- 增删 apps 包（做减法 / 新增业务域）
- 搬模块跨层（比如把 Ticket 从 apps 挪到 kernel）
- 改 import 结构（端口化、注册反转）
- 任何让层体量变化 > 5% 或让跨层边数变化的改动

流程固定三步：

1. **量实测**（不得手写猜测）——`sync_architecture_diagram.py` 会打印每层的
   文件数与行数、6 项跨层边数、6 项上行违规
2. **改图**——`docs/architecture-diagram.html` 里的 L 徽标、功能域模块清单、
   ⑦ 矩阵、页脚总计
3. **验证**——重跑 `sync_architecture_diagram.py`，全 `[OK]` 才算完成

这条已接入 pytest（`tests/test_scripts_arch.py::test_architecture_diagram_matches_code`），
**图过时会直接让 CI 红**，不依赖人记得。

### ⚠ 工具选型规则：graphify 与门禁不可互换

2026-11-04 结构评审（docs/design/structure-review-graphify-20261104.md §0）实测：

| 度量 | 模块顶层 import | 函数体内 import | 结论 |
|---|---:|---:|---|
| graphify AST 抽取 | 1,166 / 1,171 = **99.6%** | 627 / 865 = **72.5%** | 对 lazy 有 ~27% 系统性盲区 |
| `test_layer_matrix` 门禁 | 全量（AST + PEP 328 解析） | 全量 | 语句级权威口径 |

**规则：**
1. **上行违规 / 循环依赖的判据只认 `test_layer_matrix`**，不用 graphify 计数替代基线。
2. graphify 的正确用途：**拓扑、热点（god nodes）、扇入扇出、体量统计**。
3. 两者只能比**方向与簇分布**，绝不比绝对值。曾出现"门禁 46 vs graphify 46"的
   **数值巧合**——逐条集合比对后交集仅 37/46 = 80.4%，graphify 漏了 9 条
   lazy import 上行（如 `apps/study/scheduler.py:779`，是门禁 L2→L1=1 的唯一来源）。
4. 判环必须**分两张图**：仅 eager（顶层 import，环 = 真 ImportError 风险）
   vs 含 lazy（环 = 概念耦合）。包级 SCC 会把 `from pkg import Name`（穿透 `__init__`）
   与 `import pkg.sub`（直达）混同，**必然产生假环**——实测 14 包巨环在 10 种导入顺序下
   全部可导入。真判据是模块级 eager SCC：当前 4 个，全部在单包内，跨包 = 0。

## migration/ — 包搬迁工具

下次「core/ → 层目录」级别的搬迁直接复用。每步迁移协议
（docs/design/physical-layer-migration-plan-20261103.md §1.2）中它们的位置：

```
1. find_importers.py <tail>                     # 定位受影响文件（爆破面普查）
2. git mv core/<pkg> <layer>/<pkg>
3. 处理被搬包内部跨层引用
4. rewrite_import_prefix.py <old> <new>         # 文本级三形态改写
   fix_relative_imports.py <tail> <new>         # 解析级相对导入改写
5. 门禁成员表登记
6. python -m compileall -q src tests -x attic
   python scripts/arch/check_dangling_imports.py
   python scripts/dev/clear_pyc.py && pytest <受影响> -q   # 清缓存再验证
7. commit（一包一 commit）
```

## dev/ — 开发辅助

| 脚本 | 用途 |
|---|---|
| `clear_pyc.py` | 清 __pycache__（搬迁后陈旧字节码会掩盖真实错误——陷阱③） |
| `diff_pytest_failures.py` | 两份全量日志失败集差分（差分验证制度固化版） |

## 历史

全部工具来自 2026-11-03/04 物理分层迁移的实战沉淀，固化时做了三件事：
路径参数化（去 tmp 硬编码）、docstring 补「为什么存在」与历史教训、
退出码对齐目录契约。迁移期间的 20+ 个一次性 fix_*.py 补丁未固化。
