# scripts/ — 工具目录说明

物理分层迁移（2026-11-03 → 11-04，docs/physical-layer-migration-plan-20261103.md）
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
| `check_dangling_imports.py` | import 解析目标在 strategy_research/ 下不存在 | 文本前缀替换对相对导入天然盲区；曾抓出 7 处静默功能失效 |
| `check_path_literals.py` | 非 import 语句里的旧路径拼接（`"core"` 段 / 非法层段） | import 改写工具的双盲区；曾抓出 11 处 presets/skills 目录定位漂移 |
| `graph_layer_audit.py` | graphify 符号图的层向矩阵 + core 残留 + god-nodes | 与语句级门禁互为**第二种量纲**；曾暴露门禁 level=0 拼接 bug |

运行：
```bash
python scripts/arch/check_dangling_imports.py
python scripts/arch/check_path_literals.py
python -m graphify extract . --code-only --no-cluster --out tmp/graphify2   # 先抽图
python scripts/arch/graph_layer_audit.py tmp/graphify2/graphify-out/graph.json
```

## migration/ — 包搬迁工具

下次「core/ → 层目录」级别的搬迁直接复用。每步迁移协议
（docs/physical-layer-migration-plan-20261103.md §1.2）中它们的位置：

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
