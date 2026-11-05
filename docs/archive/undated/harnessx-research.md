# HarnessX 调研报告

> 调研时间: 2026-08-04（首次）; 2026-08-04（复核，补充技术报告细节）
> 论文: https://arxiv.org/abs/2606.14249
> GitHub: https://github.com/Darwin-Agent/HarnessX
> 官网: https://darwin-agent.github.io/HarnessX/（含完整 technical report）

---

## 1. 概述

HarnessX 是小米 Darwin Agent Team 开源的 **Agent 运行时框架**，核心理念是将 Agent 的 Harness（运行时调度层）变成可组合、可演化的一等公民。

**核心公式**: `agent = model.agentic(harness)`

- `ModelConfig` — 提供商路由、fallback、per-role 模型分配
- `HarnessConfig` — 完整的行为流水线（工具、记忆、处理器、trace、沙箱）

**X** 代表 e**X**tensible Behavior Composition — 像搭乐高积木一样组合、适配、演化 Harness，无需重写 Agent。

---

## 2. 核心问题

现有 Agent 框架（LangChain、AutoGen、Claude Code、DeerFlow）的 Harness 全靠人工静态开发：

| 问题 | 说明 |
|------|------|
| 换模型/工具/领域要重写 | 脚手架无法复用 |
| 轨迹数据浪费 | 执行产生的轨迹无法用于自动优化 |
| 代码耦合 | 提示词、工具封装、重试、记忆全搅在一条代码里 |
| 优化互不连通 | Harness 产出的轨迹白白浪费，模型提升也无法同步更新调度 |

---

## 3. 架构设计

### 3.1 Processor 积木块

最小行为单元是 Processor，8 个生命周期钩子：

```
step_start → context_ready → before_model → after_model 
→ before_tool → after_tool → step_end → task_end
```

9 维行为分类：
1. **context** — 系统提示词、历史管理、用户包装器
2. **control** — 13 个安全与可靠性处理器（循环检测、工具调用纠正、解析重试等）
3. **evaluation** — LLM 判官、PRM、自验证
4. **memory** — 提取、检索、5 种策略
5. **multi_model** — 模型路由
6. **observability** — OTel、检查点、指标
7. **tools** — 技能加载器、schema 适配器、过滤器
8. **sandbox** — 本地、Docker、E2B
9. **training** — SFT/RL 桥接

**组合方式**: `builder = HarnessBuilder().add(proc1).add(proc2).slot(tool_registry=reg)`

### 3.2 AEGIS 进化引擎

AEGIS = Adaptive Evolutionary Guardrails for Intelligent Systems

**核心思想**: 把 Harness 演化映射为强化学习 MDP：

| MDP 元素 | 对应 |
|---------|------|
| 状态 | 当前配置 + 历史轨迹库 |
| 动作 | 安全编辑（加工具、改提示、重构流程） |
| 奖励 | 任务得分 + 完整轨迹 |

**四阶段流水线**（全由元大模型驱动）：

1. **压缩** — 海量原始轨迹 → 关键故障
2. **规划** — 哪些坑踩过、哪些方向没试
3. **生成** — 类型安全的候选 Harness 修改方案
4. **Critic** — 盯奖励欺骗，确定性闸门强制"新方案不能让已解决任务变差"

### 3.3 模型 + Harness 协同进化

- Harness 演化和模型 RL 训练共享同一个回放缓冲区
- Harness 用 AEGIS 做符号化编辑，模型用跨 Harness 的 GRPO 做参数微调
- 同任务下不同脚手架的轨迹放一组比相对优势 → 模型学会在各种执行流程里找最优策略

---

## 4. 实验结果

5 个基准（ALFWorld、GAIA、WebShop、τ³-Bench、SWE-bench Verified）：

| 指标 | 数据 |
|------|------|
| 平均提升 | **+14.5%** |
| 最高提升 | **+44.0%** |
| 弱模型提升更大 | Qwen 在 ALFWorld: 53% → 97% |
| 强模型提升较小 | Sonnet 4.6 仅 +11% |

消融实验：
- 全局单 Harness：峰值 73.8% → 最终 49.5%（灾难性遗忘严重）
- 变体隔离：峰值 87.4%，无退化，token 节省 25%
- AEGIS 四阶段 vs 单阶段：精度相近但 token 节省 14%

### 4.1 核心结果细节

- **14/15** 配置提升（仅 GAIA + GPT-5.4 持平，0.0%）
- **最大增益案例**: ALFWorld + Qwen3.5-9B: 53.0% → 97.0%（+44.0%）
- **评估协议**: 最多 15 轮，每轮跑全任务集，连续 3 轮无提升提前停；用 **Pass@2**（两个独立 attempt，任一成功即算过）
- **异构任务停滞案例**: GAIA + GPT-5.4 在全局单 Harness 下停滞（峰值 73.8% → 最终 49.5%）；变体隔离达到 87.4%（峰值=最终），token 从 143.7M 降至 107.8M

### 4.2 Failure Analysis（轨迹分析揭示的失败模式）

| 失败模式 | 现象 | 后续对策 |
|---------|------|---------|
| **Reward hacking** | GAIA/Sonnet 靠 verifier 格式规律从 74.8% 涨到 79.6% | 两轮后加 cross-check guard |
| **灾难性遗忘** | tau³-Bench Telecom 第 6 次同类型提醒使合规率 94.7% → 80.7%（R7） | R9 恢复，靠变体隔离 |
| **Under-exploration** | ALFWorld/Sonnet 的 prompt 编辑每轮 <1% 增益，ship 预测准确率 80% → 0% | 需检测"杠杆疲劳"并换杠杆 |

---

## 5. 与我们项目的映射

### 5.1 架构对比

| HarnessX 组件 | 我们的对应组件 | 冲突 |
|---------------|--------------|------|
| RunLoop (异步) | AgentLoop (线程) | 执行模型完全不同 |
| InMemoryToolRegistry | ToolRegistry | API 不兼容 |
| Processor (8 生命周期钩子) | on_event 回调 | 钩子模型不同 |
| HarnessJournal (Tracing) | EventStore + SSE | 事件系统不同 |
| ModelConfig + Providers | LLMConfig | 提供商抽象不同 |
| HarnessBuilder (组合式) | GoalWorkflowConfig (DAG) | 编排模型不同 |
| Sandbox (Docker/E2B) | 本地执行 | 不需要沙箱 |

### 5.2 概念映射

| AEGIS 概念 | 我们项目对应 | 可借鉴度 |
|-----------|-------------|---------|
| Journal | Goal + Evidence 系统 | ⭐⭐⭐ 高 |
| Attribution | StudyMonitor drift 检测 | ⭐⭐⭐ 高 |
| Lever Scoreboard | 无对应 | ⭐⭐⭐ 高（新能力） |
| Novelty Gate | 无对应 | ⭐⭐ 中 |
| **Seesaw/回归门控**（候选不能回归已解决任务） | 无对应（归因里的 `regressed` 只记录不拦截） | ⭐⭐⭐ 高（最值得补） |
| **Early-stop 协议**（连续 3 轮无提升提前停） | 无对应（`study_rounds` 有轮次但无早停逻辑） | ⭐⭐⭐ 高 |
| **Pass@2 采样**（两个 attempt 任一成功即过） | 回测单次跑 | ⭐⭐ 中 |
| **失败编辑归档**（被拒编辑连同原因归档） | 无对应（有 `retry_rationale` 但缺归档通道） | ⭐⭐ 中 |
| **Reward-hacking 交叉校验**（verifier 被游戏化时加 guard） | verdict 是 LLM 判定，有被指标游戏化风险 | ⭐⭐ 中 |
| **Under-exploration 检测**（杠杆连续低增益即失效） | 无对应 | ⭐ 低-中 |
| Evidence Gate | 无对应 | ⭐⭐ 中 |
| Replay Gate | 无对应 | ⭐ 低 |

---

## 6. 结论：能否直接用？

### 简短回答：**不能直接用，但可以借鉴核心概念**

**不能直接用的原因**：
1. HarnessX 是完整 Agent 框架，不是可插入的库
2. 替换 AgentLoop/ToolRegistry/EventStore 需要 2-3 人月
3. AEGIS 引擎需要元模型、轨迹收集、RL 训练基础设施

**值得借鉴的概念**：
1. **Processor 生命周期钩子** — 让行为更可组合
2. **Journal 跨轮次记忆** — 追踪假设效果
3. **Attribution 归因系统** — 精确归因每个预测
4. **Lever Scoreboard** — 识别最有效的修改类型
5. **Novelty Gate** — 防止重复失败的假设
6. **HarnessConfig 序列化** — 支持版本化和一键恢复

---

## 7. 推荐方案

> 最新实施计划见 `docs/archive/undated/aegis-implementation-plan.md`（已扩展为 6 大机制）

| 阶段 | 行动 | 工作量 |
|------|------|--------|
| **Phase 1** | GoalJournal：给 Goal 添加跨轮次追踪 + 失败编辑归档 | 1 周 |
| **Phase 2** | StudyAttribution：对 Study 结果进行归因 | 1 周 |
| **Phase 3** | Lever Scoreboard：追踪修改类型的效果 + 杠杆疲劳检测 | 1 周 |
| **Phase 4** | Novelty Gate + Regression Gate：防重复失败 + 防回归已解决任务 | 3-4 天 |
| **Phase 5** | Early-stop 协议：连续 3 轮无提升提前停止 | 2 天 |

**不推荐**：替换 AgentLoop/ToolRegistry/EventStore（成本太高，收益不确定）。

**可选增强**（Phase 5 之后）：
- Pass@2 采样：回测多 seed 运行，降低误判
- Reward-hacking 交叉校验：verdict 双通道核验
- 变体隔离：任务冲突时路由到独立 Study 变体而不是丢弃局部修复

---

## 8. 实施状态（2026-08-04）

### ✅ 已完成

| 概念 | 落地 | 文件 |
|---|---|---|
| **Journal** | `goal_journal` 表 + `JournalEntry` + CRUD + `build_journal_context` | `goal/store.py`、`goal/models.py` |
| **Attribution** | `classify_attribution` + `compute_precision`（纯函数） | `study/attribution.py` |
| **Lever Scoreboard** | `LeverScoreboard`（Beta 后验 + 疲劳检测 + context 构建） | `goal/scoreboard.py` |
| **Novelty Gate** | `check_novelty`（ID 重复 / 签名重复 / 标签相似度） | `goal/store.py` |
| **Regression Gate** | `check_regression` + `archive_rejected_edit`（软标记） | `goal/store.py` |
| **Early-stop** | `_check_early_stop`（连续 3 轮无提升，仅 max_rounds 时生效） | `study/runner.py` |
| **阶段拆分** | `run_research_round` → 3 个独立阶段函数 | `autoresearch.py` |
| **AutoresearchRunner** | AEGIS-powered 轮次引擎（替代 AutoresearchExecutor） | `study/runner.py` |
| **双引擎切换** | `executor_type` 路由（默认 autoresearch / 可选 workflow） | `study.py`、`chat.py` |
| **Prompt 注入** | researcher/strategist 新增 `predicted_affected` + journal/scoreboard context | `.prompts/researcher.md`、`.prompts/strategist.md` |

### ⏳ 待完成

| 内容 | 状态 |
|---|---|
| 新增测试（attribution/scoreboard/journal/rounds/runner_aegis） | 待实施 |
| 前端 Study tab 轮次历史 + journal/scoreboard 展示 | 待定 |
| `docs/archive/undated/aegis-implementation-plan.md` 更新 | ✅ 已更新 |
