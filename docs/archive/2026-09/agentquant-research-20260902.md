# AgentQuant 调研报告 — 2026-09-02

## 概述

**AgentQuant** 不是一个统一项目，而是 GitHub 上两个**独立项目**共用的名字，都在探索 AI 代理 + 量化交易研究的交叉领域。本地代码库（`quantnodes-strategy-research`）与它们是**独立开发的平行项目**，无任何直接依赖。

---

## 项目 A：OnePunchMonk/AgentQuant（184 星，MIT）

**仓库**：https://github.com/OnePunchMonk/AgentQuant

### 定位
自我进化的自主 AI 量化研究代理。

### 核心架构
ReAct 循环（分析 → 假设 → 回测 → 反思 → 存储 → 改进），5 个类型化节点。

### 关键特性
- **SQLite 持久记忆**：跨会话保存策略学习
- **参数自我进化**：遗传算法 + 差分进化优化代理参数（6 个 epoch，Sharpe 提升 +37.4%）
- **6 策略库**：momentum / mean_reversion / volatility / trend_following / breakout / multi_strategy
- **Walk-forward 验证**：train/validation/test 分割防止过拟合
- **可证伪声明追踪**：回测前预测 Sharpe，验证 86% 准确率
- **Web 搜索集成**：Tavily API 获取市场情绪
- **63 个单元测试** + CI/CD

### 技术栈
Python 3.10+ / yfinance / SQLite / Claude (Anthropic) / Tavily API / Streamlit UI

---

## 项目 B：nlpquant/AgentQuant（17 星，AGPL-3.0）

**仓库**：https://github.com/nlpquant/AgentQuant

### 定位
自然语言 → 可执行策略的量化交易平台（NVIDIA AI Hackathon Top 3）。

### 核心架构
Full-stack monorepo（apps/web + apps/agent + apps/mcp-server）

### 关键特性
- 自然语言生成策略代码
- Kubernetes 沙箱执行隔离
- 20+ 回测绩效指标
- MCP 服务器

### 技术栈
Next.js 15 / TypeScript / Python 3.12 / LangChain / NVIDIA NAT / Redis / Docker

### ⚠️ 版权风险
AGPL-3.0（病毒式许可），不建议采纳。

---

## 与 strategy-research 的对比

| 维度 | strategy-research（本地） | AgentQuant A | AgentQuant B |
|------|--------------------------|--------------|--------------|
| **定位** | AI 驱动量化研究框架 | 自我进化代理 | NL→策略产品 |
| **Agent 架构** | 多代理 Swarm + DAG 调度 | ReAct 循环（5 节点）| LangChain + NAT |
| **因子库** | 460+ 因子（Alpha101/GTJA191/Qlib158）| 6 策略 | 50+ 指标 |
| **数据源** | 8 个（腾讯/东财/AkShare/Tushare/...）| yfinance | Yahoo Finance |
| **持久化** | SQLite + FTS5 + session management | SQLite 跨会话记忆 | Redis 缓存 |
| **UI** | Textual TUI（全屏多面板）| Streamlit | Next.js Web |
| **回测** | 完整引擎套件（多市场）| Walk-forward | Kubernetes 沙箱 |
| **测试** | 5,536+ | 63 | 未公开 |
| **Sandbox** | StaticSandbox + path whitelist | 无 | Kubernetes |

### 关键相同点
- 都用 SQLite 持久代理记忆
- 都实现多代理模式（strategy-research 有完整 Swarm；AgentQuant 有 ensemble voting）
- 都关注防过拟合（walk-forward validation）
- 都集成 LLM 推理 + 量化回测

### 关键差异
- strategy-research **远更成熟**（5,536+ 测试、460+ 因子、8 数据源、完整 TUI）
- AgentQuant A 聚焦**参数自我进化**（遗传算法优化自身配置）——strategy-research 没有
- AgentQuant B 是**完整产品**（Kubernetes 执行隔离）——版权风险高

---

## 可借鉴的模式

### 1. 参数自我进化（AgentQuant A）

**模式描述**：用遗传算法 + 差分进化优化代理自身的研究参数（模型选择、温度、策略权重等），跨 epoch 迭代改进。

**strategy-research 现状**：代理配置静态（`LoopConfig` / `AgentPlugin` 参数固定），无自动优化机制。

**可移植性**：
- **高**：`LoopConfig` 已支持 per-strategy 参数覆盖（`explorer.py` max_iter=50, no_progress=5）
- **需扩展**：添加 `StrategyEvolver` 类，跨 study 记录参数 → 性能映射，进化下一代配置
- **参考**：`core/swarm/presets/` 的 YAML 预设系统可作为参数 schema

### 2. 可证伪声明追踪（AgentQuant A）

**模式描述**：代理在回测前预测关键指标（Sharpe ratio），回测后验证预测准确率。追踪 86% 的预测一致性。

**strategy-research 现状**：
- `core/goal/` 的 `GoalStore` 有 `criteria` 和 `evidence`，但无"预测"字段
- `core/study/review_loop.py` 有 `parse_review_output` 但只做定性审查
- `core/study/runner.py` 的 `_check_stop_conditions` 只看实际指标 vs 目标

**可移植性**：
- **高**：在 `StudyRecord` 或 `GoalStore` 的 criteria 中添加 `predicted_value` 字段
- **实现**：reviewer agent 生成预测 → 回测后对比 → 存入 `objective_history` 或 journal
- **收益**：量化代理研究能力的可信度

### 3. Ensemble Voting（AgentQuant A）

**模式描述**：多个策略投票决定最终交易信号，减少单策略风险。

**strategy-research 现状**：`core/swarm/` 有多代理协作，但研究代理（researcher/evaluator）的决策是顺序的而非投票。

**可移植性**：
- **中**：study graph 已支持多节点并行（`StudyGraph` + `graph.topological_layers`）
- **需扩展**：添加 `voting_node` 类型，聚合多个 agent 输出后决策
- **参考**：`core/agent/builtin_tools/subagent_tool.py` 的子代理委派模式

### 4. Walk-forward 验证框架（AgentQuant A）

**模式描述**：train/validation/test 三段分割，防止过拟合，每段独立评估。

**strategy-research 现状**：`core/validation/` 有 walk-forward 实现（`cli/walk_forward.py`），但未集成到 study 生命周期。

**可移植性**：
- **已有基础**：`core/validation/walk_forward.py` + `core/validation/cli.py`
- **需集成**：study 的 `_check_stop_conditions` 可以检查 walk-forward 结果而非仅看单次回测
- **收益**：study 产出的策略更稳健

---

## 不建议采纳的模式

| 模式 | 原因 |
|------|------|
| Kubernetes 沙箱（AgentQuant B）| strategy-research 已有 `StaticSandbox` + path whitelist，K8s 过重 |
| Streamlit UI（AgentQuant A）| strategy-research 已有 Textual TUI + React WebUI |
| AGPL-3.0 许可（AgentQuant B）| 病毒式许可风险 |

---

## 依赖关系

**零**。strategy-research 无任何对 agentquant 的导入、引用或依赖。两个项目是独立的平行发展。

---

## 建议

1. **短期（1 周）**：调研 AgentQuant A 的 `claims_tracker` 实现细节，评估是否可作为 `core/goal/claims.py` 集成
2. **中期（1 月）**：实现参数自我进化（`StrategyEvolver`），利用现有 `LoopConfig` + `SwarmRuntime` 基础设施
3. **长期**：考虑 ensemble voting 作为 study graph 的新节点类型

---

*调研日期：2026-09-02*
*调研人：opencode*
*仓库：quantnodes-strategy-research*
