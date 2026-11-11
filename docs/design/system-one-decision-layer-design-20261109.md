# System One 决策层接入 — 方案设计与评审稿

> **性质**：**讨论稿**，含待拍板的开放问题与备选方案。非实施规格。
> **上位调研**：`system-one-survey-20261109.md`（事实层：Kev 接口、
> 本项目决策点普查、**触达面实测、token 实测、成本盈亏平衡实测**）。
> **约束来源**：`framework-and-principles-20261105.md`（理念四/五/七）、
> `layered-skeleton-design-20261103.md`（L1–L4 归属与方向规则）。
> **关联**：`unified-loop-adoption-eval-20261107.md`（decider 协议来源）、
> `context-hub-discussion.md`（投影器可能复用其仲裁阶段）。

> ⚠️ **v2 修订（2026-11-10）**：初版有三处判断被实测推翻，已在本文更正：
> ① 成本论据不成立（§0.1）；② 8k 不是普遍障碍（§5.1）；
> ③ 多个「槽位」实际零触达（§4 重排）。**立项理由已改写。**

---

## §0 一句话结论

**不要把 Kev 当「更便宜的 LLM」接，把它当「补齐概率化决策能力」的层接。**

先补「模型分层」这根轴（`roles` 配置 + 独立客户端家族 + batch-first 端口），
再在**真实有触达**的槽位做影子校准，用「错误预算下可自动化比例」证明自己，
最后才推开。任何跳过「影子 + 校准曲线」直接采纳的做法都应否决。

### 0.1 立项理由的更正（重要）

| 初版理由 | 实测结论 | 处置 |
|---|---|---|
| 降本（文章称 444×） | 单次决策仅省 **$0.00115**；专用 GPU 盈亏平衡需 **11,599 次/天**；真实触达面远低于此 | ❌ **删除该论据** |
| 8k 上下文是硬墙 | evaluator 实际输入仅 **1–4k token**（`_summary_truncate` 默认 300 字符/节点）；唯一真超的是 guardrails 扫 30k 工具结果 | ⚠️ 降级为防御性措施 |
| critic / study_reviewer 是热点 | **37 个 preset 零编排**；只有 prompt 文件与插件声明 | ❌ 移出范围 |
| 低延迟 | 真实（单次前向 vs 3 轮 ReAct），但决策不在用户等待路径上 | ⚠️ 次要收益 |

**站得住的理由只有一条，但足够硬**：现有 LLM 路径**拿不到概率**
（`logprobs` / `response_format` 在 `src/` 全仓零实现）。没有概率就无法做
「置信度不足 → 转人工 / 转大模型」，这是**能力缺口，不是成本优化**。

附带价值：让 `critic` / `risk_controller` / `study_reviewer` 那些
**声明了却无人消费**的 verdict 第一次真正可被消费（调研 §3.1.1）。


---

## §1 三层决策栈

```
Tier 0  确定性规则      现有 deciders / budget / regex / 计数
                        零成本、可审计、可复现、无外部依赖
   ↓  仅在 Tier 0 无法判定时下沉
Tier 1  System One      noul / choice / score，批量一次前向
                        ~20–50ms、概率、格式免疫、可校准阈值
   ↓  仅在 confidence 不达标、或需要读证据/生成时上浮
Tier 2  System Two      现有 AgentLoop / 工具调用 / 长文本
                        深度推理，唯一能「读」和「写」的层
```

### 1.1 三条不可违反的约束

| # | 约束 | 依据 |
|---|---|---|
| C1 | **Tier 1 永不成为不可逆动作的唯一门**。goal 完成、permission allow、文件写、发布，必须保留 Tier 0 规则或人工兜底 | 理念四：评估器在循环外、终止层级在进程外 |
| C2 | **降级必须现形**。Kev 不可达 / 超时 / 超长 → 回落 Tier 0 或 Tier 2 时**必须发事件**（`decision.fallback(reason=...)`），不允许静默 | 理念六：没有驱动也必须以 skipped 现形 |
| C3 | **检查信号的产生通道不得由被检方控制**。安全类判断（`guardrails.py`）的**阻断权**不给概率模型，Kev 只能提高召回 → 升级为 ASK，不能自动放行 | 理念七 |

### 1.2 为什么不是「两个 LLM」

Tier 1 与 Tier 2 不是同质的两档模型，差异在**接口形状**：

| | Tier 1 (Kev) | Tier 2 (LLM) |
|---|---|---|
| 协议 | `POST /v1/systemone`（非 OpenAI） | `/v1/chat/completions` |
| 输入 | 一段 state + N 个结构化问题 | 消息序列 + 工具 schema |
| 输出 | 概率分布（无 token 生成） | 文本 / tool_calls |
| 失败模式 | 超长 422、不可达、低置信 | JSON 破损、幻觉、超长 |
| 缓存 | state 前缀缓存（2~2.5×） | prompt cache（provider 相关） |

**因此不能共用 `OpenAICompatClient`，也不能共用 `ModelHub`。**

---

## §2 落位（不破层门禁）

`scripts/arch/layer_matrix.py:91` 是 `longest-prefix match`，未注册模块
回落到父包层。新目录必须**显式登记**，否则门禁失明。

| 层 | 新目录 | 职责 | 依赖 |
|---|---|---|---|
| **L4** | `foundation/llm/decision/` | `Question` / `Answer` 数据模型、`DecisionClient` Protocol、`systemone.py`（httpx 实现）、`DecisionHub.resolve()` | 只依赖 httpx（已在 `pyproject.toml:33`） |
| **L3** | `kernel/agent/decision/` | 各 slot 的「状态投影 + 问题构造 + 阈值采纳 + 降级」绑定层 | 只依赖 L4 协议 |
| **L1** | `interfaces/gateway_wiring.py` | 组装根构造 client 并注入；**未配置就不构造** | L4 + L3 |

### 2.1 为什么 `DecisionHub` 与 `ModelHub` 并列而不合并

`model_hub.py` 的契约是「provider 必须是已注册的 OpenAI payload 塑形器，
否则拒绝」。把 Kev 塞进 `_REGISTRY` 会让这个契约失真：`_validate()`
的语义是「这个 provider 能塑形 OpenAI payload」，而 Kev 不能。

**并列的代价**是两处解析逻辑；**合并的代价**是一条契约被稀释。
后者更贵——契约一旦可以例外，`model_hub.py:9` 承诺的「不猜、不降级」
就不再可信。

### 2.2 端口必须 batch-first

```python
# foundation/llm/decision/protocol.py
class DecisionClient(Protocol):
    def decide(
        self,
        state: str,
        questions: dict[str, Question],
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Answer]: ...
```

**不要设计 `decide_one()`。** 理由：

1. Kev 一次前向回答 N 个问题，state 只编码一次并复用前缀缓存。
2. 单发 N 次会把 HTTP + 序列化开销放大 N 倍，把「快思考」变成新瓶颈。
3. 批量语义会**反向约束槽位选择**——优先改造「同一份上下文要问 N 个判断」
   的地方（见 §4 P2 的 goal 判据覆盖）。

若某槽位天然只有一个问题，仍走同一端口（`questions` 只有一个键），
**不新增单发方法**。

---

## §3 配置设计（待评审）

### 3.1 方案 A（推荐）：在 `llm.json` 上加 `roles` 轴

```jsonc
{
  "llm": {
    "provider": "deepseek", "model": "deepseek-chat",   // Tier 2，现状不变
    "roles": {
      "system_one": {
        "base_url": "http://127.0.0.1:8009",
        "model": "kev-4b",
        "api_key_env": "KEV_API_KEY",
        "confidence_threshold": 0.75,
        "timeout_s": 2.0,
        "max_state_tokens": 7800
      }
    }
  }
}
```

| 优点 | 缺点 |
|---|---|
| 与现有 `profiles` 机制同文件、同加载器，无需新配置文件 | `LLMConfig` 需要新增 `roles` 字段（frozen dataclass 改一次） |
| 未配置即不构造，天然满足「零行为变化」 | `roles` 与 `profiles` 语义易混（前者按角色、后者按进程） |
| 环境变量可覆盖（`AGENTNODES_LLM_ROLES__SYSTEM_ONE__BASE_URL` 之类） | 命名规范尚未在 `param-naming-unification-20261109.md` 里定义 |

### 3.2 方案 B：独立 `decision.json`

| 优点 | 缺点 |
|---|---|
| 完全隔离，不碰 `LLMConfig` | 第二个配置文件 → 第二套加载/校验/文档 |
| 可独立热重载 | 违反「配置只有一个入口」的既有倾向 |

### 3.3 方案 C：不做配置，靠环境变量

**否决**。5 个字段 + 嵌套角色，环境变量会变成一坨难以发现的隐式配置。

### 3.4 待拍板

| # | 问题 | 备选 | 倾向 |
|---|---|---|---|
| Q1 | `roles` 放 `llm.json` 还是独立文件？ | A / B | **A** |
| Q2 | `confidence_threshold` 是全局还是每槽位？ | 全局 / 每槽位 | **每槽位**（不同判断的错误代价差几个量级） |
| Q3 | 未配置时的默认行为？ | 完全不构造 / 构造但恒降级 | **完全不构造**（同 `CapabilityRegistry` 的「不注册不可见」） |
| Q4 | 是否允许远程 Kev（非 localhost）？ | 允许 / 仅 localhost | **允许但需显式 `allow_remote: true`**（避免把判断数据默认送出本机） |

---

## §4 槽位选择（按「真实触达 × 能力缺口 ÷ 风险」排序）

**v2 重排**：初版把「代码里存在」当成「运行时在用」，排序偏乐观。
下表新增**触达面**列，依据调研 §3.1.1 的逐项核实。

| 优先级 | 位置 | 原语 | 真实触达 | 收益类型 | 风险 | 备注 |
|---|---|---|---|---|---|---|
| **P0** | `kernel/workflow/orchestrate_guard.py:81` `looks_like_question` | `noul` | **每次 DAG session 收尾**（`service.py:999-1003`） | 概率 + 减少误重跑 | **极低** | 正则保留为 fallback；纯判断无副作用；输入 ≤6.7k token（实测） |
| **P1** | `kernel/workflow/node_types.py:432` `_dispatch_evaluator` | `choice`(3) + `score` | **窄**：仅 2 个内置 plan-execute 模板，只经 API 路由 | 概率 + 消灭 3 轮 ReAct | **中** | 输入实测 1–4k token（**不撞 8k**）；Kev 不能读文件 → 证据仍走工具，只下沉「判断」 |
| **P1** | `templates/.prompts/risk_controller.md:60-64` | `choice` + `score` | **广但无反馈**：13/37 preset 使用，但**零 preset 配置 `condition`** | **让 verdict 第一次可被消费** | **低** | 先修 `quant_validators.py:64` 的字段漂移，再谈原语化 |
| **P2** | `apps/goal/context.py:83` 判据覆盖 | N × `noul` **批量** | goal 续跑路径（`context_injector.py:234`） | 概率 + batch 兑现 | **中** | 实测 40 判据 + 40 证据 = 4.7k token，**宽裕**；不得作为完成门（C1） |
| **P2** | `kernel/agent/guardrails.py:99` 注入检测 | `noul`（仅升级） | 每次用户输入 + 每个工具结果 | 抓正则漏掉的改写 | **中** | **唯一真会超 8k 的槽位**（30k 工具结果 = 10k token）→ 只扫首尾窗口；只能 escalate（C3） |
| **❌ 移出** | `critic` / `study_reviewer` | — | **零编排**（37 preset 零出现） | — | — | 接进去也没有输入。先回答「要不要这个角色」 |
| **❌ 推迟** | `kernel/evolution/holdout.py:216` `classify_intent` | `choice`(5) | 离线 | 语义分类 | — | 签名含 intent（`holdout.py:233`），换分类器让历史签名失效 → 需先升 `TASK_SIGNATURE_VERSION`（R4/Q6） |

### 4.1 为什么 P0 仍是 `looks_like_question`

它是唯一同时满足「真实高频触达 + 输入天然小 + 有廉价可观测的后果」的槽位：

- 每次 DAG session 收尾都过（`service.py:999-1003`），**不是罕见路径**；
- 输入是最终答复，实测 20k 字符 = 6.7k token，**在 8k 内**；
- 输出是布尔，不需要任何生成；
- 判错 → 多跑一整轮 LLM，代价明确且可度量；
- 现有实现是正则（`_QUESTION_CLOSE` / `_WORD_TAIL` / `_WH_TAIL`，
  `orchestrate_guard.py:46-71`），保留为 fallback 后**风险接近零**；
- 已有 26 条参数化测试（`tests/test_orchestrate_guard.py:18-48`）可直接
  当作**现成的标注集**用于影子期冷启动——这是它额外的优势。

### 4.2 为什么 P1 的 evaluator 不能一步到位

`_dispatch_evaluator` 当前的输入是 `_upstream_summary(ctx)`，即上游步骤
的 summary/artifacts 拼接。Kev 的 8,192 token 上限意味着：

- 上游步骤多、artifacts 大时**会 422**；
- 需要投影器把 summary 压到预算内（§5.1）；
- 压完之后判断质量是否仍够，**必须靠影子数据回答，不能靠直觉**。

所以 P1 的正确形态是：**Tier 2 先跑（现有 AgentLoop），Tier 1 影子并发，
比对 verdict 一致性**；一致率与置信分布达标后才切换主路径。

---

## §5 三个硬约束的应对

### 5.1 上下文长度（**v2 更正：不是普遍障碍**）

| 事实 | 应对 |
|---|---|
| 0.8B/4B/9B validated **8,192** token | 客户端侧硬校验 token 数，超限**不发请求**，直接降级 |
| 超出 server 上限 **422 拒绝**（不静默截断） | 同上；**禁止**依赖 `KEV_TRUNCATE_STATES=1`（静默截断会让判断基于残缺输入） |
| 27B 到 65,536 | 若未来上 27B，预算可放宽，但仍是有限值 |

**实测（调研 §5）推翻了「普遍撞墙」的判断**：

| 槽位 | 实测 token | 结论 |
|---|---|---|
| evaluator（默认截断，5 节点） | 1,137 | 宽裕 |
| evaluator（默认截断，20 节点） | 3,772 | 宽裕 |
| `looks_like_question`（20k 字符答复） | 6,666 | 够 |
| goal 判据批量（40 判据 + 40 证据） | 4,766 | 宽裕 |
| **guardrails（30k 工具结果）** | **10,000** | **唯一真超** |

**投影器从「必需」降级为「防御性」**：

```python
class StateProjector(Protocol):
    def project(self, payload: ProjectionInput, *, budget_tokens: int) -> ProjectedState: ...
```

- 实现放在 L3 `kernel/agent/decision/projectors/`，**每槽位一个**——
  投影是「这个判断需要看到什么」的业务语义，不能做成通用压缩。
- **不是批次 B 的前置依赖**——首批槽位输入本来就装得下。
- 投影结果必须**可解释**：落 `decision.requested` 事件时带
  `projected_tokens` / `dropped_sections`，否则出问题无法归因。
- token 估算复用现有 `foundation/utils/token_utils.py`，不引新依赖。
  **注意它是 3 chars/token 的粗略估算**（`token_utils.py:12`），中文场景
  实际 token 数会更高，硬校验要留安全余量（建议按 6k 而非 8k 设阈）。

### 5.2 frozen `LLMConfig` 的 per-call 覆盖 bug

`interfaces/api/session/service.py:897`：

```python
if cfg and model:
    cfg.model = model        # FrozenInstanceError
```

这是**前置修复项**，不是本方案的一部分。修法：改 `cfg.with_config(model=model)`。
一旦开始做 per-call 模型选择，这条路径必然被踩到。

### 5.3 健康检查与超时

| 问题 | 应对 |
|---|---|
| 本地 serve 有冷启动（首次下载 adapter + base） | 启动时不阻塞探测；首次请求超时 → 降级 + 事件 |
| `timeout_s` 给多少 | **≤2s**。Tier 1 的存在意义是「比 Tier 2 快一个数量级」；给到 10s 就失去意义 |
| 连续失败怎么办 | 熔断（复用 `kernel/agent/circuit_breaker.py` 的思路），N 次失败后一段时间内直接走 Tier 0/2，**并持续发 fallback 事件** |

---

## §6 风险清单

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| **R1** | ~~8k 上限导致大面积 422~~ | **实测不成立**（调研 §5）：除 guardrails 扫 30k 工具结果外，各槽位输入 1–5k | 保留硬校验作防御；guardrails 只扫首尾窗口 |
| **R2** | 判断错误被静默吞掉 | 错误动作无法归因 | 每次 `decision.resolved` 落盘：state 摘要、问题、分布、采纳/降级、最终结果 |
| **R3** | 置信度被误当准确率 | 阈值设错 | 文档与事件字段都明确标注「confidence 是分布形状函数，不是实测准确率」（README 原文口径）；阈值必须在本项目数据上校准 |
| **R4** | `classify_intent` 改动破坏 task_signature 稳定性 | RSI 的历史签名对不上，holdout 抽样错乱 | **本方案最隐蔽的风险**。`holdout.py:233` 的签名含 intent；换分类器会让同一任务算出新签名。应对：`TASK_SIGNATURE_VERSION` 升版 + 双写过渡期 |
| **R5** | 多一个常驻服务 | 部署复杂度上升 | 未配置不构造；文档明确「可选能力层，非必需依赖」 |
| **R6** | 27B 才 65k，小模型 8k | 误以为「换个模型就能吃长上下文」 | 容量规划以 model card 的 validated context 为准，不用文章数字 |
| **R7** | 微调数据来自运行期决策日志 | 数据回流污染 / 自我强化偏差 | 微调（批次 G）**推迟**，且训练集必须含「Tier 2 反驳 Tier 1」的负例 |
| **R8** | **成本论据不成立**（调研 §6） | 若以「降本」立项，评审阶段就会被算术否掉 | 立项理由改为「补齐概率化决策能力」。**不要在方案里写 ROI** |
| **R9** | **影子期工作量被低估**（调研 §4.1） | 无比较器 / 无决策事件 / 无分桶，四件套全要新建 | 单独设为批次 B，不与其他批次混做 |
| **R10** | **token 估算偏乐观** | `token_utils` 是 3 chars/token，中文实际更高 | 硬校验按 **6k** 设阈而非 8k |

---

## §7 落地批次（v2 重排）

沿用「每批独立可回滚、零行为变化优先」的既有写法。

| 批次 | 内容 | 行为变化 | 回滚方式 |
|---|---|---|---|
| **A** | L4 `decision/` 端口 + `systemone.py` + `DecisionHub` | **零**（未配置不构造） | 删目录 + 回退 `layer_matrix.py` 登记 |
| **B** | **影子基础设施**（独立批次，R9）：决策事件 schema + 双跑包装 + 比较器 + 落盘 | 零（只多事件） | 关开关 |
| **C** | 影子期：`looks_like_question` 双跑，只记录 agreement | 零 | 关开关 |
| **D** | 校准 + 首个真采纳：`looks_like_question` → `noul`，阈值 + 正则 fallback | 有 | 开关回正则 |
| **E** | evaluator verdict → `choice` + `score`，AgentLoop 降级为 fallback | 有 | 开关回 AgentLoop |
| **F** | `risk_controller` 字段漂移修复 + 原语化 | 有 | 逐角色开关 |
| **G** | goal 判据批量 `noul` | 有 | 开关 |
| **H**（推迟） | decision log → 微调 Kev adapter | — | 不部署即无影响 |

**关键变化**：
- **影子基础设施单独成批（B）**——它不是「加个开关」，要新建决策事件
  schema、双跑包装、比较器、落盘四件套。混在别批里会无法归因。
- **`risk_controller` 从 E 拆出来单独一批（F）**，因为它需要**先修 bug**
  （`quant_validators.py:64` 校验了 prompt 从不产出的字段）。
- **critic / study_reviewer 移出**——37 preset 零编排，接进去没有输入。

### 7.1 批次 A 的最小验收

- 未配置 `roles.system_one` 时，全量测试**零差异**；
- 配置后，一个 `curl` 等价请求能通，事件正确落盘；
- `scripts/arch/layer_matrix.py` 新增条目，门禁通过；
- **不引入 torch / mlx 依赖**（`pyproject.toml` 的 `dependencies` 不变）。

### 7.2 批次 C/D 的验收门（整套方案的关键，不能省）

影子期把每条 `decision.resolved` 事件落盘，回放成标注集，计算：

```
在错误预算 5% 下，Tier 1 能自动采纳的决策比例
```

这是 Kev README 用的同一条曲线（Jev 0.70 / Kev-4B 0.52–0.69，新来源数据），
但**必须在本项目自己的分布上重算**。

**冷启动标注集已经有了**：`tests/test_orchestrate_guard.py:18-48` 的
26 条参数化用例（16 正 + 10 负）可直接当作初始标注集，
正则实现即为初始 baseline 的预测器。

| 结果 | 决策 |
|---|---|
| 采纳率 ≥ 目标 且 错误率 ≤ 预算 | 进批次 D 真采纳 |
| 采纳率低于目标 | **只保留影子**，不上线采纳 |
| 超限（422）比例过高 | 先补投影器，重跑影子 |

> 这条门的意义：它回答「能替掉多少判断」，而不是「准确率多少」。
> 后者在类别不均衡时会给出误导性的乐观数字。
>
> **同时必须记录成本**：即使采纳率高，也要用调研 §6 的模型复核
> 「省的 LLM 调用费 vs 新增的运维成本」，避免出现「技术上成功、
> 经济上荒谬」的结论。

---

## §8 与既有机制的关系

| 既有机制 | 关系 |
|---|---|
| `kernel/agent/deciders.py` | **不动**。Tier 1 是 decider 的**上游**——它产出一个结论，再由 decider 翻译成四值 `Decision`。不是替代 |
| `foundation/structured_output.py` | **不动**。它的 4 层降级是 Tier 2 的补丁；Tier 1 路径不需要它。长期看 Tier 1 铺开会让它调用量下降 |
| `kernel/evolution` | **潜在的自我强化回路**：每条 `decision.resolved` 既是日志也是训练样本。这是本项目第一个「随运行而自我强化成本结构」的部件。但见 R7——批次 G 之前不做 |
| `CapabilityRegistry` | **不冲突**。decision 层是 kernel 内部机制，不需要业务侧注册；若未来某槽位的投影逻辑是业务语义，可考虑走 capability 端口 |
| `ContextHub` | 投影器**可能**复用其仲裁阶段（`context-hub-discussion.md` §2.5 的 ContextPlan）。待 ContextHub 落地后再评估，**当前不依赖** |

---

## §9 明确不做

1. **不做工具路由**（原生 function-calling 更强，换掉是净亏）。
2. **不做进程内权重加载**（会把 L4 变成 GPU 依赖）。
3. **不把 `provider: "kev"` 写进 `llm.json`**（`model_hub.py:54` 拒绝；语义错配）。
4. **不依赖 `KEV_TRUNCATE_STATES=1`**（静默截断 → 基于残缺输入的判断）。
5. **不让 Tier 1 拥有 allow 权限**（C1/C3）。
6. **不在批次 A–F 期间改 `structured_output.py`**（两件事混做无法归因）。
7. **不引用文章的性能/成本数字做承诺**（见调研 §1.2）。

---

## §10 待拍板的开放问题（汇总 · v2）

| # | 问题 | 选项 | 建议 |
|---|---|---|---|
| Q1 | 配置位置 | `llm.json` 的 `roles` / 独立 `decision.json` / 纯 env | **`llm.json` 的 `roles`** |
| Q2 | 阈值粒度 | 全局 / 每槽位 | **每槽位** |
| Q3 | 未配置行为 | 不构造 / 构造但恒降级 | **不构造** |
| Q4 | 是否允许远程 Kev | 允许 / 仅 localhost | **允许但需显式 opt-in** |
| Q5 | evaluator 是否同期做 | 与 P0 同批 / 单独一批 | **单独一批**（批次 E，不与 P0 混） |
| Q6 | `classify_intent` 是否纳入 | 纳入 / 推迟到签名升版后 | **推迟**——R4 的连锁影响需要单独设计 |
| Q7 | 事件命名 | `decision.*` / `systemone.*` | **`decision.*`**（不绑厂商） |
| Q8 | 模型档位 | 0.8B / 4B / 9B / 27B | **影子期用 4B**（README 推荐起步档） |
| **Q9** | **本方案是否值得做**（v2 新增，最重要） | 全做 / 只做 A+B 探路 / 不做 | **只做 A+B 探路**。理由：成本论据已否（§0.1），唯一硬理由是「补齐概率能力」，而这需要先有影子数据才能证明价值。**A+B 是低成本探路，C 之后再按数据决定是否继续** |
| **Q10** | **`risk_controller` 的字段漂移怎么修**（v2 新增） | 改校验器 / 改 prompt / 两者都改 | **改校验器**（`quant_validators.py:64` 校验 `risk_passed`），因为 13 个 preset 依赖现有 prompt 输出；但这属于**独立 bug fix**，不应混在本方案里 |
| **Q11** | **critic / study_reviewer 要不要留**（v2 新增） | 保留 / 删除 / 待定 | **待定，但不阻塞本方案**。它们零编排、零消费者——这是「减法」议题，不是 System One 议题 |
| **Q12** | **是否需要分桶放量**（v2 新增） | 需要 / 不需要 | **不需要**。真实触达量低（远低于 1.16 万次/天），全量开关即可；百分比放量是给高流量场景用的 |

### 10.1 如果只能回答一个问题

**Q9**。v2 调研的两条实测（成本不成立、8k 不是墙）已经削弱了本方案
的大部分「优化」色彩。它现在是一个**能力补齐型提案**，而非效率提案。

因此建议的默认答案是：**做 A + B（端口 + 影子基础设施），
然后停下来看数据**。这两批的行为变化为零、可完全回滚，
且产出的决策日志无论后续是否采纳 Kev 都有独立价值
（它是本项目第一个「带概率的决策日志」）。

