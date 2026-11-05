# Agent 工具参考手册 (Tool Reference)

系统化梳理 AgentLoop 注册的全部 32 个内置工具：用法、输入参数、预期输出、前置条件、注意事项、以及输出后对 agent 的后续引导建议。

> 维护约定：工具源位于 `src/agentnodes/core/agent/builtin_tools/`。
> 本文档应与 `build_default_registry()` 的输出保持一致；新增/修改工具时请同步更新。

---

## 工具范式 v2（设计定稿）

> 状态：**已完成实施（P1-P5，2026-08-05）**。本范式由 9 个维度的逐项讨论收敛而成；
> 实施计划、契约测试见下文；P6 被动学习已立项，规划见 [docs/archive/undated/passive-learning-proposal.md](passive-learning-proposal.md)。
> 落地验证：477 tests passed（工具/loop/chat/workflow/role 相关面），
> 遗留事项见"落地验证与遗留"小节。

### 范式总览（9 维度决策）

| 维度 | 定稿 |
|---|---|
| **本体** | 工具 = `name + 声明 + 执行体`；无状态机；工具间可互相调用（一般不），组合深度 = 1 |
| **被动学习** | 记录被动（event_log + trace.jsonl 双源，agent 正常使用即落盘）+ 扫描主动（定期挖掘）；双粒度挖掘（同 turn 合作性共现 / 跨 turn 流程性序列），沉淀标准不同；**后置实现（P6）** |
| **组合形态** | 声明式配置（`steps` + 参数映射符号）→ 组合库（workspace `tools/combo/*.yml`）→ 运行时实例化注册；只支持线性步骤，复杂逻辑改手写工具——**配置不滑向 DSL** |
| **定义形态** | 源码单源说明书（统一模板，docstring 约定节）+ 注册时 inspect 自动生成**简略版**预置 prompt + `tool_help` 按需返回**详细版**；消灭手写 `parameters` dict 双写 |
| **错误范式** | C 混合：业务失败 `return err_actionable(...)`；意外异常 `raise` → `BaseTool` 顶层统一结构化兜底；语义区分：return = 确定性失败（不重试）、raise = 意外（transient 可重试） |
| **上下文与副作用** | 显式 `ToolContext`（workspace / session_id / progress），注入参数从 LLM schema 剥离；声明式 `effects`（写 DB / 写 FS / 网络），`is_readonly` 作为派生布尔保留兼容 |
| **参数容错** | 框架统一层：按说明书类型声明驱动（JSON 字符串 parse / 单键包裹解包 / 类型强转），仅在声明类型与收到类型不匹配时触发；`safe_get_param` 退役 |
| **注册发现** | C 分层：显式核心清单 + 能力组注册函数（`check_available` 依赖门控保留）+ 组合库加载器；契约测试保障 注册表 ↔ `__all__` ↔ 说明书 ↔ docs 一致 |
| **编排引导** | 三层面：简略版目录（常驻，只答"哪个工具合适"）/ 详细版说明书（按需，含使用时机/相关工具/错误处理范式）/ `err_actionable.fix`（运行时 debug）；fix_msg 与说明书同源；高频流程沉淀为组合工具 |

### 工具说明书模板（统一约定，最终定稿）

每个工具在源码 docstring 中按约定章节书写说明书；注册时与查询时各生成一版。

**类型/默认值单源**：`execute` 使用显式签名（`def execute(self, ctx, strategy_name: str, yaml_path: str | None = None)`）——参数名/类型/默认值由签名与注解承担（框架容错层消费 `__annotations__`，简略版必填参数由此派生）；docstring 只写语义。存量 `**kwargs` 工具迁移完成前，注册时回退 `parameters.required`。

```
简略版（注册时 inspect 生成 → 预置 prompt，轻量目录）：
  - name[类别]: 用途一句话；必填: p1,p2；副作用: 只读|写DB,写FS,网络

  字段来源：
    name        类名
    用途一句话   docstring 首行（PEP 257 摘要行，与详细版同源，~20 字）
    类别         领域分类：文件/回测/因子/行情/分析/技能/Web/Goal/Shell
                （组合工具 = 配置指定，默认按子工具主类别推断）
    必填参数     显式签名无默认值参数（只列名，不列类型/可选）
    副作用       effects 声明（写DB / 写FS / 网络，可组合；未声明回退 is_readonly）
  预算：33 工具 ≈ 600-700 token/轮（现状全参数渲染的 1/3~1/5）

详细版（tool_help(name) 实时读源码 → docstring 原文，按需返回）：
  # <name> 工具说明书
  版本          x.y.z（语义版本）
  变更          当前版关键变更摘要（历史归 git）
  ## 用途       2-4 句：做什么、典型场景、何时不用（与其他工具的分工边界）
  ## 参数       每个参数的语义、默认值含义、参数间关系（类型/默认值由签名
              单源，不在此重复）
  ## 示例       1-2 个最小可用调用示例（JSON 形式，含必填参数）
  ## 边界       前置条件、上下文安全行为、幂等性、单位/格式约定
  ## 错误处理范式 各失败场景：触发条件 → fix 指引 → 是否可重试 → 是否幂等
  ## 相关工具   前置工具 / 结果消费工具（可选）
```

- docstring 约定节：`版本:` / `变更:` / `## 用途` / `## 参数` / `## 示例` / `## 边界` / `## 错误处理范式` / `## 相关工具`
- `tool_help` 返回 docstring **原文**（markdown，无解析层）；组合工具说明书两版从组合配置（steps + 映射符号）生成

### 组合工具细节（已落地，`core/agent/combo.py`）

- **配置**：workspace `tools/combo/<name>.yml`：
  ```yaml
  name: read_two
  description: 读取两个文件并返回第二个的内容
  category: 文件
  steps:
    - tool: read_file
      params:
        path: input.path1        # 组合工具输入参数
    - tool: read_file
      params:
        path: step1.result.path  # 前一步 JSON 结果的字段路径
  with_summary: false            # true 时返回步骤摘要
  ```
- **参数映射符号**：`input.<path>`（组合输入，点路径）/ `step<N>.result.<path>`（前一步结果字段）/ 其他字面量
- **加载**：`build_default_registry(workspace=<ws>)` 自动扫描 `tools/combo/*.yml` 注册；非法配置跳过并告警
- **执行**：`registry` 工具级调用子工具（`invoke`：容错 + 错误兜底同普通工具）；同一 `ToolContext` 传递
- **中间结果**：正常路径**不进上下文**；默认返回 `{status: ok, ...最后一步输出}`；`with_summary: true` 附加 `combo_summary`；报错时**完整错误透传** + `combo_step`/`combo_tool` 定位
- **副作用**：父工具 effects = 子工具配置并集；嵌套深度 = 1（组合引用组合被拒绝）
- **说明书**：生成动态 docstring（用途/步骤/参数/边界/错误处理范式/相关工具），`tool_help` 与简略版 brief 同机制
- **沉淀闭环**：被动学习挖掘 → 规则初筛 + 组合提案 + 人工确认 → 写入组合库（P6 立项后落地）；高频复用的组合可"提升"为手写工具固化进显式清单

### 契约测试（P5 落地，`tests/test_tool_contract.py`）

注册表 ↔ 说明书 ↔ 副作用 ↔ schema 一致性由测试守护：
- name 唯一、brief 完整、category 已声明、docstring 首行 = 简略版用途（同源）
- 写工具 effects 非空（9 个 write 工具逐一断言）、只读工具 effects 为空、effects 枚举合法
- 注入参数（workspace 等）不出现在 schema；必填参数与签名无默认值参数一致（strict 工具除外）
- 引导同源：run_backtest/compute_factor 说明书与代码 fix 的 workflow 一致，`commit_market_data` 全库零残留

### 落地验证与遗留

**验证**（P1-P5 完成后全量回归）：工具/loop/chat/workflow/role 相关 26 个测试文件
477 passed、4 skipped；契约测试 20 例、组合库 18 例常驻守护。
说明书 8 节完整性已纳入契约（全部 34 个注册工具，`test_spec_sections_complete`）。

**遗留事项**：
- **P6 被动学习**（后置立项，规划详见 [docs/archive/undated/passive-learning-proposal.md](passive-learning-proposal.md)）：trace.jsonl + event_log 双源、双粒度（同 turn 合作性 / 跨 turn 流程性）共现挖掘 → 规则初筛 + 组合提案 + 人工确认 → 写入组合库；框架已就绪（组合库/加载器/契约测试），缺挖掘器 + 产线 trace 接线（当前 `trace_dir` 无人传）
- **pre-existing 失败**：`test_assistant_message_event.py` 5 例（loop compact 溢出检测对 mock config 的 `overflow_ratio` 比较抛 TypeError）；`test_b3_read_path_consistency` 的 limit 语义已修；`test_role_factory` 偶发 LLM 超时（环境相关，单跑绿）
- **说明书已全量补全**：goal/analysis/skills 等 17 个简版工具说明书已补全为 8 节模板（版本 1.1.0），契约测试强制全部注册工具 8 节完整

### 实施计划

| 阶段 | 内容 | 状态 |
|---|---|---|
| P1 | 说明书基础设施：模板规范 + 注册时解析器（简略版）+ `tool_help` 工具 | **已完成** |
| P2 | BaseTool 骨架 v2：ToolContext / 框架统一容错层 / 错误兜底 / effects；loop 注入单点化（sync/async 合一） | **已完成** |
| P3 | 33 个存量工具迁移（一次性切换，无旧 `parameters` dict 回退）：docstring 说明书填写、删手写解析与样板、effects 声明 | **已完成** |
| P4 | 分层注册重构 + 组合库：核心显式清单 + 能力组 + 组合库加载器 + 组合执行器 | **已完成** |
| P5 | 契约测试（注册表↔说明书↔effects↔schema 一致性；fix_msg 与说明书同源断言）+ 文档同步 | **已完成** |
| P6 | 被动学习：挖掘器（双源双粒度共现）+ 评估器（规则初筛 + 提案 + 人工确认）+ 沉淀（组合库 + 回馈统计） | 已立项，规划见 [docs/archive/undated/passive-learning-proposal.md](passive-learning-proposal.md) |

### 对本文档其余部分的衔接

- **通用约定**（下方）：已随 v2 落地更新——`workspace`/`session_id` 从 schema 剥离（经 ToolContext 注入）、副作用改为 effects 声明、错误范式分两档（ToolError 结构化 / 框架兜底）
- **各工具条目**（§1-§8）：已按说明书模板重写（与源码 docstring 同源；契约测试守护 docstring 首行 = brief 同源）
- **工具后续引导策略（next_step 取舍）**：由维度 6"三层面引导"继承并演进——返回值仍保持纯净（`next_step` 不回归），引导迁入说明书与运行时 fix

---

## 总览

| 类别 | 工具 | 副作用（effects 声明） | 白名单角色 |
|------|------|--------|-----------|
| 文件/代码 | `read_file` `list_files` `write_file` `git_diff` | write_file: 写FS；其余只读 | 多角色 |
| 回测 | `run_backtest` `list_history` `strategy_compare` `drawdown_analysis` `benchmark_comparison` | run_backtest: 写DB,写FS；其余只读 | strategist / backtest_diagnostics |
| 因子 | `compute_factor` `factor_analysis` `factor_cross_sectional_analysis` `factor_quintile_returns` `factor_ic_decay` `factor_turnover` | 只读 | factor_analyst / researcher |
| 行情数据 | `get_market_data` `import_data` `list_data_sources` `search_symbol` | get_market_data: 写DB,网络；import_data: 写DB；其余只读 | researcher / data_quality / strategist |
| 其他分析 | `options_pricing` `pattern_recognition` | 只读 | — |
| 技能 | `list_skills` `load_skill` `tool_help` | 只读 | 通用 |
| Web | `web_search` `read_url` `read_document` | 网络访问（effects: 网络），只读语义 | researcher / strategist |
| Goal | `create_goal` `add_evidence` `complete_goal` `get_goal_status` `list_goals` | 写DB（goals.db；create/add_evidence/complete） | 通用 |

**通用约定（v2）：**

- **调用约定**：`execute(ctx: ToolContext, 显式参数)`——参数名/类型/默认值由签名与注解单源承担；schema 由注册时从签名派生。
- **注入参数**：`workspace`/`session_id` 由框架经 ToolContext 注入（loop 统一接线），**不出现在 schema**，LLM 无需（也不应）传值；显式调用时 `ctx=None` 兼容部分工具。
- **副作用**：写工具在类上声明 `effects`（`db`=写数据库 / `fs`=写文件系统 / `net`=网络访问）；`is_readonly` 由 effects 派生。写工具清单（9 个）：`write_file` `run_backtest` `get_market_data` `import_data` `clean_data` `create_goal` `add_evidence` `complete_goal` `run_command`。
- **错误范式**（两档）：
  - 业务/容错失败抛 `ToolError` → 结构化 `{"status": "error", "error", [received/expected/fix/tool]}`（received/expected/fix 可选，按场景填充）；
  - 意外异常由框架兜底 → `{"status": "error", "error": "<类型>: <信息>", "tool": <名>}`，并记日志；
  - 网络/临时性错误（`TRANSIENT_TOOL_ERRORS`）由 loop 自动重试。
- **框架容错**：`_coerce_params` 按签名注解在类型不匹配时强转（JSON 字符串 list/dict、单键包裹、int/float/bool 转义）；缺必填参数 → TypeError 由框架拦截并重试/兜底。
- **依赖检测**：`web_search`/`read_document` 依赖对应 Python 包，缺失时被排除注册。
- **组合工具**：workspace `tools/combo/*.yml` 自动加载注册（见"组合工具细节"）。

---

## 1. 文件 / 代码

### read_file

- **类别**: 文件 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名 + ToolContext; schema 自动派生)
- **用途**：
  读取工作区内文件内容, 支持 limit/offset 分片。路径相对 workspace,
  必须位于允许的读取根目录 (strategies/templates/memory/logs/data/docs/.)。
- **参数**：
  - path: 相对 workspace 的文件路径 (必填)
  - limit: 返回的最大行数 (可选)
  - offset: 起始行偏移, 0 起 (可选)
- **示例**：
  {"path": "strategies/momentum_20d/strategy.py"}
- **边界**：
  只读工具; 白名单外路径/绝对路径/.. 会被拒绝; 二进制/非 UTF-8 文件报错。
- **错误处理范式**：
  - 缺 path → error + expected 示例
  - 白名单外 → error + fix 提示允许根目录
  - 文件不存在/是目录 → error + fix 用 list_files 确认
  - 非 UTF-8 → 提示用 read_document 或跳过
  - 所有失败均可安全重试
- **相关工具**：
  list_files: 浏览目录; write_file: 写入

### list_files

- **类别**: 文件 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名 + ToolContext)
- **用途**：
  浏览工作区目录结构: 文件与子目录清单 (含大小)。读文件前先用它探索。
- **参数**：
  - path: 目录路径, 相对 workspace (默认 '.')
  - pattern: glob 过滤 (可选, 如 '*.py' / 'strategies/*')
- **示例**：
  {"path": "strategies"}
- **边界**：
  只读工具; 仅限 workspace 内目录; 文件路径会报错 (用 read_file)。
- **错误处理范式**：
  - 路径不存在 → error + fix 提示顶层结构
  - 目标是文件 → error + fix 用 read_file
  - 均可安全重试
- **相关工具**：
  read_file: 读文件内容; write_file: 写入

### write_file

- **类别**: 文件 ｜ **副作用**: 写FS
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名 + ToolContext; 副作用改 effects)
- **用途**：
  写入文件内容到工作区。路径限允许写根目录
  (strategies/templates/memory/logs); .py 文件做 AST 校验,
  危险代码 (exec/eval、受限 import、dunder 访问) 会被拒绝。
- **参数**：
  - path: 相对 workspace 的文件路径 (必填, 限写白名单)
  - content: 文件内容 (必填, 字符串)
- **示例**：
  {"path": "strategies/momentum_20d/strategy.py", "content": "..."}
- **边界**：
  写工具 (effects=fs); 自动创建父目录; 覆盖已有文件。
- **错误处理范式**：
  - 缺 path/content → error + expected 示例
  - AST 校验失败 → error 含具体危险代码说明
  - 白名单外 → error + fix 允许根目录
  - 写入失败 → error + fix 检查权限
  - 幂等: 重跑覆盖同一路径, 安全
- **相关工具**：
  read_file: 读回校验; list_files: 浏览

### git_diff

- **类别**: 文件 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名 + ToolContext)
- **用途**：
  查看 workspace 的 git diff。默认未暂存改动; staged=true 看暂存;
  ref1/ref2 对比两个提交。
- **参数**：
  - staged: 只看暂存改动 (默认 false)
  - ref1/ref2: 提交对比 (需同时给)
  - pathspec: 限定路径 (不能以 '-' 开头, 防参数注入)
  - max_lines: 返回最大行数 (默认 200)
- **示例**：
  {"pathspec": "strategies/momentum_20d/"}
- **边界**：
  只读工具; 要求 workspace 是 git 仓库; 超时 30s。
- **错误处理范式**：
  - 非 git 仓库 → error + fix (git init)
  - 超时 → fix 用 pathspec 缩小范围
  - 均可安全重试
- **相关工具**：
  read_file: 看具体文件; write_file: 修改后 diff

---

## 2. 回测

### run_backtest

- **类别**: 回测 ｜ **副作用**: 写DB, 写FS
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名 + ToolContext; effects 声明)
- **用途**：
  读取 strategies/<name>/config.yaml 运行回测, 产出新 run 写入
  runs/<name>/ 与 DuckDB。策略配置就绪且数据已入库后验证表现。
  数据未入库时先 get_market_data; 只看历史结果用 list_history。
- **参数**：
  - strategy_name: 策略目录名 (必填, strategies/<name>/config.yaml 须存在)
  - action: 运行标注 (审计用, 默认 'agent')
  - description: 可选描述
  - yaml_path: 覆盖默认 config 路径 (相对 workspace)
- **示例**：
  {"strategy_name": "momentum_20d"}
- **边界**：
  写工具 (effects: db + fs); 前置: price_data 已有该策略数据;
  同策略重复运行产生新 run, 不覆盖旧 run。
- **错误处理范式**：
  - 缺 strategy_name → error + expected 示例
  - 策略目录不存在 → fix 提示 list_files 查看 strategies/
  - 数据为空 / 无 DB → fix 给 workflow: get_market_data(persist=True) → 重跑
  - 配置 YAML 非法 → fix 指向 config.yaml 检查
  - 所有失败均可安全重试 (无部分写入遗留)
- **相关工具**：
  get_market_data: 数据前置; list_history/drawdown_analysis/
  benchmark_comparison: 结果消费

### list_history

- **类别**: 回测 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名 + ToolContext)
- **用途**：
  查看过去的回测运行记录: 从 strategies/<name>/runs/results.tsv 读取
  摘要行 (含关键指标)。不指定 strategy_name 时找第一个 results.tsv。
- **参数**：
  - strategy_name: 按策略过滤 (可选)
  - limit: 最大返回行数 (默认 20)
- **示例**：
  {"strategy_name": "momentum_20d"}
- **边界**：
  只读工具; 无 results.tsv 时返回空 runs + message。
- **错误处理范式**：
  - 读取失败 → error + fix 检查权限
  - 无记录不是错误 (返回空列表)
- **相关工具**：
  run_backtest: 产生记录; drawdown_analysis: 深度分析

### strategy_compare

- **类别**: 回测 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  读取多个策略 runs/results.tsv 的最新一行, 按指定指标列横向对比,
  用于回测结果选优。缺失结果文件的策略带 error 字段, 不整体失败。
- **参数**：
  - strategy_names: 逗号分隔的策略名列表 (必填)
  - metrics: 逗号分隔的指标列 (默认
  sharpe,ann_return,max_dd,calmar,turnover,win_rate)
- **示例**：
  {"strategy_names": "mom_20d,mom_60d", "metrics": "sharpe,ann_return,max_dd"}
- **边界**：
  只读工具; 需要 workspace; 各策略须已跑过回测 (results.tsv 存在);
  指标列不存在时该列为 null; 数值转浮点失败时保留原值。
- **错误处理范式**：
  - strategy_names 缺失 → error
  - 单策略 results.tsv 缺失/读取失败/无记录 → 该策略行带 error
  (非整体失败)
  - 幂等: 只读不写
- **相关工具**：
  前置: run_backtest; 后续: drawdown_analysis / benchmark_comparison

### drawdown_analysis

- **类别**: 回测 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  从最新 run 的权益曲线计算回撤序列: 最大回撤、当前回撤、回撤期
  数量与按深度排序的 Top N 回撤区间 (含开始/谷底/恢复索引与时长)。
  依据回撤深度与恢复时长判断风控参数是否需要调整。
- **参数**：
  - strategy_name: 策略名 (必填)
  - top_n: 返回的回撤区间数量 (默认 5)
- **示例**：
  {"strategy_name": "mom_20d", "top_n": 10}
- **边界**：
  只读工具; 需要 workspace; 最新 run 须含权益曲线
  (equity.csv/equity_curve.csv/portfolio.csv/nav.csv 之一, 或
  run.log 含 equity= 数值); 权益点 < 10 报错; 仍在回撤中的区间
  recovery_idx 为 null。
- **错误处理范式**：
  - runs 目录不存在/无 run → error
  - 找不到权益曲线或点 < 10 → error, 检查 run 输出
  - 幂等: 只读不写
- **相关工具**：
  前置: run_backtest; 后续: benchmark_comparison / strategy_compare

### benchmark_comparison

- **类别**: 回测 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  对比策略最新 run 的权益曲线与基准 (DuckDB ohlcv 中的指数/标的)
  的日收益: 年化 alpha、beta、跟踪误差、信息比率、最大相对回撤与
  双方年化收益。用于判断策略是否相对基准有超额。
- **参数**：
  - strategy_name: 策略名 (必填)
  - benchmark_code: 基准代码 (必填, 如 000300.SH, 须已在 ohlcv)
  - start_date/end_date: 基准数据时间窗 (可选, ISO 日期)
- **示例**：
  {"strategy_name": "mom_20d", "benchmark_code": "000300.SH"}
- **边界**：
  只读工具; 需要 workspace; 策略须有最新权益曲线 (≥10 点);
  基准代码须已入库; 两者按尾部对齐取较短长度; 基准查询用字符串
  拼接 asset 值 — 仅传已知代码。
- **错误处理范式**：
  - 策略/基准缺参 → error + expected
  - 基准未入库/无数据 → error, 先 get_market_data(benchmark_code)
  - 权益曲线缺失 → error
  - beta 分母为零时 beta/alpha 为 null (非失败)
  - 幂等: 只读不写
- **相关工具**：
  前置: run_backtest + get_market_data; 同类: drawdown_analysis

---

## 3. 因子

> 所有因子工具从 DuckDB `ohlcv` 视图读数据（`ohlcv` 是 `price_data` 表的视图，含 `date/asset/open/high/low/close/volume`）。
> 前置条件：至少一个资产已通过 `get_market_data(persist=True)`（或 `import_data`）入库。

### compute_factor

- **类别**: 因子 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名 + ToolContext)
- **用途**：
  在单资产宽表 (close/open/high/low/volume) 上计算因子表达式
  (如 'ts_mean(close, 20) / ts_mean(close, 60) - 1'), 返回结果采样。
- **参数**：
  - factor_code: 因子表达式 (必填)
  - asset: 资产代码 (默认第一个可用资产)
  - factor_name: 因子名 (可选, 用于展示)
  - n_samples: 采样数 (默认 5)
- **示例**：
  {"factor_code": "ts_return(close, 20)"}
- **边界**：
  只读工具; 读取 workspace DuckDB 的 ohlcv 视图 (price_data);
  数据为空会给 workflow 提示。
- **错误处理范式**：
  - 缺 factor_code → error + expected 示例
  - 无 DB/空表 → error + fix: get_market_data → compute_factor
  - asset 不存在 → error + expected 可用资产列表
  - 表达式错误 → error + available_columns 与示例表达式
  - 均可安全重试
- **相关工具**：
  get_market_data: 数据前置; factor_analysis/factor_quintile_returns 等: 后续分析

### factor_analysis

- **类别**: 因子 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名 + ToolContext)
- **用途**：
  对因子表达式做 IC/IR 分析: 计算 IC mean、spearman IC、观测数。
  需要 workspace DuckDB 有价格数据。
- **参数**：
  - factor_code: 因子表达式 (必填)
  - asset: 资产代码 (默认第一个可用)
  - forward_days: 前向收益天数 (默认 5)
- **示例**：
  {"factor_code": "ts_return(close, 20)"}
- **边界**：
  只读工具; 观测数 < 10 时返回 insufficient data 错误。
- **错误处理范式**：
  - 无 DB/空表 → error + workflow 提示
  - asset 不存在 → error + expected 可用资产
  - 数据不足 → error + 需要 >= 10 行
  - 均可安全重试
- **相关工具**：
  compute_factor: 单因子计算; factor_quintile_returns 等: 深入分析

### factor_cross_sectional_analysis

- **类别**: 因子 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  对资产池计算因子表达式的逐日截面 IC (Pearson + Spearman), 汇总
  IC 均值/标准差/IR/IC>0 比例, 并附前 5 个样本日期。验证因子在
  横截面上是否有区分度。单资产验证用 factor_analysis。
- **参数**：
  - factor_code: 因子表达式 (必填, 语法见 .skills/factor-research.md)
  - universe: 逗号分隔代码或 all (默认 all)
  - start_date/end_date: 数据时间窗 (可选, ISO 日期)
  - forward_days: 前向收益窗口天数 (默认 5)
- **示例**：
  {"factor_code": "ts_mean(close,20)/ts_mean(close,60)-1",
  "universe": "600519.SH,000858.SZ,000001.SZ"}
- **边界**：
  只读工具; 需要 DuckDB ohlcv 数据; 需 ≥3 资产且 ≥3 个因子计算
  成功; 有效 IC 观测 ≥5; 样本 < 20 根 K 线的资产被跳过。
- **错误处理范式**：
  - universe 含不存在代码 → error + 缺失列表
  - 资产数/因子成功数 < 3 → error, 需先入库更多资产
  - IC 观测 < 5 → error "too few valid IC observations"
  - ohlcv 为空/库不可用 → error, 先 get_market_data(persist=True)
  - 幂等: 只读不写
- **相关工具**：
  前置: get_market_data; 后续: factor_quintile_returns / factor_ic_decay;
  同类: factor_analysis (单资产)

### factor_quintile_returns

- **类别**: 因子 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  把资产池按因子值逐日分为 N 组 (默认 5 组), 计算各组的平均前向
  收益 (holding_period 天) 与多空价差 (Qn - Q1), 检验因子分组
  单调性。
- **参数**：
  - factor_code: 因子表达式 (必填)
  - universe: 逗号分隔代码或 all (默认 all)
  - start_date/end_date: 数据时间窗 (可选)
  - n_groups: 分组数 (默认 5)
  - holding_period: 前向收益持有天数 (默认 5)
- **示例**：
  {"factor_code": "ts_rank(close,20)", "n_groups": 5, "holding_period": 5}
- **边界**：
  只读工具; 需要 DuckDB ohlcv; 资产数须 ≥ n_groups*2; 样本 < 20 根
  或因子计算失败的资产被跳过; 某日有效资产不足则跳过该日。
- **错误处理范式**：
  - 资产不足 n_groups*2 → error + 所需/实有数量
  - ohlcv 为空 → error, 先入库
  - 某组无观测 → 该组 mean_return 为 null (非整体失败)
  - 幂等: 只读不写
- **相关工具**：
  前置: get_market_data; 后续: factor_ic_decay / factor_turnover;
  同类: factor_cross_sectional_analysis

### factor_ic_decay

- **类别**: 因子 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  计算因子在多个前向收益周期 (默认 1,5,10,20,60 天) 的逐日截面
  Spearman IC 均值/标准差/IR, 观察预测力随周期的衰减速度,
  用于选择因子最佳持有周期。
- **参数**：
  - factor_code: 因子表达式 (必填)
  - universe: 逗号分隔代码或 all (默认 all)
  - start_date/end_date: 数据时间窗 (可选)
  - horizons: 逗号分隔的前向周期列表 (默认 1,5,10,20,60)
- **示例**：
  {"factor_code": "ts_mean(close,20)/ts_mean(close,60)-1",
  "horizons": "5,10,20"}
- **边界**：
  只读工具; 需要 DuckDB ohlcv; 因子计算成功资产须 ≥3; 单日截面
  有效资产 < 3 则跳过该日。
- **错误处理范式**：
  - 因子成功资产 < 3 → error
  - 某 horizon 无有效观测 → 该周期 ic_mean 等为 null (非整体失败)
  - ohlcv 为空 → error, 先入库
  - 幂等: 只读不写
- **相关工具**：
  前置: get_market_data; 后续: 按最佳 horizon 构建策略;
  同类: factor_cross_sectional_analysis / factor_turnover

### factor_turnover

- **类别**: 因子 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  按 rebalance_freq 天间隔采样因子值, 计算相邻采样日资产排名的
  Spearman 相关, 换手率 = 1 - 秩相关; 输出平均/中位换手与排名
  稳定度 (1 - 平均换手)。低换手因子排名稳定, 更适合实盘。
- **参数**：
  - factor_code: 因子表达式 (必填)
  - universe: 逗号分隔代码或 all (默认 all)
  - start_date/end_date: 数据时间窗 (可选)
  - rebalance_freq: 采样间隔天数 (默认 5)
- **示例**：
  {"factor_code": "ts_mean(close,20)/ts_mean(close,60)-1",
  "rebalance_freq": 10}
- **边界**：
  只读工具; 需要 DuckDB ohlcv; 因子成功资产须 ≥3; 采样期 < 2 报错;
  相邻采样日公共资产 < 3 的间隔被跳过。
- **错误处理范式**：
  - 采样期 < 2 → error "not enough rebalancing periods"
  - 无有效换手观测 → error "no valid turnover observations"
  - 因子成功资产 < 3 → error
  - 幂等: 只读不写
- **相关工具**：
  前置: get_market_data; 同类: factor_ic_decay / factor_quintile_returns

---

## 4. 行情数据

### get_market_data

- **类别**: 行情 ｜ **副作用**: 写DB, 网络
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名 + ToolContext; 副作用改 effects)
- **用途**：
  按 fallback 链获取 OHLCV 行情, persist=True (默认) 直接写入
  DuckDB price_data (回测/因子立即可用), 返回摘要+预览;
  全量数据不进 LLM prompt (context 安全)。
- **参数**：
  - codes: 资产代码列表 (必填, 如 ['600519.SH','000858.SZ'])
  - start_date/end_date: ISO 日期 (必填)
  - interval: K 线周期 (默认 '1D')
  - source: 数据源覆盖 (可选)
  - max_rows: 每代码最大行数 (默认 500)
  - persist: 是否入库 (默认 True; False 只查看)
  - strategy_name: 数据分区名 (默认 'default')
  - force_refresh: 跳过缓存强制网络取数 (默认 False)
- **示例**：
  {"codes": ["600519.SH"], "start_date": "2023-01-01", "end_date": "2023-12-31"}
- **边界**：
  写工具 (effects: db + net); 幂等 (INSERT OR REPLACE);
  纯数字代码会误判为 FRED/macro, A 股务必带后缀。
- **错误处理范式**：
  - 缺 codes/日期 → error + expected 示例
  - 日期范围非法 → error + 校验说明
  - 指定 source 不可用 → error + 可用源列表
  - 网络失败 → error (transient, 可重试)
  - persist=True 幂等, 重试安全
- **相关工具**：
  run_backtest/compute_factor/factor_*: 数据消费方

### import_data

- **类别**: 行情 ｜ **副作用**: 写DB
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名 + ToolContext)
- **用途**：
  手动/外部 OHLCV 数据导入 DuckDB。主流程是
  get_market_data(persist=True); 本工具仅用于粘贴外部数据/CSV。
- **参数**：
  - data: {asset_code: [记录列表]} (必填)
  - strategy_name: 数据分区名 (默认 'default')
- **示例**：
  {"data": {"600519.SH": [{"trade_date": "2023-12-11", "close": 1544.5}]}}
- **边界**：
  写工具 (effects: db); 支持 LLM 错误包裹 (JSON 字符串/单键 dict) 容错。
- **错误处理范式**：
  - 缺 data → error + expected 结构示例
  - 数据形状错误 → error + fix 提示用 get_market_data
  - 均可安全重试 (INSERT OR REPLACE 幂等)
- **相关工具**：
  get_market_data: 推荐主流程

### list_data_sources

- **类别**: 行情 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名)
- **用途**：
  列出全部注册数据源: 可用性/适用市场/是否需要 API key。
  取数前先查可用源, 或排障时确认数据源状态。
- **参数**：
  无
- **示例**：
  {}
- **边界**：
  只读工具; 不访问网络。
- **错误处理范式**：
  无输入参数, 极少失败; 失败均可安全重试。
- **相关工具**：
  get_market_data: 用可用源取数

### search_symbol

- **类别**: 行情 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名)
- **用途**：
  按名称或代码模糊搜索证券 (A 股主, 经 akshare spot 数据)。
- **参数**：
  - query: 查询词 (必填, 名称或代码)
  - market: 市场过滤 (默认 'a_share')
  - limit: 最大结果数 (默认 10)
- **示例**：
  {"query": "茅台"}
- **边界**：
  只读工具; 依赖 akshare 与网络; 无匹配返回空列表 (非错误)。
- **错误处理范式**：
  - 缺 query → error + expected 示例
  - akshare 未装 → fix 安装
  - 网络失败 → error + fix 换查询词/检查网络
- **相关工具**：
  get_market_data: 搜到的代码直接取数

---

## 5. 其他分析

### options_pricing

- **类别**: 分析 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  用 Black-Scholes 公式计算欧式期权理论价与 Greeks
  (delta/gamma/theta/vega/rho), 用于研究中的敏感度分析。
  仅支持欧式期权, 不处理分红与美式提前行权。
- **参数**：
  - spot/strike/rate/volatility/time_to_expiry: 标的价/行权价/
  无风险利率/波动率/剩余期限 (年), 均须为正
  - option_type: call 或 put (默认 call)
- **示例**：
  {"spot": 100.0, "strike": 105.0, "rate": 0.03, "volatility": 0.25,
  "time_to_expiry": 0.5, "option_type": "call"}
- **边界**：
  只读工具; 无需 workspace/数据库; 需 scipy; strict 工具 (schema
  由 strict 模式强制必填)。
- **错误处理范式**：
  - option_type 非 call/put → error + 枚举提示, 修正后重试
  - 任一参数非正 → error + 提示, 修正后重试
  - 幂等: 纯函数计算
- **相关工具**：
  pattern_recognition: 行情形态分析 (研究输入)

### pattern_recognition

- **类别**: 分析 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  从 DuckDB ohlcv 读取最近 N 根 K 线, 用简化启发式检测价格形态:
  均线趋势 (MA5 vs MA20)、近阻力/近支撑 (接近近期高低点 2% 内)、
  波动率挤压 (近 5 日标准差 < 近 20 日的 60%)。非严格形态识别,
  输出带置信度, 作为研究输入而非交易信号。
- **参数**：
  - asset: 限定单个资产代码 (可选; 缺省分析全部资产)
  - lookback: 分析的 K 线数量 (默认 60)
- **示例**：
  {"asset": "600519.SH", "lookback": 120}
- **边界**：
  只读工具; 需要 workspace 含 DuckDB 且 ohlcv 非空; 数据量 < 10 根
  报 insufficient data。
- **错误处理范式**：
  - 缺 workspace / 库不可用 / ohlcv 为空 → error, 先入库
  - 数据不足 (< 10 根) → error, 需 get_market_data(persist=True)
  - 幂等: 只读不写
- **相关工具**：
  前置: get_market_data / import_data; 同类: compute_factor

---

## 6. 技能

### list_skills

- **类别**: 技能 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  列出方法论技能: workspace/.skills/ 优先, 合并内置
  templates/.skills/, 返回名称/类别/一句话描述, 可按类别过滤。
  技能全文用 load_skill 按需加载, 避免大全文直接进 prompt。
- **参数**：
  - category: 按类别过滤 (可选; 缺省返回全部)
- **示例**：
  {"category": "因子研究"}
- **边界**：
  只读工具; 需要 workspace 上下文; 无技能时返回空列表 (非错误)。
- **错误处理范式**：
  - 缺 workspace 上下文 → error, 需 AgentLoop 注入
  - 扫描/加载异常 → error + 异常信息, 可重试
  - 幂等: 只读不写
- **相关工具**：
  load_skill: 加载技能全文; tool_help: 同类按需加载机制

### load_skill

- **类别**: 技能 ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  按名称加载技能的完整 markdown 全文 (含 API 契约/工作流/示例),
  供 agent 按方法论执行。workspace/.skills/ 覆盖同名内置技能。
  先 list_skills 浏览目录, 再决定加载哪个。
- **参数**：
  - name: 技能名 (必填)
- **示例**：
  {"name": "factor-research"}
- **边界**：
  只读工具; 需要 workspace 上下文; name 为空/非字符串报错。
- **错误处理范式**：
  - name 缺失/非法 → error + 提示
  - 技能不存在 → error + available 列表 (最多 20 个)
  - 内部异常 → error + 异常信息, 可重试
  - 幂等: 只读不写
- **相关工具**：
  list_skills: 目录浏览; tool_help: 同类按需加载机制

---

## 7. Web

### web_search

- **类别**: Web ｜ **副作用**: 网络
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名)
- **用途**：
  DuckDuckGo 网页搜索, 返回标题/URL/摘要。无需 API key。
- **参数**：
  - query: 搜索词 (必填)
  - max_results: 最大结果数 (默认 10)
- **示例**：
  {"query": "A-share momentum factor research"}
- **边界**：
  只读工具 (effects: net); 依赖 duckduckgo_search 包。
- **错误处理范式**：
  - 缺 query → error + expected 示例
  - 网络失败 → error (transient, 可重试)
- **相关工具**：
  read_url: 打开结果

### read_url

- **类别**: Web ｜ **副作用**: 网络
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名)
- **用途**：
  抓取网页 URL 并返回 Markdown 内容; 适合读文档/文章/论文。
- **参数**：
  - url: 要抓取的 URL (必填)
  - max_chars: 最大字符数 (默认 10000)
- **示例**：
  {"url": "https://docs.python.org/3/"}
- **边界**：
  只读工具 (effects: net); 依赖 ...web.fetch。
- **错误处理范式**：
  - 缺 url → error + expected 示例
  - 网络/解析失败 → error (transient, 可重试)
- **相关工具**：
  web_search: 找 URL

### read_document

- **类别**: Web ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 迁移 v2 (显式签名)
- **用途**：
  从 PDF 文件提取文本内容 (含页码标记)。需 PyMuPDF。
- **参数**：
  - path: PDF 文件路径 (必填, 绝对路径)
  - max_pages: 最大页数 (默认 50)
- **示例**：
  {"path": "/home/user/papers/momentum.pdf"}
- **边界**：
  只读工具; 依赖 fitz (PyMuPDF), 缺失时被排除注册。
- **错误处理范式**：
  - 缺 path → error + expected 示例
  - 文件无法解析 → error
- **相关工具**：
  无

---

## 8. Goal

> Goal 工具写 `goals.db`（effects=db）。`session_id` 由框架经 ToolContext 注入（聊天场景），否则回退 `"default"`。
> 典型生命周期：`create_goal` → `add_evidence`(可多次) → `complete_goal`；用 `get_goal_status`/`list_goals` 查看。

### create_goal

- **类别**: Goal ｜ **副作用**: 写DB
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  为当前会话创建研究目标 (goals.db), 已存在目标则被取代。
  criteria 为空时用默认标准。研究开始前用本工具确立目标,
  研究中用 add_evidence 记录证据。
- **参数**：
  - objective: 研究目标描述 (必填, 非空)
  - criteria: 完成标准列表 (可选, list[str]; 字符串/JSON/单键
  包裹均容错解析; 缺省用默认标准)
- **示例**：
  {"objective": "评估动量因子在 A 股的有效性",
  "criteria": ["完成截面 IC 分析", "完成分层回测"]}
- **边界**：
  写 goals.db (effects=db); session_id 由框架注入, 无会话回退
  default; 已存在目标被取代, 创建前可先 get_goal_status。
- **错误处理范式**：
  - objective 缺失/空 → error + expected/fix
  - 存储异常 → error + 输入回显, 验证参数后重试
  - 幂等性: 替换语义, 重复调用会覆盖旧目标
- **相关工具**：
  后续: add_evidence / get_goal_status / complete_goal

### add_evidence

- **类别**: Goal ｜ **副作用**: 写DB
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  向当前会话的 active goal 追加证据条目 (指标/观测/结论), 可关联
  criterion 推动进度百分比; 证据累积完成后用 complete_goal 收尾。
- **参数**：
  - text: 证据文本 (必填, 非空)
  - criterion_id: 关联的完成标准 id (可选)
  - source_type: 证据来源类型 (默认 evidence)
  - run_id: 关联的回测 run id (可选)
- **示例**：
  {"text": "截面 IC = 0.045 (2023-01-01 至 2023-12-31)",
  "criterion_id": "c1"}
- **边界**：
  写 goals.db (effects=db); 需要会话已有 active goal; session_id
  由框架注入, 无会话回退 default。
- **错误处理范式**：
  - text 缺失 → error + expected/fix
  - 无 active goal → error + fix (先 create_goal)
  - 存储异常 → error + 文本预览回显, 可重试
  - 幂等性: 每次追加独立证据记录
- **相关工具**：
  前置: create_goal; 后续: get_goal_status / complete_goal

### complete_goal

- **类别**: Goal ｜ **副作用**: 写DB
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  将当前会话的 active goal 标记为完成 (lite 模式), 可附 recap
  总结。必填 criterion 缺证据时会阻止完成。
- **参数**：
  - recap: 完成总结 (可选)
- **示例**：
  {"recap": "动量因子截面 IC 显著, 回测 Sharpe 1.2"}
- **边界**：
  写 goals.db (effects=db); 需要会话已有 active goal; 无目标时
  提示先 create_goal; session_id 由框架注入。
- **错误处理范式**：
  - 无 active goal → error + fix (先 create_goal)
  - 必填 criterion 缺证据 → 完成被拒 (补齐证据后重试)
  - 存储异常 → error, 检查目标是否已完成
  - 幂等性: 已完成的目标重复调用会报错
- **相关工具**：
  前置: create_goal / add_evidence; 后续: list_goals

### get_goal_status

- **类别**: Goal ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  查询当前会话 active goal 的快照: 状态/进度百分比/完成标准及
  各自状态/证据数。研究过程检查进度或决定是否 complete_goal。
- **参数**：
  (无显式业务参数; session_id 由框架注入)
- **示例**：
  {}
- **边界**：
  只读工具 (不写库); 无 active goal 时返回 {has_goal: false}
  而非错误; session_id 由框架注入。
- **错误处理范式**：
  - 数据库不可访问 → error + fix
  - 无目标 → 正常返回 has_goal=false (非错误)
  - 幂等: 只读不写
- **相关工具**：
  前置: create_goal; 后续: add_evidence / complete_goal

### list_goals

- **类别**: Goal ｜ **副作用**: 只读
- **版本**: 1.1.0
- **变更**: v1.1.0 补全说明书 (v2 范式 8 节模板)
- **用途**：
  列出 goals.db 中的研究目标摘要 (goal_id/会话/状态/进度/创建时间),
  可按状态过滤, 用于回顾历史目标或恢复研究。
- **参数**：
  - status: 过滤状态 (可选; active/complete/abandoned, 缺省全部)
  - limit: 返回条数上限 (默认 10)
- **示例**：
  {"status": "active", "limit": 20}
- **边界**：
  只读工具 (不写库); session_id 由框架注入; 未指定会话时列出
  全部会话的目标 (跨会话浏览)。
- **错误处理范式**：
  - status 非法 → error + expected 枚举提示
  - 数据库异常 → error + fix
  - 幂等: 只读不写
- **相关工具**：
  get_goal_status: 当前目标快照; create_goal: 创建目标

---

## 标准工作流

### 工作流 A：数据获取 → 回测（推荐，一步入库）

```
get_market_data(codes=['600519.SH','000858.SZ'], start_date='2023-01-01',
                end_date='2024-12-31', persist=True, strategy_name='mom')
  └─ fetch + 写入 DuckDB 一步完成，返回 summary/preview（不进 prompt）
run_backtest(strategy_name='mom')
```

> 已合并 `commit_market_data`：`get_market_data` 的 `persist=True`（默认）直接把行情写入 DuckDB，无需二次调用。

### 工作流 B：因子研究四连

```
factor_cross_sectional_analysis(factor_code='ts_mean(close,20)/ts_mean(close,60)-1', universe='all')
  └─ 截面 IC
factor_quintile_returns(factor_code=..., universe='all')
  └─ 分组单调性
factor_ic_decay(factor_code=..., horizons='1,5,10,20,60')
  └─ 衰减
factor_turnover(factor_code=..., rebalance_freq=5)
  └─ 稳定性 → 若稳定则 run_backtest
```

### 工作流 C：策略创建

```
list_files(path='strategies')          # 确认结构
write_file('strategies/<name>/strategy.py', ...)
write_file('strategies/<name>/config.yaml', ...)
run_backtest(strategy_name='<name>')   # 首次回测
```

### 工作流 D：策略评估与对比

```
list_history(strategy_name='<name>')
strategy_compare(strategy_names='a,b,c')
drawdown_analysis(strategy_name='<name>')
benchmark_comparison(strategy_name='<name>', benchmark_code='000300.SH')
```

### 工作流 E：Goal 驱动研究

```
create_goal(objective='...', criteria=['...'])
  → 研究过程中 add_evidence(text=..., criterion_id=...)
  → get_goal_status 看进度
  → complete_goal(recap='...')
```

### 工作流 F：外部资料研究

```
web_search(query='A股动量策略 实证')
read_url(url='https://...')
read_document(path='/abs/path/paper.pdf')
```

---

## 每角色工具白名单

见 `src/agentnodes/core/agent/role_factory.py::_ROLE_TOOL_WHITELIST`：

| 角色 | 白名单工具 |
|------|-----------|
| researcher | read_file, list_history, factor_analysis, web_search, read_url, get_market_data, search_symbol |
| data_quality | read_file, web_search, read_url, get_market_data, list_data_sources |
| factor_analyst | read_file, compute_factor, factor_analysis, get_market_data |
| strategist | read_file, write_file, run_backtest, git_diff, web_search, read_url, get_market_data |
| portfolio_construction | read_file, get_market_data |
| risk_controller | read_file, factor_analysis, get_market_data |
| attribution_analyst | read_file, factor_analysis |
| anti_overfit_analyst | read_file, list_history, factor_analysis |
| backtest_diagnostics | read_file, run_backtest, git_diff |
| critic | read_file, list_history |

> 注意：白名单用注册名；因子四连 / 策略对比等 Phase-4 工具不在任何角色白名单中——聊天模式（`allowed_tools=None`）才全量暴露。

---

## 已知缺陷与边界清单

1. **`data-routing.md` 引用的 7 个数据工具未实现**：`get_fund_flow` `get_dragon_tiger` `get_northbound_flow` `get_margin_trading` `get_block_trades` `get_shareholder_count` `get_lockup_expiry` `get_sector_info` `get_research_reports` `get_stock_news` `get_financial_statements` `get_options_chain` `get_macro_series` 均不存在。技能文档与实际注册不符（仅 `get_market_data` 等 5 个数据工具存在）。
2. **纯数字代码误判**：`detect_market('510300')` → `macro`（FRED 分支优先匹配数字）。规避：A 股代码带 `.SH/.SZ` 后缀。
3. **`000001.SH` 判为 index**：`is_index` 匹配优先于股票，回测以 `a_share` 股票为主要场景时注意。
4. **跨资产工具资产下限**：`factor_cross_sectional_analysis`/`factor_ic_decay`/`factor_turnover` 需 ≥3 资产；`factor_quintile_returns` 需 `n_groups*2`。
5. **`list_history` 无 strategy 参数时只扫第一个策略目录**。
6. **`drawdown_analysis`/`benchmark_comparison` 依赖权益曲线文件名约定**：`equity.csv`/`equity_curve.csv`/`portfolio.csv`/`nav.csv`。
7. **web 工具条件注册**：`web_search`/`read_document` 依赖包未装则不注册——`list_data_sources` 之外无直接前端指示。
8. **`import_data` 已降级**：主流程为 `get_market_data(persist=True)` 一步入库；`import_data` 仅用于手动/外部数据。

---

## 工具后续引导策略（next_step 取舍）

> 本节记录业界调研结论与本项目的最终决策。

### 业界调研结论

**Anthropic《Building Effective Agents》(2024-12)**：

- 工具定义即 **ACI**（Agent-Computer Interface）——应投入与 HCI 同等的精力打磨；引导内容推荐放在 **tool description**（何时用、示例、边界、与其它工具的区分），而不是返回值。
- **返回值应是干净的 ground truth 数据**；不建议夹带指令性文本。
- 流程编排由 **workflow（预定义代码路径）或 system prompt** 承担，不由工具返回值承担。
- 错误恢复给 retry hint（`fix` 字段）是普遍实践——本项目 `err_actionable` 的 `received/expected/fix` 即此范式。

**业界横向对比**：

| 系统 | 是否有"下一步"提示 | 形式 |
|------|-------------------|------|
| OpenAI Function Calling | ❌ 无 next_step 字段 | 引导在 function description + system prompt |
| Anthropic Tool Use | ❌ 无 | description 里写 "use after X"；返回值纯净 |
| LangChain / LangGraph | ❌ 无 | 流程用 agent/harness 编排 |
| GitHub Copilot 等代码 agent | ❌ 工具不返回建议 | planner 单独决策 |
| **API 分页/游标**（GitHub cursor 等） | ✅ 有 `next_cursor` | 唯一被业界认可的"返回值带下一步"范式——且**仅在状态依赖**时存在 |

### 最终决策

**所有 `next_step` 字段已移除。** 通用"下一步建议"（run_backtest→list_history、因子四连互相指向、import_data→run_backtest 等）不符合业界惯例且跨角色不可达，全部删除。

**唯一的契约式场景通过合并解决**：`get_market_data` 原本需要"先返回摘要、再 `commit_market_data` 入库"的两步契约。经评审，将该两步**合并为一步**——`get_market_data(persist=True)` 直接写入 DuckDB 并返回摘要，`commit_market_data` 工具退役。由此消除了唯一需要"返回值带下一步"的场景，与业界"返回值纯净"的 ACI 惯例对齐。

**引导归属**：
- 工具 description 写明 `何时用` / `边界` / `persist` 语义（ACI 范式）。
- 工作流（数据流、因子研究顺序）由 `chat.md` / `SYSTEM_PROMPT_HEADER` / skill 文档承担。
- 返回值保持纯净数据（summary + preview），不含指令性文本。
