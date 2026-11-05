# Phase 5-ACI 设计：工具面 Poka-yoke 审计

> 来源：`refactor-plan-20261008.md` Phase 5（调研 #5）· Anthropic《Building effective agents》Appendix 2「ACI 工程 ≥ HCI」
> 目标：44 个工具的**命名/描述/参数/边界**四面审计，按 Poka-yoke（防呆）标准修
> 范围：`core/agent/builtin_tools/`（13 文件 44 工具）· `toolsets.py`（16 组裁剪）；签名变更需 caller 复验，优先**描述与默认值级修复**（零签名变更）
> 状态：审计框架定稿，待执行

## 1. 现状（inventory 实测）

| 维度 | 数据 |
|---|---|
| 工具总数 | **44**（13 个 builtin_tools 文件） |
| toolset 组 | **16**（minimal / chat / 9 角色 / backtest_diagnostics / critic / planner / evaluator / study_*） |
| 已见不一致 | `list`（file_tools）与 `list_files`（minimal 白名单）撞名 · `read_document` 不在任何 toolset · `pattern_recognition`/`options_pricing`/`task`/`todowrite`/`shell`/`import_data`/`skill` 游离白名单 |

## 2. 审计维度（每工具四问）

| 维度 | 问题 | Poka-yoke 标准 |
|---|---|---|
| **命名** | 同概念统一叫法？`list` vs `list_files`、`read` vs `read_document` 边界清晰？ | 单一词汇表达单一职责 |
| **描述** | docstring 是否给初级工程师写（用法、边界、何时不用）？ | Anthropic：示例/边界/输入格式/与他工具区分 |
| **参数** | 防呆：强制绝对路径？必填校验？互斥/默认？合法值枚举？ | 难以传错（例：`file_read` 强制绝对路径根治相对路径错——Anthropic SWE-bench 案例） |
| **边界** | 与他工具职责重叠？toolset 白名单覆盖合理？ | 一工具一职；游离工具决定「进白名单」或「标实验」 |

## 3. 执行计划（~0.5 天）

1. **审计**（0.5 天）：每类挑代表（`read`/`write`/`shell`/`websearch`/`tool_help`/`run_backtest`/`compute_factor`）+ 撞名对（`list` vs `list_files`/`read_document`）——出四维打分表
2. **修复**（优先级）：
   - 命名冲突/边界：文档澄清（不改签名，避改 caller）
   - 描述质量：docstring 增强（用法/边界/防呆说明）
   - 参数防呆：绝对路径强制、默认值、合法值——**零签名变更**（用 default/校验内部强化）
   - toolset 白名单：游离工具归位（`read_document` 进 web 相关组？`pattern_recognition` 进 factor_analyst？）
3. **验收**：派生测试（`rg -l "toolsets|builtin_tools|tool_help" tests`）+ 69 回归 + ruff src=0

## 4. 边界

- **不改签名**（调用方多、语义深；签名变更是 Phase 5-crash 或专项）
- **不删工具**（白名单归位优先；DELETE-CANDIDATE 风格的删除需另立）
- **不改 toolset 组定义**（先审计归位，多工具进组是「归位」，删组是「决策」）

## 5. 风险

- 描述改动影响 LLM 行为（prompt 面）——**轨迹 diff 是主检测器**（Phase 0 安全网）
- 参数默认值变化——**必须复验调用方**（`rg "tool_name("`）；默认值收紧=行为变化

## 6. 审计执行记录（2026-10-08）

### 6.1 覆盖率（44 工具）

| 档位 | 数量 | 备注 |
|---|---|---|
| **SPEC 模板**（工具说明书 7 段） | **40** | 用途/参数/示例/边界/错误范式/相关工具/版本——Anthropic 标准已实践 |
| DESC only | 1 | `engine_run`（backtest 管道子工具） |
| bare（无 description） | 3 | `config_load` / `data_prepare` / `data_gate`（backtest 管道子工具，run_backtest 内部步骤） |

**判定**：主产品面 40/44 已达 Poka-yoke 说明书标准；4 个残缺集中在 **backtest 管道子工具**（内部步骤，agent 不直接调用），价值有限。

### 6.2 发现并修复：toolset 白名单缺口（真 bug）

`minimal` / `chat` 两组配置 `list_files`，但 registry 里的实现叫 `list`（file_tools）——
**minimal 模式下 agent 缺目录列表能力**（白名单指向不存在的工具名）。

修复：toolsets.py 两处 `list_files` → `list`（匹配 registry 真名，零签名变更）。
A/B 实证：修前 minimal 解析 MISSING `list_files`；修后 MISSING 清零，68 toolset/gateway/role 测试绿。

### 6.3 清单：孤儿工具 14 个（任何 toolset 组都不引用）

| 工具 | 归属文件 | 判定 |
|---|---|---|
| `list`（修复后归 minimal/chat） | file_tools | ✅ 已归位 |
| `create_goal` / `add_evidence` / `complete_goal` / `get_goal_status` / `list_goals` | goal_tools | **决策**：应进研究流 toolset？（planner 已有 `list_goals`，其余 4 个未归组——可能是有意「goal 由系统调用非 agent」） |
| `read_document` | web_tools | **决策**：应进 chat/researcher？ |
| `import_data` / `pattern_recognition` / `options_pricing` | data/factor/options | **决策**：按角色能力归组？ |
| `shell` / `task` / `todowrite` / `skill` / `list_skills` | 能力面工具 | **保留**：opt-in 语义（chat_loop `allow_shell_tools` 门；skill 系统按需载入） |

**结论**：孤儿不是 bug 是**有意裁剪**（capability 面待 opt-in）。归组是产品决策（触发条件：某个角色第一次需要该能力时）。

### 6.4 待办（ACI 审计后续）

- 4 个 backtest 子工具补说明书（半天，优先级低——agent 不直接调）
- `list` vs `list_files` 命名统一（**签名变更**，需 LLM prompt 兼容评估）——留 Phase 5-crash 或专项
- goal_tools 5 个归组决策（产品面）
