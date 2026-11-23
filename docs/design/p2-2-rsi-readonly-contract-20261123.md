# P2-2 RSI 进化视图：只读契约骨架

> 日期：2026-11-23
> 性质：契约骨架草案（P2-2 起步）；只有数据 schema 与缺态语义，不承诺 UI 排期。
> 依据：[U-A 执行计划](./ui-ua-frontend-execution-plan-20261122.md) §7 P2-2、§9 C2、
> [侧边栏业界调研](./ux-side-panel-industry-survey-20261123.md) P10 入口诚实性、
> G1 已完成闭环（`apps/evolution/cycle_driver.py` 636 行 / `kernel/evolution/*`）。

## 1. 现状（已核实）

- **后端闭环是真**：`apps/evolution/cycle_driver.py`（G1 后 emit/seed 显式 port）、
  `kernel/evolution/` 12 模块：genome_proposals（GenomeProposal dataclass）/
  validation_gate（ValidationResult, GenomeVersionStore）/ golden_eval（GoldenEvalResult）/
  holdout（Trajectory, HoldoutSet, EventStoreReportSource）/ skill_memory（EffectiveHarnessConfig）
- **UI 契约为零**：`app.py` 无 evolution 路由挂载；前端无组件；无鉴权边界审查
- **结论**：引擎有、数据有，但**没有面向 UI 的读取端点**——不得把「代码目录存在」当 UI 接口存在（P10）

## 2. 只读契约骨架（建议 schema，待后端确认）

设计原则：**只输出服务端真实字段；缺字段 = 显未知，不补零、不造数**。

| 域 | 候选字段（来源模块） | 缺态语义 |
|---|---|---|
| 信号健康 | 活跃信号源、采样时间窗、新鲜度（需确认来源） | 未采样 → 「未采样」；无权限 → 「无权限」；加载失败 → 「失败」 |
| 轮次/闭环阶段 | proposal id + ProposalStatus（genome_proposals）、ValidationResult.status（validation_gate） | 无轮次 → 「暂无轮次」；阶段未知 → 「未知」 |
| 评分与 Δ | GoldenEvalResult / ValidationResult 的分数字段（**单位与方向待确认**） | 未评分 → 「未评分」；Δ 缺基线 → 「无基线」 |
| 证据流 | HoldoutSet / Trajectory / EventStoreReportSource 的证据引用 | 无证据 → 「无证据」；脱敏后展示 |

**必采样/错误/无权限三态必须可区分**（计划 §7 P2-2.2）。

## 3. 实施顺序（P2-2，全部待批准）

1. **契约先行**：后端提供只读端点（路径/字段/单位/时间窗/权限），此处只定 schema 形状——无端点前不建 UI
2. **字段映射验证**：用真实 evolution 运行数据核对 §2 每列是否可由服务端给出；缺字段要么后端补、要么 UI 显未知
3. **只读视图**：按示意设计信号健康 / 轮次阶段 / 评分证据流；红黄绿只用于健康语义，分数变化须带来源与含义
4. **动作（启动/中止/重试）**：仅在操作契约 + 权限齐备后加入；副作用与失败恢复需审计（P2-2.4）
5. **验收**：真实数据验证取值、刷新、筛选、权限、空态、响应式；离线 fixture 只做组件测试

## 4. 边界与不做

- 图标/一级导航：P2-2 完成前 **RSI 不进导航**（P10；与本次图标精简一致）
- 不把 mockup 第三视图当功能承诺
- 不新增/恢复业务后端（需在 §7 的产品与后端负责人确认归属后另案）
- 不展示假分数、假信号、假进度

## 5. 开放问题（需负责人确认）

- 信号源定义、评分单位/方向/时间窗、证据字段的安全边界
- 权限模型：谁能看 RSI 数据？（沿用会话归属还是独立 role）
- 是否复用 `/api/system/*` 的既有模式，还是单开 `/api/evolution/read/*`
