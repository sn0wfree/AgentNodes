# P6 被动学习 — 立项文档

> 状态：**已立项（规划中，未实施）**。范式 v2 P1-P5 已收尾，本阶段补齐范式"被动学习"维度的落地。
> 上游文档：docs/guides/agent-tools-reference.md（范式 v2 设计定稿、组合工具细节、契约测试）。
> 一句话目标：**agent 正常使用即沉淀** —— 从真实使用轨迹中挖掘可复用的工具组合，经评估与人工确认后写入组合库，形成自增长的工具资产。

---

## 1. 背景与定位

范式 v2 九维度中的"被动学习"维度（docs/guides/agent-tools-reference.md）：

> 记录被动（event_log + trace.jsonl 双源，agent 正常使用即落盘）+ 扫描主动（定期挖掘）；双粒度挖掘（同 turn 合作性共现 / 跨 turn 流程性序列），沉淀标准不同；后置实现（P6）

P1-P5 已交付的框架为 P6 提供了现成底座：

| 组件 | 位置 | 现状 |
|---|---|---|
| 组合库加载器 | `core/agent/combo.py` `load_combo_tools` | 就绪（扫描 `workspace/tools/combo/*.yml`，非法配置跳过） |
| 组合执行器 | `CompositeTool` | 就绪（参数映射、错误定位、effects 并集、动态说明书） |
| 契约测试 | `tests/test_tool_contract.py`（20 例）+ `tests/test_combo_tools.py`（18 例） | 就绪，可守护新沉淀组合 |
| 事件总线 | `EventStore`（SQLite event_log）+ EventType 注册表 | **产线已启用**，数据在持续积累 |
| trace 写入器 | `core/agent/trace.py` `TraceWriter` | **产线未接线**（`trace_dir` 无人传），需 P6 补 |

**结论**：P6 的主体工作 = 挖掘器 + 评估器 + 沉淀链路 + trace 接线，框架消费端（组合库）已就绪。

## 2. 数据源

### 2.1 event_log（主源，产线已启用）

- 存储：SQLite `event_log(id, aggregate_id=session_id, seq, type, data_json, time_created)`，同库还有 `sessions/messages/message_parts/attempts`
- 与 P6 最相关的事件类型（`api/session/event_v2.py:50-106` EventType 注册表）：
  - `tool_call`：`{tool, name, id, arguments(JSON 全文), call_id, iter}` —— **完整入参**
  - `tool_result`：`{tool, id, call_id, status(done|error), ok, result(前200字符), preview, elapsed_ms}` —— 入参完整、结果预览
  - `tool_progress` / `tool_heartbeat`：耗时与进度
  - `iter_start` / `iter_end`：迭代边界（同 turn 判定的依据）
  - `assistant_message` / `message_received`：消息边界（跨 turn 判定的依据）
- 优势：产线全量、批量 SQL 可查、与 session/message 关联完整
- 局限：`tool_result` 只存 200 字符预览；依赖产线持续运行才有数据

### 2.2 trace.jsonl（辅源，需接线）

- 现状：`TraceWriter` 完整（`trace.py:58-157`），事件级粒度（loop_start/iter_start/llm_response/tool_result/tool_error/loop_end/loop_final 等），append+flush 崩溃安全，大字段 offload 到 sidecar（阈值 50KB）
- 缺口：`AgentLoop` 构造链（`service.py:880 build_chat_agent_loop` → `chat_loop.py:110`、`role_factory.py:112`）均未传 `trace_dir`，目前只有测试启用
- P6 接线方案：`service.py` 创建 loop 时传入 `trace_dir`（建议 `workspace/logs/trace/<session_id>/`，与 `TraceWriter.find_trace_dir`（trace.py:254-284）定位能力衔接）
- 补充价值：包含 `llm_response`（finish_reason/tool_call_count）与错误分类，可支撑"agent 拟用但失败"的挖掘（事件源缺失的信号）

### 2.3 数据源策略

| 维度 | event_log（主） | trace.jsonl（辅） |
|---|---|---|
| 覆盖 | 产线全量 | 接线后全量（含本地运行） |
| 字段 | 完整 arguments | result 全量可 offload |
| 用途 | 同 turn 共现挖掘（主）、序列挖掘 | 交叉验证、失败分析、离线回放 |

挖掘器读 event_log 为主；trace 用于验证候选（同一组合在 trace 中的 success/fail 比例）与离线补数据。

## 3. 挖掘器设计

### 3.1 双粒度

**A. 同 turn 合作性共现（同 iter 内的相邻 tool_call 对）**

- 取每个 `iter_start`/`iter_end` 之间的 `tool_call` 序列（按 seq 排序）
- 窗口内相邻对（可选窗口 k=2-3）计数 `(toolA → toolB)` 出现频次
- 示例：同一迭代内 `read_file → compute_factor → run_backtest` 连续出现
- 产出：共现对/共现三元组 + 频次 + 成功率（`tool_result.status == done` 占比）

**B. 跨 turn 流程性序列（跨 iter / 跨消息的稳定序列）**

- 以 `message_received` 为边界切用户任务；跨迭代聚合同一任务内的调用序列
- 对序列做规范化（去重相邻重复、参数脱敏——只保留类型与"是否是此前输出"的引用关系）
- 产出：稳定序列模式 + 支持的任务数 + 平均步数

### 3.2 候选生成流水线

```
event_log SQL 聚合 → 邻接对计数（同 iter）
                   → 任务级序列挖掘（跨 iter）
        → 候选规则初筛（§4.1）→ 参数引用识别（输出→下一步输入 的 ref 链）
        → 生成候选 YAML（combo.py 配置格式）→ 评估器
```

- 参数引用识别是候选可执行性的关键：`toolB.arguments` 中某字段的值等于 `toolA.result` 预览片段（或 toolA 的典型输出字段名/数据类型）→ 标记为 `step1.result.<path>` 引用候选；无法识别引用关系的组合不产出（宁缺毋滥）
- 去重：与既有组合库（含手写工具名）做名称/步骤序列相似度去重，避免重复沉淀

### 3.3 挖掘器接口

```python
# 新模块 src/agentnodes/core/learning/miner.py
MinerResult: {candidate_id, pattern, freq, success_rate, sample_ids: [...], params_map_draft}
def mine_event_log(db_path: str, window_days: int, min_freq: int) -> list[MinerResult]
def mine_trace_dir(trace_dir: str) -> list[MinerResult]
def generate_combo_config(candidate: MinerResult) -> dict   # 生成 YAML 候选
```

- 阈值（可配置）：同 turn 共现 `min_freq` 默认 ≥3 且成功率 ≥0.8；跨 turn 序列要求支持 ≥2 个不同任务（跨 session 验证，避免单任务过拟合）

## 4. 评估器设计

### 4.1 规则初筛（自动，硬规则 + 评分）

硬性拒绝（任一命中即淘汰）：
- 组合含黑名单工具（如 `tool_help`、带非只读 effects 且合并有风险的写工具——写工具组合默认不自动产出，除非两者 effects 类型一致且已具稳定历史）
- 参数引用链不完整（无法映射到 `input.*` 或 `stepN.result.*`）
- 组合总步数 > 5（超过组合工具设计的"线性短流程"边界）
- 序列含 `error` 状态占比 > 20%（组合沉淀的是成功路径）
- 与既有组合库/手写工具重复

评分（加权，产出排序）：
- 频次、成功率、步数增益（vs 当前等效手写调用次数）、跨任务泛化度

### 4.2 提案 + 人工确认

- 产出 `propose.md`（或 JSON）：候选 YAML 预览 + 依据（样本 session/turn、频次统计、样例参数）
- 人工确认通道：CLI 命令（暂定 `python -m agentnodes.learning <confirm|reject|propose>`，读 `workspace/learning/proposals/`）；确认后写入 `workspace/tools/combo/<name>.yml`，注册即生效（`build_default_registry` 自动加载）
- 拒绝可附原因（进入反馈统计，后续规则初筛可学习硬规则配置）

### 4.3 回馈统计

- 组合库工具被真实调用的次数/成功率/被框架修正率记录在案（复用 event_log：`tool_call` 中 `tool` 名即组合名）
- 定期回算：高频+高成功率 → "提升"候选（人工评估后固化为手写工具进显式清单）；长期零使用 → 退役标记

## 5. 沉淀链路

```
event_log/trace（记录被动）
   → 挖掘器（扫描主动：定期 cron 或按需 CLI）
   → 规则初筛 → proposals/（提案）
   → 人工确认（CLI）
   → workspace/tools/combo/<name>.yml
   → build_default_registry 自动注册（P4 已就绪）
   → 使用统计回馈（评估器 4.3）
   → 高频者提升为手写工具 / 低频者退役
```

## 6. 里程碑与验收

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| M1 trace 接线 | `service.py`/`chat_loop.py`/`role_factory.py` 传 `trace_dir`；trace 目录布局与轮转 | 产线一次会话产生完整 trace.jsonl；已有测试不回归 |
| M2 挖掘器 | `miner.py`：event_log 同 turn 共现 + 跨 turn 序列 + 候选 YAML 生成 | 单元测试（构造 20+ 条合成事件数据）：频次/成功率统计正确、引用识别命中构造样本 |
| M3 评估器 | 规则初筛 + 提案输出 + CLI 确认命令 | 硬规则用例测试（黑名单/引用缺失/超步数/重复）；CLI 确认→写入 combo 目录→注册生效 |
| M4 回馈统计 | 组合使用统计 + 提升/退役标记报告 | 统计与 event_log 对账；退役逻辑用例 |

**最终验收**：一条真实会话轨迹 → 挖掘出 ≥1 个可执行组合候选 → 人工确认 → 组合库注册 → 新会话中该组合被 agent 调用成功。

## 7. 风险与开放问题

1. **数据量**：产线 event_log 数据量需摸底；若过小（刚启用），M2 用合成数据先行，真实数据边积累边验证
2. **参数引用识别精度**：`tool_result` 只有 200 字符预览，跨工具 ref 链可能断——兜底：优先挖掘 `input.*` 型组合（参数全部来自组合输入，无需跨步骤结果引用）；复杂 ref 依赖 trace 接线后的全量 result（`write_tool_result` offload）
3. **写工具组合的默认策略**：初版只对 effects 为只读/DB-同源的工具产出组合候选；含多个异质副作用（写文件+写 DB）的组合仅提案、默认人工拦
4. **性能**：挖掘为离线批处理（CLI/定时），不进在线路径；SQL 按时间窗 + session 过滤
5. **说明书**：自动生成的组合说明书依赖子工具说明书质量（P5 已部分保障：docstring 首行 = brief 同源）；goals/web/shell 简版说明书不阻塞（契约只查首行）

## 8. 涉及文件（预估）

| 文件 | 动作 |
|---|---|
| `src/agentnodes/core/learning/miner.py` | 新增：挖掘器 |
| `src/agentnodes/core/learning/evaluator.py` | 新增：规则初筛 + 评分 + 提案 |
| `src/agentnodes/core/learning/cli.py` | 新增：propose/confirm/reject CLI 入口 |
| `src/agentnodes/core/agent/trace.py` | 读取（`read`/`find_trace_dir` 已存在，可能小改） |
| `src/agentnodes/api/session/service.py` / `chat_loop.py` / `role_factory.py` | 改：`trace_dir` 接线 |
| `tests/test_learning_miner.py` / `test_learning_evaluator.py` | 新增：合成数据用例 |
| `docs/guides/agent-tools-reference.md` | 改：P6 状态 → 实施中/已完成 |
| `workspace/tools/combo/` | 运行时：沉淀产物落点 |
