# System One 决策层接入 — 方案设计与评审稿

> **性质**：**讨论稿**，含待拍板的开放问题与备选方案。非实施规格。
> **上位调研**：`system-one-survey-20261109.md`（事实层：Kev 接口、
> 本项目决策点普查、四个结构性缺口）。
> **约束来源**：`framework-and-principles-20261105.md`（理念四/五/七）、
> `layered-skeleton-design-20261103.md`（L1–L4 归属与方向规则）。
> **关联**：`unified-loop-adoption-eval-20261107.md`（decider 协议来源）、
> `context-hub-discussion.md`（投影器可能复用其仲裁阶段）。

---

## §0 一句话结论

**不要把 Kev 当「更便宜的 LLM」接，把它当一个新层接。**

先补「模型分层」这根轴（`roles` 配置 + 独立客户端家族 + batch-first 端口），
再在两个低风险槽位做影子校准，用「错误预算下可自动化比例」证明自己，
最后才推开。任何跳过「影子 + 校准曲线」直接采纳的做法都应否决。

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

## §4 槽位选择（按收益 ÷ 风险排序）

只列**值得做**的。不适用槽位见调研 §5。

| 优先级 | 位置 | 原语 | 收益 | 风险 | 备注 |
|---|---|---|---|---|---|
| **P0** | `kernel/workflow/orchestrate_guard.py:81` `looks_like_question` | `noul` | 现在是正则尾匹配；判错 → `service.py:969` 重跑一整轮 LLM（贵） | **极低** | 正则保留为 fallback；纯判断，无副作用 |
| **P0** | `kernel/evolution/holdout.py:216` `classify_intent` | `choice`(5) | 关键词表 → 语义分类 | **极低**（技术上） | 离线批处理，错也只影响抽样权重。**但见 R4**：签名含 intent，换分类器会让历史签名对不上 → **实际排期推迟到签名升版**（Q6） |
| **P1** | `kernel/workflow/node_types.py:432` `_dispatch_evaluator` | `choice`(3) + `score` | **最大单点收益**：把 6 轮 ReAct 压成一次前向 | **中** | Kev 不能读文件 → 证据收集仍走工具，只把「判断」下沉；输入用 `_upstream_summary(ctx)`（:298） |
| **P1** | `templates/.prompts/{critic,risk_controller,backtest_diagnostics,study_reviewer}.md` 的枚举字段 | `choice` / `score` | 现在靠 prompt 写「必须返回纯 JSON」+ 事后枚举校验；但 critic / study_reviewer 的 verdict **代码里根本没读回方**，risk_controller 更是 **prompt 产出 `risk_passed` 而校验器校验 `verdict`**（`quant_validators.py:60` 恒 no-op） | **低** | 原语化后字段即契约，不可能漂移；同时暴露「声明了但没人用」的字段 |
| **P2** | `apps/goal/context.py:83` 判据覆盖 | N × `noul` **批量** | 一次请求问 N 个「这条证据是否满足判据 X」 | **中** | **batch 设计的最佳兑现点**；不得作为完成门（C1） |
| **P2** | `kernel/agent/guardrails.py:99` 注入检测 | `noul`（仅升级） | 抓正则漏掉的改写 | **中** | **只能 escalate，不能 allow**（C3） |

### 4.1 为什么 P0 选 `looks_like_question`

它是教科书式的 System One 槽位：

- 输入是一小段文本（`final_answer` 的尾部），**天然在 8k 以内**；
- 输出是布尔，不需要任何生成；
- 判定错误有**明确、廉价、可观测的后果**（多跑一轮 LLM），所以
  A/B 对比容易量化；
- 现有实现是正则（`_QUESTION_CLOSE` / `_WORD_TAIL` / `_WH_TAIL`，
  `orchestrate_guard.py:60-71`），保留为 fallback 后**风险接近零**。

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

### 5.1 上下文长度（最可能撞墙）

| 事实 | 应对 |
|---|---|
| 0.8B/4B/9B validated **8,192** token | 客户端侧硬校验 token 数，超限**不发请求**，直接降级 |
| 超出 server 上限 **422 拒绝**（不静默截断） | 同上；**禁止**依赖 `KEV_TRUNCATE_STATES=1`（静默截断会让判断基于残缺输入） |
| 27B 到 65,536 | 若未来上 27B，预算可放宽，但仍是有限值 |

**投影器设计（待评审）**：

```python
class StateProjector(Protocol):
    def project(self, payload: ProjectionInput, *, budget_tokens: int) -> ProjectedState: ...
```

- 实现放在 L3 `kernel/agent/decision/projectors/`，**每槽位一个**——
  投影是「这个判断需要看到什么」的业务语义，不能做成通用压缩。
- 投影结果必须**可解释**：落 `decision.requested` 事件时带
  `projected_tokens` / `dropped_sections`，否则出问题无法归因。
- token 估算复用现有 `foundation/utils/token_utils.py`，不引新依赖。

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
| **R1** | 8k 上限导致真实负载大面积 422 | 采纳率远低于预期 | 投影器 + 硬校验 + 影子期先测「超限比例」这个指标 |
| **R2** | 判断错误被静默吞掉 | 错误动作无法归因 | 每次 `decision.resolved` 落盘：state 摘要、问题、分布、采纳/降级、最终结果 |
| **R3** | 置信度被误当准确率 | 阈值设错 | 文档与事件字段都明确标注「confidence 是分布形状函数，不是实测准确率」（README 原文口径）；阈值必须在本项目数据上校准 |
| **R4** | `classify_intent` 改动破坏 task_signature 稳定性 | RSI 的历史签名对不上，holdout 抽样错乱 | **这是本方案最隐蔽的风险**。`holdout.py:233` 的签名含 intent；换分类器会让同一任务算出新签名。应对：签名版本号 `TASK_SIGNATURE_VERSION` 升版 + 双写过渡期 |
| **R5** | 多一个常驻服务 | 部署复杂度上升 | 未配置不构造；文档明确「可选加速层，非必需依赖」 |
| **R6** | 27B 才 65k，小模型 8k | 误以为「换个模型就能吃长上下文」 | 容量规划以 model card 的 validated context 为准，不用文章数字 |
| **R7** | 微调数据来自运行期决策日志 | 数据回流污染 / 自我强化偏差 | 微调（批次 G）**推迟**，且训练集必须含「Tier 2 反驳 Tier 1」的负例 |

---

## §7 落地批次

沿用「每批独立可回滚、零行为变化优先」的既有写法。

| 批次 | 内容 | 行为变化 | 回滚方式 |
|---|---|---|---|
| **A** | L4 `decision/` 端口 + `systemone.py` + `DecisionHub` + 事件 `decision.requested` / `decision.resolved` / `decision.fallback` | **零**（未配置不构造） | 删目录 + 回退 `layer_matrix.py` 登记 |
| **B** | 影子模式：`looks_like_question` 双跑，只记录 agreement，不采纳 | 零（只多事件） | 关配置开关 |
| **C** | 校准 + 首个真采纳：`looks_like_question` → `noul`，阈值 + 正则 fallback | 有 | 开关回正则 |
| **D** | evaluator verdict → `choice` + `score`，AgentLoop 降级为 fallback | 有，收益最大 | 开关回 AgentLoop |
| **E** | 角色输出原语化（critic / risk_controller / study_reviewer / backtest_diagnostics） | 有 | 逐角色开关 |
| **F** | goal 判据批量 `noul` | 有 | 开关 |
| **G**（推迟） | decision log → 微调 Kev adapter | — | 不部署即无影响 |

### 7.1 批次 A 的最小验收

- 未配置 `roles.system_one` 时，全量测试**零差异**；
- 配置后，一个 `curl` 等价请求能通，事件正确落盘；
- `scripts/arch/layer_matrix.py` 新增条目，门禁通过；
- **不引入 torch / mlx 依赖**（`pyproject.toml` 的 `dependencies` 不变）。

### 7.2 批次 B/C 的验收门（整套方案的关键，不能省）

影子期把每条 `decision.resolved` 事件落盘，回放成标注集，计算：

```
在错误预算 5% 下，Tier 1 能自动采纳的决策比例
```

这是 Kev README 用的同一条曲线（Jev 0.70 / Kev-4B 0.52–0.69，新来源数据），
但**必须在本项目自己的分布上重算**。

| 结果 | 决策 |
|---|---|
| 采纳率 ≥ 目标 且 错误率 ≤ 预算 | 进批次 C 真采纳 |
| 采纳率低于目标 | **只保留影子**，不上线采纳 |
| 超限（422）比例过高 | 先补投影器，重跑影子 |

> 这条门的意义：它回答「能替掉多少判断」，而不是「准确率多少」。
> 后者在类别不均衡时会给出误导性的乐观数字。

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

## §10 待拍板的开放问题（汇总）

| # | 问题 | 选项 | 建议 |
|---|---|---|---|
| Q1 | 配置位置 | `llm.json` 的 `roles` / 独立 `decision.json` / 纯 env | **`llm.json` 的 `roles`** |
| Q2 | 阈值粒度 | 全局 / 每槽位 | **每槽位** |
| Q3 | 未配置行为 | 不构造 / 构造但恒降级 | **不构造** |
| Q4 | 是否允许远程 Kev | 允许 / 仅 localhost | **允许但需显式 opt-in** |
| Q5 | P1 evaluator 是否同期做 | 与 P0 同批 / 单独一批 | **单独一批**（收益最大但风险也最大，不要和 P0 混） |
| Q6 | `classify_intent` 是否纳入 | 纳入 / 推迟到签名升版后 | **推迟**——R4 的连锁影响需要单独设计 |
| Q7 | 事件命名 | `decision.*` / `systemone.*` | **`decision.*`**（不绑厂商；将来换模型不用改事件名） |
| Q8 | 是否现在就定模型档位 | 0.8B / 4B / 9B / 27B | **影子期用 4B**（README 推荐的起步档）；按影子数据再决定 |
