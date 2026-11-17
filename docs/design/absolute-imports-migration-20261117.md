# 全项目相对导入 → 绝对导入迁移设计

> **日期**: 2026-11-17 · **性质**: 实施设计 · **分支**: `refactor/session-lifecycle-kernel`
> **用户决策（2026-11-17 确认）**: ① tests/_fixtures 8 处豁免；② 新增回归防护门禁
> `check_no_relative_imports.py` 接 batch_gate；③ 批次划分（试金石 + 5 批 = 6 个实现 commit）。

---

## 1 · 现状盘点（2026-11-17 AST 全扫，import 语句级）

| 范围 | 数量 | 文件数 | 处置 |
|---|---|---|---|
| **src/agentnodes** | **498 处** | 172 文件 | 本任务主战场 |
| tests（全在 `tests/_fixtures/`） | 8 处 | 3 文件 | **豁免**：tests 不是包（无 `__init__.py`），绝对化需引入 `tests/__init__.py` → 改变 pytest 收集模式，风险 > 收益 |
| scripts / templates | 0 | — | 无涉 |

形态分布（决定转换器形状）：

| 维度 | 分布 | 转换要点 |
|---|---|---|
| 点数 | 单点 `.` 476 · `..` 25 · `...` 5 | 换算公式见 §2（已抽查 15 条 `..`/`...` 验证正确） |
| 位置 | 模块顶层 342 · 函数内 157 · TYPE_CHECKING 7 | 函数内 = 惰性/避环导入，**保持原位置只换路径** |
| 文件类型 | 普通模块 345 · `__init__.py` 161 | re-export 链顺序不动 |
| 形态 | 多行括号 72 · `from . import X` 空模块 14 · 行尾注释 18 · 动态导入 0 | 只替换语句首行 `from .xxx` 前缀；括号体/缩进/注释不动 |

## 2 · 转换规则

对文件所在包 `P`（相对 `src/agentnodes` 的目录，`.` 连接；`__init__.py` 同其目录），
目标 = `"agentnodes." + P` 去掉尾部 `(level-1)` 段，再拼接 `module`：

```
kernel/agent/strategy/steps/compaction.py（P = agentnodes.kernel.agent.strategy.steps）
  ..._core_types  (level=3) → agentnodes.kernel.agent._core_types
  ..loop_context  (level=2) → agentnodes.kernel.agent.strategy.loop_context
  .part_accumulate(level=1) → agentnodes.kernel.agent.strategy.steps.part_accumulate
```

**改写方式**：只在语句**首行**替换点串前缀 —— `from ` 之后的 `level` 个点替换为
绝对 base（module 非空时追加 `.`）；多行括号体、行尾注释、缩进一律不动。
**行为不变性**：相对/绝对解析到同一模块对象，绑定语义相同；函数内导入的位置不动
（惰性/避环语义保留）；`getLogger` 等运行时机制零涉及。

## 3 · 批次与验证

| 批 | 范围 | 约处数 | commit |
|---|---|---|---|
| 0 试金石 | foundation（除 llm）+ kernel/context + hooks + skills + events | ~35 | 1 |
| 1 | kernel/agent（loop.py 25 处最大单文件；`..`15/`...`5 集中区） | 165 | 2 |
| 2 | foundation/llm + interfaces/cli + cli/tui + mcp | ~70 | 3 |
| 3 | interfaces/api + interfaces 根 | ~55 | 4 |
| 4 | apps 全部 + kernel/workflow + runtimes + gateway + orchestration | ~130 | 5 |
| 5 收尾 | kernel/session + evolution + permission + swarm + 其余 + **新门禁** + 实测回填 | ~45 | 6 |

**每批验证（R-011 三件事 + R-007 三件套）**：

1. 逐文件 `git diff` 审计：**变更行必须全部是 `from` 开头行**（审计脚本强制，非人眼）；
2. `pytest --collect-only` 收集数与锚点一致（不增删测试）；
3. 全量 pytest `-n 8 --dist loadgroup` 0 failed；
4. `batch_gate` ALL GREEN（含 `check_dangling_imports --layers`：两种形态同判，上行违规仍 0）；
5. 架构图预期**零漂移**（行内替换不增减行 → 行数/文件数/符号数不变），仍跑 sync 确认。

## 4 · 回归防护门禁（批 5 落地）

`scripts/arch/check_no_relative_imports.py`：`src/agentnodes` 内 `level>0` 的
ImportFrom 必须 = 0（AST 扫描，豁免：无）；R-019 注入自检；接 `batch_gate`。
没有它，相对导入会随新代码回流 —— 这是本任务的持久收益所在。

## 5 · 明确不做

- tests/_fixtures 的 8 处（豁免，见 §1）；
- import 排序 / PEP8 重排 / 顺手重构；
- 不改任何 import 的顺序、位置（含函数内惰性导入的位置）、括号结构。

## 6 · 回滚

每批独立 commit：`git revert <batch>` 即回到该批前状态；批间无交叉依赖
（转换器按文件独立换算，批序不影响正确性）。

## 7 · 验收标准（全批完成后）

- AST 扫描 `src/agentnodes` 内 `level>0` = **0**；
- 全量 pytest 0 failed；`batch_gate` ALL GREEN；`check_dangling_imports --layers` 上行违规 0；
- collect 锚点不变；架构图零漂移（或 --write 后一致）；
- 本文档 §8 回填各批实测数字。

## 8 · 执行结果（2026-11-17 实测）

| 批 | 范围 | 实测处数 | commit |
|---|---|---|---|
| 0 试金石 | foundation(除 llm) + kernel/context·hooks·skills·events | 30 / 16 文件 | `8cf22b07` |
| 1 | kernel/agent（含 `..` 15 / `...` 5） | 165 / 63 文件 | `c58e72c8` |
| 2 | foundation/llm + interfaces/cli·tui·mcp | 70 / 23 文件 | `22f4732c` |
| 3 | interfaces/api + interfaces 根 | 50 / 13 文件 | `bf25ae20` |
| 4 | apps + kernel/workflow·runtimes·gateway·orchestration | 110 / 33 文件 | `f5bc4e8f` |
| 5 收尾 | kernel/session·evolution·permission·swarm·memory + 门禁 | 69 / 21 文件（+memory 2） | 本批 |
| — | **合计** | **~498 处 / 172 文件** | **src 残留 = 0** |

**转换器改进（批 4 证据驱动）**：原「目标不存在 → 放弃整文件」策略过粗 ——
`apps/goal/{store,workflow}.py` 含**故意的 dangling 导入**（探测已归档模块，
带 `# dangling-import: ok`），整文件放弃会漏掉同文件 6/19 处正常相对导入。
改为**逐语句跳过**后两文件补齐 25 处，残留归零。

**验证**：每批全量 pytest；末批 7594 passed / 106 skipped；2 failed 均为并行会话
（builtin_tools 摊平）未提交改动引起的文档数字漂移（L2 42→41、总计 431→430），
与本迁移零关联（本迁移行数守恒，逐文件 numstat 验证）。

**新门禁**：`scripts/arch/check_no_relative_imports.py`（batch_gate 第 11 步，
R-019 注入自检 + 6 条单测）—— 保证相对导入不回流。
