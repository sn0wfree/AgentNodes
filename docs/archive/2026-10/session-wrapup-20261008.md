# Session Wrap-up — 2026-10-08（全天）

> 从「CI 跑通后看 flaky」到技术债清零的完整弧线。**~50 commits**，三轮结构快照见证全过程。
> 姊妹篇：`structure-debt-{survey,resurvey,third-pass}-20261008.md`（数据）· `INDEX.md`（导航）。

## 1. 全天主线（6 段）

| 段 | 主题 | 代表 commit |
|---|---|---|
| 1 | **CI 基建**：LPT 时间感知分片、`--reruns`、timings collector | `9c4760e` `24a11dc` |
| 2 | **真 bug 修复**：12 个 F821 NameError 家族 + claims 饿死 + Projector.close + store tiebreak | `3bdde1d` `261a88f` `b653eea` |
| 3 | **重构链 Step 0-3**：RoundOutcome 契约 → 查表分派 → 分支抽取（phase_engine 24→8、`_run_loop` 32→12） | `68c891f` `63eaddb` `db41db6` |
| 4 | **P0 防线**：pre-commit（E9/F821）+ eval 跟幅门禁（exit 1） | `2b652f2` `5072cb3` |
| 5 | **P1 落地**：extension-points 分工文档、llm/e2e/slow 三层 lane（PR gate 61s）、study 引擎链收尾（dispatch 22→6、build_langgraph 21→4） | `6221542` `bdbf922` `2b98a4c` |
| 6 | **清扫终局**：P1.5（F401×56/F811/DELETE 12 条 -1703 行）+ P2 五件 + c-4（4 谎报标签撤销、dag/env 真删除） | `dc238b5` `d8fe6b8` … c-4 |

## 2. 终态数字（三轮对比见 third-pass）

| 维量 | 开始 | 终态 |
|---|---|---|
| ruff src | 71 | **0** |
| C901 | 8（runner 32 压主链） | **0**（migrate 进 ignore） |
| 测试 | 12,366 / 15 failed | 12,287 / **0 failed / 0 flaky** |
| DELETE-CANDIDATE | 17 挂账 | 执行 14 + 撤销 4，**EXPECTED_LABELS 归零** |
| PR gate | 全量 ~10 min | 9,278 tests / **61s**（llm/e2e/slow 隔离） |
| flaky 治理 | `--reruns` 主防线 | **根因清零**，reruns 仅兜底 |

## 3. 方法论沉淀（写进文档可复用的）

1. **doc first + git first + 最小触碰**：每个改动批次先设计文档、独立 commit、别人的东西逐条 rg 复验后才动
2. **A/B 归因法**：flaky/可疑改动用 stash 对照（双侧 N 次）判定因果，不信单次样本
3. **机械操作四次翻车的教训**（decorator 粘连/错绑、脚本字串替换误伤、genexpr 笔误）——规则：**语义变换必须手工、块操作含装饰器、修补出错宁回滚**
4. **标签 ≠ 事实**：5 处 DELETE-CANDIDATE 撒谎（workflow/dag、session/store×3、Skeleton）——**删除前 rg 复验是硬前置**

## 4. 明确未做（留给下一程）

| 项 | 性质 |
|---|---|
| **branch protection** | GitHub 网页手动（`p0-guardrails-design.md` §4 清单就绪） |
| **eval 基线首跑** | nightly 自举 或本地 `--update-baseline`（脚本就绪） |
| **pyright 宽松模式** | 唯一可继续「清」的项（类型债，渐进） |
| 巨文件 | 判断不动（三轮验证） |
| `_archived` fixtures 注记 | 存档，不清理 |

## 5. 收官判断

技术债（NameError 家族、C901 主链、lint 噪音、flaky、死代码、假选项、无门禁）**全部清零或转化为防线**。仓库从「边清边修」转入「跑起来」状态——下一步质量投资点是 eval 基线的日常化与 branch protection，而不是继续找下一个债。
