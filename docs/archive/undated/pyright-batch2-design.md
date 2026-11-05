# Pyright 批 2 — B 机械注解对齐

> 前置：`docs/archive/2026-10/pyright-triage-20261008.md` §3 · 当前 951 errors · 目标 **951 → ~844**
> 清单：`docs/archive/undated/pyright-batch2-list.md`（107 处 / 25 文件）

## 1. 修复策略（按子类）

| 子类 | 数量 | 修法 | 运行时 |
|---|---|---|---|
| **B1. pandas stub 联合返回** | ~61 | `typing.cast(Series/DataFrame, expr)` 锚定实际返回 | 无 |
| **B2. 注解过窄** | ~20 | 放宽返回/参数注解（`dict[str, str]`→`dict[str, Any]`） | 无 |
| **B3. Optional 运算/下标/迭代** | ~15 | 局部 `is not None` 判 / `or 0` / 绑定局部变量 | 行为等价 |
| **B4. 全路径必须返回** | 3 | `raise NotImplementedError`（Protocol stub） | 无 |
| **B5. 别名/Literal 不一致** | ~8 | `str` 常量命名空间的注解对齐为 `str` | 无 |

**原则**：
- 零行为变更（B3 仅加显式 None 分支，语义与原隐式路径一致）
- 不用 `# type: ignore` 降噪——用 `cast` / 放宽注解 / 显式判空
- 机械变换用 AST 脚本（仅限 B1 单行 `return`），改完 `git diff` 人审 + pyright 复验

## 2. 文件分组（按修法）

### B1 pandas cast（AST 脚本 + 人审）
| 文件 | 处 |
|---|---|
| `core/compute_factor.py` | 39 |
| `core/alpha_zoo_ops.py` | 9 |
| `core/data_source/akshare_loader.py` | 5 |
| `core/tools/data_transforms.py` | 5 |
| `core/config_runner.py` | 1 |
| `core/data_source/fred_loader.py` | 1 |
| `core/portfolio/cli.py` | 1 |
| `core/utils/backtest_metrics.py` 等 | 视情况 |

### B2 放宽注解（手工）
| 文件 | 修法 |
|---|---|
| `api/session/service.py:130` | `dict[str, str]` → `dict[str, Any]`（docstring 已写 `limit: 10`） |
| `api/session/service.py` 另一处 | 同上 |
| `core/workflow/store.py` ×4 | `list[dict[str, Any]]` 过滤 None 或改 `list[dict[str, Any] \| None]` |
| `core/agent/context_injector.py` | `list[ContextInjector]` → `Sequence[ContextInjector]` |
| `cli/theme.py` | `Iterator` → `AbstractContextManager`（`@contextmanager` 返回 CM） |
| `core/utils/backtest_utils.py` | `list[Timestamp]` → `list[Timestamp \| NaTType]` 或过滤 |
| `core/agent/builtin_tools/backtest_analysis_tools.py` | `str \| NDArray` → 抽成 `str`（`str(...)`/索引） |
| `core/alpha_zoo_convert.py` / `alpha_zoo_yaml.py` | `dict`/`object` 返回对齐 |
| `core/agent/sandbox/protocol.py` | 全路径补 `raise NotImplementedError` |

### B3 Optional 判空（手工，行为等价）
| 文件 | 修法 |
|---|---|
| `core/study/runner.py:430` | `if self._round_start_clock is not None:` |
| `core/study/runner.py:638` | `max_rounds = ...; if max_rounds is not None and round_num >= max_rounds` |
| `cli/tui/widgets/transcript.py:414` | 同上模式 |
| `api/session/projection_store.py` ×4 | Iterable None 判 |
| `factor_tools.py:184` / `backtest_analysis_tools.py:413` / `cli/__init__.py:197` | 下标前 None 判 |
| `validation/walk_forward.py` / `covariance.py` | 操作数类型收窄 |

### B5 ShutdownReason 别名（手工）
`core/study/runner.py`：类属性是 `str`，注解却写 `ShutdownReason`。
**修法**：返回注解统一为 `str | None`（常量仍挂在 `ShutdownReason` 命名空间），`_run_loop() -> str | None`。不改 Enum（避免 JSON/比较语义漂移）。

## 3. 执行顺序

1. **doc + commit**（本文档 + batch2-list）
2. **B1** AST 脚本 → `git diff` 人审 → 跑该文件测试
3. **B2/B3/B5** 手工逐文件
4. **验证**：pyright（预期 ~844）+ 全量测试 0 failed + regressions-gate
5. **commit**：`fix: pyright batch 2 — mechanical annotation alignment`

## 4. 验收

- [x] 本批规则 errors → 0（`reportReturnType`/`Operator`/`OptionalSubscript`/`Iterable`）
- [x] 总 errors 951 → **839**（Δ-112；批 2 清单 107 + 顺手 5）
- [x] 相关模块测试：747 + 218 = **965 passed / 0 failed**
- [x] 无行为变更（B3 判空路径与原隐式一致）
- [x] 附带修复：`TraceWriter` 句柄泄漏（WinError-32）+ `read_text(encoding="utf-8")` + `test_sse_e2e` 补 e2e lane

**注**：本机全量 PR-gate 仍有 Windows 环境既有失败（GBK `read_text`、路径分隔符 `\ws`、进程句柄），与批 2 无关；已抽查对照 pre-change 代码同样失败。

## 5. 落地结果（2026-10-08）

| 项 | 前 | 后 |
|---|---|---|
| total errors | 951 | 839 |
| B 规则 | 107 | 0 |
| cast wraps (B1) | 0 | ~70 |
| 另修 | — | TraceWriter.close + 2 test encoding + e2e lane |
