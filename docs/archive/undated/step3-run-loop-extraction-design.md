# Step 3 设计：`_run_loop` 生命周期分支抽取

> 计划来源：`docs/archive/2026-10/phase-engine-refactor-plan-20261008.md` §3 Step 3
> 前置：Step 0-2 已完成（claims 修复 → RoundOutcome 契约 → 查表分派）
> 范围约束：**仅改 `src/strategy_research/core/study/runner.py`**；`_run_loop` 对外行为与返回值（`ShutdownReason`）不变；不碰 phase_engine / monitor / scheduler
> 状态：设计定稿（含方向修正），待实施

## 1. 方向修正：方法抽取，而非 `StudyLifecycle` 类

原计划（plan §3 Step 3）写的是「抽 `StudyLifecycle` 状态机类，主循环变 `while not terminal: transition()`」。读码后修正：

| 原设想 | 读码事实 | 结论 |
|---|---|---|
| 状态归状态机类 | `best_score` / `idle_rounds` / `total_used_turns` / `total_used_time` / `prev_passed` 等**已是 runner 实例字段**，多处（budget 恢复、stop 判定、SSE emit）直接读写 | 搬状态 = 大面积改 runner 状态管理，风险高收益低 → **状态留在 runner** |
| 所有分支都是状态转移 | 实际是两类：① 每轮顶部的**控制检查**（cancel/pause）② **轮结果处理**（aborted/HITL/stop/max/early-stop）——后者是流程分支，硬套 transition() 会把直白流程复杂化 | 不做全面状态机化 |
| — | `phase_engine` Step 2 用方法抽取把 C901 24→8、主函数 421→184，模式已被 1279 回归验证 | **沿用同模式** |

**修正后方向**：按分支块抽 3 个方法，返回值携带状态语义（`ShutdownReason | None`、控制流指令），主循环只保留 while + 调用 + continue。状态机语义保留在返回值约定里；未来若需要持久化多状态，可在方法抽取之上再演进。

## 2. C901 = 32 的构成审计（`runner.py:362-690`，329 行）

| 块 | 行范围 | 分支内容 | 估贡献 |
|---|---|---|---|
| A. 顶部控制检查 | 391-416 | `cancelled`（含 ARCHIVED 子判断）、`paused`（含暂停中被取消子判断） | ~6 |
| B. 轮准备 | 418-441 | objective flush `applied_now` 子分支 | ~2 |
| C. 轮结果：aborted | 458-464 | novelty 拒绝 → budget 记账 → continue | ~1 |
| **D. HITL pause/resume** | **467-568** | PAUSED/emit/时间 bank；decision≠approved → expire/interrupted/RUNNING；cancelled 子分支；resume 后 3 种失败（repause / no_checkpointer / aborted）；e2 重算 | **~12** |
| E. 轮收尾 emit | 589-649 | claims try/except、scoreboard hasattr、sb_data 条件 | ~5 |
| F. 终止条件 | 652-680 | stop_conditions、max_rounds、early-stop（嵌套 idle 判断） | ~6 |
| G. cooldown | 685-687 | — | 0 |

**最大杠杆：D（~12）**，其次 E/F。

## 3. 抽取方案

### 3.1 三个新方法（均为 `AutoresearchRunner` 的私有 async 方法）

```python
async def _top_control_check(self, round_num, session, sid) -> str:
    """Loop-top control: 'cancelled' | 'resumed' | 'continue'.
    Covers块 A — including the ARCHIVED-preserve and paused-cancelled
    sub-branches."""

async def _handle_hitl_pause(
    self, result, round_num, session, sid, previous_summary,
) -> tuple[dict | None, str]:
    """Covers block D. Returns (resumed_result_or_None, instruction).
    instruction: 'continue' (skip round — rejected/repause/no_checkpointer/
    aborted/loop-top-cancel) | 'advance' (approved + resumed, dict is the
    rebuilt result)."""

async def _finalize_round(
    self, result, metrics, verdict, round_num, previous_summary,
    session, sid,
) -> ShutdownReason | None:
    """Covers blocks E+F: budget/heartbeat/SSE + stop/max/early-stop.
    Returns None to keep looping, or the terminal ShutdownReason."""
```

主循环变为：

```python
while True:
    control = await self._top_control_check(round_num, session, sid)
    if control == "cancelled":
        return ShutdownReason.CANCELLED
    # 'resumed' or 'continue' both fall through to round increment
    ...round prep (block B stays —只有 ~2 分支)...
    result = await asyncio.to_thread(self._run_one_round, ...)
    ...
    if result.get("aborted"):
        self._account_round_budget(result); continue
    if result.get("paused_for_approval"):
        resumed, instruction = await self._handle_hitl_pause(...)
        if instruction == "continue":
            continue
        result = resumed
    ...metrics 绑定...
    stop = await self._finalize_round(...)
    if stop is not None:
        return stop
    ...cooldown...
```

### 3.2 明确不搬的东西

- **状态字段**（`_best_score` / `_idle_rounds` / `_total_used_*` / `_prev_passed` / `_round_start_clock`）——留在 runner
- **块 B（轮准备）**——只 ~2 分支，留在主循环保可读性
- **aborted 单分支**——3 行，inline 更清楚
- `_run_one_round`、`_check_stop_conditions`、`_wait_for_approval` 等既有方法——不动

### 3.3 语义保持红线（逐条对应原代码注释）

| 行为 | 出处 | 搬移要求 |
|---|---|---|
| ARCHIVED 不被 CANCELLED 覆盖 | 原 393-404 | `_top_control_check` 原样保留双 emit |
| HITL 等待不计入时间预算（bank elapsed） | 原 476-480（M2） | `_handle_hitl_pause` 原样保留 |
| decision≠approved：expire interrupt 防僵尸 | 原 487（M1） | 原样 |
| no_checkpointer / repause：不写幽灵空 metrics round | 原 538-565（H2） | 原样 |
| resume 后重算 e2_passed/passed_now | 原 569-587 | 原样（随 D 块进 `_handle_hitl_pause`） |
| early-stop 仅在 max_rounds 配置时生效 | 原 665 | `_finalize_round` 原样 |

## 4. 验收标准

| # | 项 | 通过条件 |
|---|---|---|
| 1 | C901 `_run_loop` | **≤15**（现 32；Phase_engine 先例 24→8） |
| 2 | `_run_loop` 行数 | 329 → 目标 <130 |
| 3 | 定向测试 | `test_study_runner_{comprehensive,integration}` + `test_study_rounds` + `test_hitl_*` + `test_resume_round_*` + `test_study_hitl_respond` + `test_study_budget` + `test_study_error_recovery` 全绿 |
| 4 | 69 文件回归 | 0 failed（基线 1279） |
| 5 | ruff | runner.py 无新增（F821/F811 尤其——搬移最易漏名字） |

## 5. 风险与回滚

- **D 块搬移是核心路径**（HITL resume 3 失败分支）——靠 hitl/resume 定向测试 + 69 回归 + e2 重算语义注释三重兜底
- **F821 兜底**：搬移后漏名字立刻炸（Step 1-2 的经验）
- **回滚**：单 commit revert；对外无签名/返回值变化

## 6. 与 plan 原文的差异记录

plan 写「抽 `StudyLifecycle`」，本文档 §1 论证改为「方法抽取 + 返回值状态语义」。状态类的引入推迟到有真实需求时（多状态持久化 / 跨 runner 复用），届时本次抽取正是它的基础。

## 7. 验收记录（2026-10-08，实施完成）

| # | 项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | C901 `_run_loop` | ≤15 | **12**（32 → 12） | ✅ |
| 2 | `_run_loop` 行数 | <130 | **114**（329 → 114） | ✅ |
| 3 | 定向测试（hitl/resume/runner/monitor 家族） | 全绿 | **107 passed** | ✅ |
| 4 | 69 文件回归 | 0 failed | **1279 passed / 0 failed** | ✅ |
| 5 | ruff | 无新增 | F821/F811/E9 清；1 个 pre-existing F401（`_aegis` import，非本次引入） | ✅ |

### 抽取结果

| 方法 | 覆盖块 | 行数 |
|---|---|---|
| `_top_control_check` | A：cancel/pause（ARCHIVED 保留、H3） | ~40 |
| `_handle_hitl_pause` | D：HITL 全路径（M2 bank、M1 expire、H2、H1 e2 重算） | 142 |
| `_finalize_round` | E+F：budget/heartbeat/harvest/3×SSE/stop/max/early-stop/prev_passed | ~95 |
| `_run_loop` 主体 | 循环 + 轮准备 + aborted + 调用 + cooldown | **114** |

语义红线（§3.3 六条）逐条随块搬移，测试绿（hitl/resume/runner 107 + 全量 1279）证明等价。

### 实施波折（记录备查）

块 D 首次用脚本机械转换（dedent + continue 改写 + 变量改名）失败：缩进破坏 + 7 处 continue 残留，文件语法损坏。回滚后改为**手工按语义重写方法体**（参照原文逐段转换），一次通过。与 Step 1 的教训一致：**机械改写适合纯搬运（块 E+F 的单一 dedent），含语义变换的搬移必须手工**。
