# QuantStudio-quantskills 调研 — 可借鉴项

> 2026-09-29 · 对象: `D:\Github\QuantStudio-quantskills`  
> 对照: `quantnodes-strategy-research`（AI Agent 量化研究框架）  
> **许可: GPL-3.0-or-later / PandaAI 商业双授权 — 只借思想，不抄代码**

---

## 1. 它是什么

| 项 | 内容 |
|---|---|
| 定位 | AI 量化工作台（Quant·Work·Trade），DSH 插件 monorepo |
| 栈 | TypeScript + pnpm，Node 22+，DeepSeek Harness 0.1.2 |
| 真正 IP | `assets/library-v2/`：15 技能 + 44 专家 + 14 专家团（含 Python 评估脚本） |
| 不是 | Python 量化引擎 — 与我们的引擎层不重叠，重叠在 **研究方法论** |

---

## 2. 与我们对比

| 维度 | QuantStudio | 我们 | 该借 |
|---|---|---|---|
| 因子评分 | **primary_score v2 归一加权** | 6 维评分 | 公式与防刷分治理 |
| 回测验收 | **5 项体检 + 反模式** | metrics 合同 | 体检 gate |
| 前向收益 | `open.shift(-1)` 无未来函数 | 有实现 | 口径核对 |
| 研究交付 | output_contract + 17 图 + 结论枚举 | 散落 | 产物契约 |
| 因子衰减 | 指数/幂律 + 半衰期 | IC 衰减简单 | 模型族 |
| 多 Agent | mailbox + 任务 DAG + revision | Swarm DAG | 任务板 CAS |
| 写操作 | Plan+snapshotHash+人审 | permission 层 | 生命周期 |
| 工具面 | Skill 打包规范 | templates/.skills | 目录契约 |

---

## 3. 高价值借鉴（重写实现）

### B1 因子主分 v2（防「刷 IC 不顾回撤」）

```text
score = 0.20*clip(IC_IR/3)
      + 0.30*clip((Sharpe+0.5)/1)
      + 0.30*clip(ann_ret/0.10)
      + 0.20*clip(1+MDD/0.30)
      + 0.10*clip(mono,-1,1)
      + 0.10*(-clip(ann_turnover/30-1,0,3))
```

- 各分量归一到 ~[-2,2]，权重即重要性
- **铁律**：项目内只有一个 `primary_score()`，改公式=历史评分不可比
- 落地：`factor_validate.compute_overall_score` 升级为 v2

### B2 回测健康体检（5 项 gate）

| 检查 | 期望 | 失败含义 |
|---|---|---|
| 持仓股数 | ≈ TopX% | universe/涨跌停问题 |
| 换手 | ≈ 2/H | 信号不稳 |
| Sharpe/IC_IR | 0.3~0.5 | 时序/成交价 bug |
| MDD | -20%~-40% | **过浅≈ look-ahead** |
| 全仓时间 | >80% | 信号 NaN |

+ 危险信号：年化>50% 且 MDD<5% → **先查 bug 不是庆祝**  
落地：`strategy_acceptance` / `validation` 增加 health_check

### B3 结论标签枚举

`effective | weakly | ineffective | regime-dependent | inconclusive`  
落地：Study claims / 因子卡 / 验收报告统一用词

### B4 研究产物契约

`output_contract`：目录规范、必备图（净值/回撤/分组/IC 衰减…）、基线对照  
（反向 / 定种子随机 / 等权买入持有 / 全平）  
落地：Study round 交付物 schema

### B5 因子衰减模型族

指数 / 双指数 / 幂律 / 非参数 + 半衰期；R²<0.5 降级  
落地：扩展 `factor_validate` IC 衰减

### B6 团队任务板

`blockedBy` DAG + 环检测 + **revision 乐观锁** + writeScopes 声明  
落地：Swarm/Study 任务协作（对齐我们 A2 toolset 与计划中的 A6）

### B7 写操作 Plan 生命周期

`prepared → executing → completed` + snapshotHash + 过期 + 人审  
落地：与 `strategy_acceptance` / permission 对齐

### B8 指标唯一真理源

禁止 agent 自写近似评分函数；runner 只读一个 `score`  
落地：工具层只暴露官方指标 API

---

## 4. 不借 / 不适用

| 项 | 原因 |
|---|---|
| TS/DSH 插件运行时 | 与我们 Python Agent 栈不兼容 |
| PandaData OAuth / 赛事 CLI | 绑定其平台 |
| UI 三栏壳 | 我们有 React SPA |
| **任何源码拷贝** | **GPL 传染**；我们 MIT |

---

## 5. 建议落地顺序

| 优先级 | 项 | 理由 |
|---|---|---|
| 1 | B1 primary_score v2 + 单源铁律 | 直接提升因子验收质量 |
| 2 | B2 health_check gate | 防 look-ahead / 假回测 |
| 3 | B3+B4 结论枚举 + 产物契约 | Study 可交付、可审计 |
| 5 | B5 衰减模型 | 因子卡增强 |
| 6 | B6/B7 任务 CAS + Plan 审批 | 多 Agent/写操作治理 |

**A4（Memory retriever）实现进行中**，与上表可并行或先收尾。

---

## 6. 一句话

> QuantStudio 强在 **研究纪律**（评分防刷分、体检防假回测、产物契约），不在引擎。  
> 对我们价值最高的是 **B1+B2：把「怎么算分、怎么信回测」写成不可绕过的单源规则**。
