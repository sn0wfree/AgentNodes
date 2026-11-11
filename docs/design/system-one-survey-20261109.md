# System One 决策模型调研记录（Kev / Jev）

> 触发：用户提供微信文章
> <https://mp.weixin.qq.com/s/vgAAvJdulV6gJyiAi5PHQw>
> （《轻量级决策模型重塑 Agent 架构：开源 Kev 落地，Jev 范式走向自托管》），
> 要求「在本 agent 项目中加入这个」。
> 性质：**调研记录（事实层）**。不含方案判断——方案见
> `system-one-decision-layer-design-20261109.md`。
> 采集方式：微信直链对默认 UA 返回「环境异常」验证页；带浏览器 UA 抓取
> HTML 后抽取 `#js_content` 正文。Kev 事实以
> <https://github.com/jaredpalmer/kev> 的 README 为准。

---

## §0 一句话

Kev 是一族**不生成文本、单次前向直接输出概率分布**的决策模型
（`noul` / `choice` / `score` 三原语），用 LoRA adapter + 指针头挂在 Qwen
底座上，Apache-2.0，可本地训练与自托管。它解决的不是「推理不够强」，
而是「高频轻量判断不该走自回归解码」。

---

## §1 文章讲了什么（含数据勘误）

### 1.1 核心主张

| 概念 | 内容 |
|---|---|
| 定位 | **System One**（快思考）：做选择题、打分、布尔判断 |
| 对照 | System Two（LLM）：深度推理、长文本、代码生成 |
| 典型槽位 | 状态判断、工具路由、任务终止校验、风险识别、意图分类 |
| 原语 | `noul`（布尔，0~1 概率）/ `choice`（≤255 选项）/ `score`（有序刻度） |
| 关键收益 | 无解码步骤 → 低延迟；只付输入 token → 低成本；输出被约束在定义空间 → **格式免疫幻觉** |
| 明确边界 | 格式合规 ≠ 判断正确；域内判断仍可能错，需置信度阈值 + 人工兜底；依赖高质量标注 |

### 1.2 ⚠️ 文章数据与仓库 README 不一致（以 README 为准）

| 项 | 文章 | Kev README（实测） |
|---|---|---|
| 参数量 | 0.6B / 4B / 8B | **0.8B / 4B / 9B / 27B**（Kev 1.0 四档同版） |
| 准确率 | Kev-8B 79.6%，Jev 85.7%，差 6 点 | 新来源 dev/test：Kev-4B `0.817/0.838`、Kev-9B `0.820/0.852`、Kev-27B `0.851/0.889`、Jev `0.857` |
| 延迟 | Kev-4B / 32GB Mac / bf16 / 5 问题 ≈ 300ms | H100 6 问题 **18.1ms**（模型时间）、L40S 41.5ms、Apple M5 5 问题 721ms |
| 训练成本 | 4B 单卡 H100 40 分钟 / 8B 83 分钟 | 4B 单 H100 约 1 小时（Modal）；0.8B 约 20 分钟 |
| 上下文 | 未提 | 0.8B/4B/9B **validated 8,192**；27B 65,536；server 上限 65,536，超出 **422 拒绝**（不静默截断） |
| 成本 | 最高降 444 倍 | README 未给该数字（属 Jev 商用侧口径），不可直接引用 |

**结论**：文章是二手转述且有陈旧/混淆。任何对外表述、容量规划、
SLA 承诺都必须回到 README 与 model card，不能用文章数字。

### 1.3 架构事实（README「How It Works」）

```
<state> …state…
<q> instructions <opt> option 1 </opt> … <decide>
<q> instructions <opt> option 1 </opt> … <decide>
```

- 每个 checkpoint = **rank-16 LoRA adapter + 小型指针头**，底座冻结
  （27B 例外：全参微调，51GB bf16）。
- 指针头拿每个选项 `</opt>` 的隐状态与问题 `<decide>` 的隐状态打分，
  softmax 成概率。`<decide>` 在最后，因此能看见完整选项表。
- 问题之间**互相隔离**（注意力掩码 / 独立行 + 前缀缓存复用 state）。
- 每个 checkpoint 附带**拟合好的 temperature**，概率默认已校准。
- `confidence` 是分布形状的函数（choice 用 `(p_max−1/K)/(1−1/K)`；
  score 用 `max(0, 1−E|level−mode|/D)`），**不是实测准确率**。

---

## §2 接口事实（集成成本的真正来源）

| 项 | 事实 |
|---|---|
| 端点 | `POST /v1/systemone`，另有 `/v1/models`、`/v1/systemone/permute`、`/v1/systemone/separate` |
| 请求 | `{state, model, questions: {<id>: {type, instructions, criteria}}}` |
| 响应 | `{answers: {<id>: {...}}, usage: {input_tokens, output_tokens}, latency_ms}` |
| 批量语义 | **一次请求可带任意多问题**，共享同一 state，问题间隔离；服务端按 16,384 token 行预算分批前向 |
| **协议** | **不是 OpenAI 兼容**——没有 `/v1/chat/completions`。TypeSafe SDK 可直接指向它，但那是因为 SDK 说 System One 协议，不是 OpenAI 协议 |
| 鉴权 | 默认开放；设 `KEV_API_KEY` 后要求 `Authorization: Bearer` |
| 绑定 | 默认 `127.0.0.1`，`--host 0.0.0.0` 才对外 |
| 部署 | `uv sync --extra serve` + `python -m kev.serve --run jaredpalmer/kev-4b --port 8009`；CUDA/ROCm/MLX |
| 安装陷阱 | PyPI 上的 `kev` 是**另一个包**（2016 年的 K.E.V. ORM）。本项目未发布 PyPI，只能源码安装 |

**对本项目最硬的一条**：`foundation/llm/` 的全部 provider 适配器
（`provider/__init__.py:32` 的 7 个）都是「OpenAI payload 塑形器」，
`model_hub.py:54` 对未注册 provider **显式拒绝而非降级**。因此 Kev
**不能**作为 `provider` 值塞进现有 `llm.json`，也**不能**复用
`OpenAICompatClient`（`openai_client.py:257`）。它必须是一条独立客户端家族。

---

## §3 本项目决策点普查（实测）

### 3.1 Choice / 分类 —— 真实 LLM 决策

| 位置 | 判定内容 | 调用形态 | 结果解析 | 实际触达面 |
|---|---|---|---|---|
| `kernel/workflow/node_types.py:432` `_dispatch_evaluator` | `verdict ∈ {continue, replan, stop}` + reason + findings | `run_agent_via_llm(role="evaluator", max_iterations=6)` → **完整 AgentLoop（带工具）** | `_parse_decision`（:460）→ `get_parser().parse(answer)`；不可解析 → 兜底 `continue`（:449） | **窄**：仅当 `WorkflowDefinition` 声明 `"type":"evaluator"` 节点。内置模板只有 `plan_execute_auto.json` / `plan_execute_approval.json` 两个，且**只经 API 路由触达**（`routers/workflow.py:617`） |
| `kernel/workflow/graph_compiler.py:392` `_last_verdict` | 消费上条 verdict 驱动重规划环路 | — | `artifacts["decision"]["verdict"]` | 同上下；`alpha_research.json` 有 evaluator 但无 planner → verdict 被忽略（`graph_compiler.py:156-160`） |
| `kernel/workflow/node_types.py:313` `_dispatch_planner` | 3–8 步计划 | `run_agent_via_llm(role="planner")` | `_parse_plan` → 同一降级解析器；失败 → 静态兜底计划 | 同上两个内置模板 |
| `templates/.prompts/critic.md:125` | `verdict ∈ {keep, discard}`、`risk_rating`、`direction` | 标准 role 运行 | **代码中无消费者** | **零**：37 个 `apps/swarm_presets/*.yaml` 中 `critic` **零出现**；内置 workflow 也零出现。只有 prompt 文件与 `quant.py:183` 的插件声明 |
| `templates/.prompts/study_reviewer.md` | `deviation`、`info_gap`、`topics[]` | 标准 role 运行 | **代码中无消费者** | **零**：37 个 preset 零出现；grep 全仓无命中 |
| `templates/.prompts/risk_controller.md:60-64` | 实际产出 `risk_passed: bool` + `risk_rating ∈ {Green,Yellow,Red}` + 风险指标 | 标准 role 运行（13/37 preset 使用） | ⚠️ **校验器校验的是另一个字段**：`apps/agent_plugins/quant_validators.py:64-67` 检查 `verdict ∈ {pass,fail,warn}`，而 prompt 从不产出 `verdict` → 该校验恒为 no-op | **广但无反馈**：13/37 preset 含该角色，但**没有任何 preset 配置 `condition`**（`findstr /c:"condition"` 零命中）→ 分支 DSL 未接线，verdict 不驱动任何分支 |
| `kernel/evolution/holdout.py:216` `classify_intent` | 5 桶意图分类 | **关键词表**（确定性） | 直接返回 | 离线，每次 `signature_for_intent` 调用 |

### 3.1.1 这张表最重要的一列是「实际触达面」

初版把「代码里存在」当成「运行时在用」，结论因此偏乐观。逐项核实后：

- **critic / study_reviewer 是纯声明**——prompt 文件存在、插件声明存在，
  但**从未被任何 workflow/preset 编排**。它们的 verdict 字段没有读者，
  因为根本没有人跑它们。
- **evaluator 是窄路径**——只在两个内置 plan-execute 模板里，且这些模板
  只经 API 路由加载。不是 chat 热路径，也不是 goal 路径。
- **risk_controller 是「广但无反馈」**——13/37 preset 使用它，产出被校验器
  （错误地）忽略，且没有任何 preset 用 `condition` 消费它。所以它的
  verdict **不驱动任何行为**，纯记录。

**推论**：本方案里「把角色输出原语化」的真实价值不是「让判断更快」，
而是**让这些判断第一次真正可被消费**。这比性能论据更硬，也更值得做。


### 3.2 Boolean —— **全部确定性，无一处用 LLM**

| 位置 | 判定 | 机制 |
|---|---|---|
| `kernel/agent/deciders.py:84-281` | 停止/继续/熔断/no-progress | 包装既有 step，读 `ctx` 标志 |
| `kernel/agent/strategy/steps/continuation.py:13` | 是否续跑 | 当前恒返回 `False` |
| `kernel/agent/guardrails.py:99,104` | 注入/风险检测 | **正则表**（`GUARDRAIL_PATTERNS:20`） |
| `kernel/permission/evaluator.py:154` | allow/ask/deny | 规则 + fnmatch + hard-deny |
| `kernel/workflow/orchestrate_guard.py:81` `looks_like_question` | 「模型是不是反问用户」 | 正则尾匹配；消费者 `interfaces/api/session/service.py:969` 据此重跑一轮 LLM |
| `apps/goal/context.py:83` `goal_needs_continuation` | goal 是否还需推进 | 计数未覆盖判据；消费者 `kernel/agent/context_injector.py:234` |
| `kernel/agent/runtime/tool_exec.py:71` | 工具是否失败 | 字符串前缀 |

### 3.3 Score —— 全部确定性或「LLM 自报后只校验范围」

| 位置 | 分值 | 机制 |
|---|---|---|
| `kernel/agent/validators/claim_validator.py:103` | `confidence = verified/total` | 数字邻近匹配，无 LLM |
| `kernel/evolution/validation_gate.py:342` | accept/reject/escalate | 统计 holdout / 成本门 |
| `evals/scorecard.py:107` | 0..1 任务分 | 确定性比对 |
| LLM 自报（`weighted_score` / `confidence` / `risk_rating`） | — | 只做范围/形状校验，**从不被代码重算** |

### 3.4 唯一的非决策型辅助 LLM 调用

`kernel/agent/compact.py:533` `_llm_summarize_v2` —— 压缩摘要，自由文本，
无 JSON 约束。这是 System Two 的本职，不在本方案范围。

---

## §4 现状的六个结构性缺口（这是「为什么现在接不进去」）

| # | 缺口 | 证据 |
|---|---|---|
| 1 | **进程内只装得下一个模型** | `LLMConfig` 是 `frozen=True` 单值 dataclass（`config.py:109`），只有 `model`（:120）；`profiles`（`config.py:418`）是**进程级单选**，不是并发多角色 |
| 2 | **结构化输出能力声明是死字段** | `model_catalog.py:61` `supports_structured_output` 全仓**零消费者**（仅本文件赋值） |
| 3 | **靠 4 层降级解析器兜底自由文本** | `structured_output.py:38`（strict → repair → regex → None）；`node_types.py:461` 调用时**不传 schema**，第 3 层实际失效 |
| 4 | **单个三选一判断要起完整 AgentLoop** | `node_types.py:436` 为得出 `verdict` 起 ReAct（`max_iterations=6` + 工具）；同期 `exec_registry.py:33` 的 `register_evaluator` **全仓无调用方**——说明「轻量 evaluator」这条路早被设计过但没接上 |
| 5 | **prompt 声明与校验器 schema 漂移** | `risk_controller.md:60-64` 产出 `risk_passed: bool`，而 `quant_validators.py:64-67` 校验 `verdict ∈ {pass,fail,warn}` → 恒 no-op；`critic` / `study_reviewer` 的 verdict 字段**全仓无读者** |
| 6 | **拿不到概率** | `logprobs` 与 `response_format` 在 `src/` 中**均零命中**（`findstr /i` 全仓）。现有 LLM 路径只有文本，**没有 token 级概率**——因此「用 logprobs 模拟 Kev」这条路在现有客户端上不成立，除非改 `openai_client.py` 的 payload 与解析 |

补充：`interfaces/api/session/service.py:897` 的 `cfg.model = model` 在给
frozen dataclass 赋值，**会抛 `FrozenInstanceError`**。任何 per-call
模型选择一落地就会踩到它，是前置修复项。

### 4.1 一条更硬的缺口：没有「影子 / A-B」基础设施

调研了全部既有机制，结论是**影子期必须自建**：

| 需要 | 现状 |
|---|---|
| 把路径开/关 | 有：进程级 env（`foundation/settings.py:183-186`）、内存 flag（`capacity.py:138`） |
| 同一输入跑两条路径 | **仅测试里**（`tests/test_engine_parity.py` 顺序跑两个引擎比 dict；`tests/test_skeleton_equivalence.py` 子进程比 JSON） |
| 可复用的比较器 | **无**——都是内联 assert |
| 记录分歧 | **无** |
| 可回放的决策日志 | 有：`event_log` append-only + `replay()`（`event_store.py:399`） |
| 一等的决策事件 | **无**——evaluator verdict 只落在 `node_outputs.artifacts`（`workflow/store.py:272-288`） |
| 标注数据集 | 有：golden JSONL + held-in/out 划分（`evals/golden_sets.py:90`） |
| 「一致率 / 自动化率 / 错误预算」 | **零**——`rg "agreement|automation_rate|error_budget"` 在 `src/` 无命中 |
| 按百分比放量 | **无**（唯一的确定性分桶是 golden set 的 `sha256 % 100`，`golden_sets.py:90-101`） |
| kill switch | 有但**只在 RSI 生效**（`halt.py:232`；`rsi_cli.py:340`），kernel/AgentLoop/workflow 均不检查 |

**含义**：批次 B（影子）不是「加个开关」，而是要新建
「决策事件 schema + 双跑包装 + 比较器 + 落盘」四件套。这是本方案里
**最容易被低估的工作量**，也是唯一能让后续批次有据可依的东西。


---

## §5 实测：8k 到底是不是墙（初版判断错误，此处更正）

初版写「AgentNodes 真实上下文远超 8,192，必须配套投影器」。**实测后这个
判断只在特定槽位成立。**

### 5.1 方法

用仓库自己的 `estimate_tokens_text`（`foundation/utils/token_utils.py:47`，
3 chars/token）按各槽位的**真实投影逻辑**复算。evaluator 的输入按
`_upstream_summary`（`node_types.py:298`）+ `_summary_truncate`
（`:247`，默认 `max_chars=300`，artifacts `[:200]`）精确复刻。

### 5.2 结果

| 槽位 | 真实输入 | token | 8k 够吗 |
|---|---|---|---|
| **evaluator**（默认截断，20 上游节点） | 11,319 字符 | **3,772** | ✅ 宽裕 |
| evaluator（默认截断，5 节点） | 3,414 字符 | **1,137** | ✅ 宽裕 |
| evaluator（无截断，8 节点 × 2k） | 32,995 字符 | 10,998 | ❌ 但此配置不存在 |
| **`looks_like_question`** | 最终答复 ≤20,000 字符 | ≤6,666 | ✅ 够 |
| `looks_like_question`（40k 字符答复） | 40,000 字符 | 13,333 | ❌ 罕见 |
| **goal 判据批量**（40 判据 + 40 证据） | 14,298 字符 | **4,766** | ✅ 宽裕 |
| **角色输出原语化**（16k 字符输出） | 16,000 字符 | 5,333 | ✅ 够 |
| **guardrails 扫工具结果** | 30,000 字符（`_TOOL_RESULT_HISTORY_MAX`，`loop_tools.py:21`） | **10,000** | ❌ **真的会超** |

### 5.3 更正后的结论

- **evaluator 不撞墙**。`_summary_truncate` 默认 300 字符/节点已经把输入
  压到 1–4k token。初版「P1 会 422」的担心不成立，**投影器对 P1 不是必需项**。
- **真正的墙只有一处**：`guardrails` 扫工具结果，上限 30,000 字符 = 10,000 token。
  但 guardrails 本来就不该整段喂给概率模型（C3：只升级不放行），
  可以只扫**首尾窗口**。
- **投影器从「必需」降级为「防御性」**。仍需保留（防 40k 答复、超长证据），
  但不应作为批次 B 的前置依赖——这大幅降低首批工作量。

---

## §6 实测：成本论据不成立（最重要的更正）

文章的核心卖点是「降本 444 倍」。**在本项目的实际价格结构下，这个论据
不成立。**

### 6.1 计算

- 现价（bundled catalog，`data/providers/deepseek/deepseek-chat.toml`）：
  输入 **$0.14/M**、输出 **$0.28/M** token。
- 单次 evaluator 决策（3 轮 ReAct，prompt 累积 ~6,411 tok，输出 900 tok）：
  **≈ $0.00115**。
- Kev 自托管：只付电费/GPU 摊销，输出 token 不计费。

### 6.2 盈亏平衡

| 专用 GPU 月成本 | 盈亏平衡点 |
|---|---|
| $400/月 | **11,599 次决策/天** |
| $800/月 | 23,198 次决策/天 |
| $2,000/月 | 57,994 次决策/天 |
| （若 loop 只跑 1 轮） | 54,829 次决策/天 |

### 6.3 为什么这个数字对本项目是致命的

结合 §3.1.1 的触达面：

- evaluator 只在 **2 个内置 plan-execute 模板**、且**只经 API 路由**触达；
- critic / study_reviewer **零编排**；
- goal 判据覆盖走的是**确定性计数**（`context.py:83`），不是 LLM。

**真实决策量级远低于 1 万次/天。** 因此：

> **「省 LLM 调用费」在本项目不是购买理由。**
> 若以成本为主要论据立项，会在评审阶段就被这个算术否掉。

### 6.4 那还剩什么理由

按强度排序（这些才是站得住的）：

1. **概率 + 可校准阈值**——现有路径**拿不到概率**（`logprobs` 零实现，§4 #6）。
   这是能力缺口，不是成本优化。有了概率才能做「置信度不够就转人工/转大模型」，
   这是现在完全做不到的事。
2. **格式免疫**——消灭 `structured_output.py` 那 4 层降级的存在理由。
3. **延迟**——单次前向 vs 3 轮 ReAct，量级差异真实存在（但本项目当前
   决策不在用户等待路径上，收益有限）。
4. **让死字段活过来**——critic / risk_controller / study_reviewer 的
   verdict 现在**没有任何消费者**。原语化是让它们第一次可被消费的最短路径。

**建议立项理由改写为「补齐概率化决策能力」，而不是「降本」。**

---

## §7 明确不适用的槽位（先排除，避免方案膨胀）

| 槽位 | 为什么不接 |
|---|---|
| **工具路由** | 默认工具集 18 个（`build_default_registry()` 实测），技术上 ≤255 可做 `choice`，但原生 function-calling 带完整 JSON Schema 语义且已被验证；换掉是净亏 |
| **compaction 摘要 / planner 生成 / 代码生成** | 生成任务，System Two 本职 |
| **permission 的 allow 决策** | 安全判定不能由概率模型放行（见 `framework-and-principles-20261105.md` 理念七） |
| **进程内加载 torch/MLX 权重** | 会把 L4 变成 GPU 依赖，`pip install agentnodes` 直接崩 |
| **`provider: "kev"` 写进 llm.json** | `model_hub.py:54` 会拒绝；语义上也是错配（非 OpenAI 协议） |
| **critic / study_reviewer 角色** | 不是「不接」，是**先确认要不要这个角色**——37 个 preset 零编排，接进去也没有输入 |

---

## §8 结论（留给设计文档的四个输入）

1. **缺的不是模型，是「模型分层」这根轴**——当前架构物理上装不下
   「Tier 1 决策模型 + Tier 2 大模型」同时在线。
2. **端口必须 batch-first**——Kev 的价值来自「一次前向、N 个隔离问题、
   state 前缀缓存复用」。设计成 `decide_one()` 会把收益吃干净。
3. **8k 不是普遍障碍**（§5 实测）——evaluator 实际只有 1–4k token；
   唯一真超的是 guardrails 扫 30k 工具结果。投影器降级为防御性措施。
4. **成本不是理由**（§6 实测）——单次决策仅省 $0.00115，盈亏平衡需
   1.16 万次/天，而真实触达面远低于此。立项理由应改为
   **「补齐概率化决策能力」**。

