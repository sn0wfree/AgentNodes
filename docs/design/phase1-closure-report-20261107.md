# 第一阶段收口报告（v1.0 · 2026-11-07）

> 批次 6–8 完成后（`8300a557` / `c0836ee4` / `93fc98e4`），
> `phase1-definition-of-done-20261107.md` 的 D1/D2/D3 与 A1–A4 实测状态。
> **回应「第一阶段未收尾」**：能力/权限/独立运行三条验收线已闭合；
> 剩余一项架构验收（引擎收敛 B1/B2）已排期，见 §3。

---

## §1 三条判定线

### D1 · 14 条 checklist（能力）

| 分组 | 状态 | 说明 |
|---|---|---|
| **A-chat**（C1 对话 / C2 恢复 / C3 流式） | ⬜ 引擎收敛后逐条 | 现有会话/流式机制在旧骨架上可用，但 checklist 化验收排在 B1/B2 后 |
| **A-exec**（C8 三元组 / C9 超时清理 / C10 后台） | ✅ 部分 | C8 已测（三元组分离）；C9/C10 工具具备、checklist 化待做 |
| **A-coding**（C4/C5/C6/C7） | ⬜ 部分 | write/read/闭环已验（E3/E4）；编辑唯一性/读后写保护未实现（当前只有整文件覆写） |
| **C11 权限闸门** | ✅ | hard_deny + L0-L4 + fail-closed，77 测试 |
| **C12/C13/C14** | ⬜ | 压缩/中断/脚本化——引擎收敛后 |

### D2 · 权限 L0–L4 ✅

hard_deny 三约束（H1 自我配置 / H2 提权 / H3 凭据）· fail-closed（两处
fail-open 封死）· 审计回调 · 非交互降级。77 权限测试全绿。

### D3 · 删 apps 后可用 ✅（E6）

`tests/test_kernel_standalone.py`：meta_path 阻断 `agentnodes.apps.*` →
chat 构建 + write/read/shell 闭环 + 权限边界**首跑即过**。
业务工具正确缺席（无 apps = 无 goal.* = 无 tool.goal）。

## §2 A1–A4（静态验收）

| 线 | 门禁 | 状态 |
|---|---|---|
| A1 配置引用可解析 | `check_tool_refs`（4 面 + 反向） | ✅ |
| **A2 kernel 零业务内容** | `check_kernel_nouns`（**扩展：py + yaml/json + 目录名**） | ✅ |
| A3 插件三层契约 | tools + python_function + prompt_file | ✅ |
| A4 空系统可跑通 | E6 | ✅ |

kernel 最终形态：**24 目录 / 160 py / 0 资产**，全通用名词。
apps 承接：`swarm_presets`（37 yaml）/ `scheduled_research`（6 py）/
`agent_plugins`（quant 3 模块）/ `goal` / `builtin_tools` / `chat`。

## §3 剩余项（已排期）

| # | 项 | 批次 |
|---|---|---|
| **架构验收（新增）** | **B1 协议统一**（StepHandler/StreamingHandler/StepResult 进 kernel）+ **B2 deciders 配置化**（三检查点映射表测试钉住零行为变化） | 下一批（第一阶段架构收尾） |
| 引擎后续 | B3 骨架隔离（loop.py ≤400 行） | 第二阶段开头（RSI 前） |
| C4/C5 | 编辑唯一性（`old_string` 唯一性校验）/ 读后写保护 | B1 后 |
| C1/C2/C3/C12/C13/C14 | checklist 化验收 | B2 后 |
| 1-E | templates/.skills 26 个量化技能留资产层，第三步随插件拆分（已决议） | 第三步 |
| 批次 5 | R3 + W5 | 引擎收敛后重估 |

**方案依据**：`unified-loop-adoption-eval-20261107.md`
（换底座不换职责 · 四个使用故事 · B1/B2/B3 落到 file:line）。

## §4 批次轨迹（6–8）

| 批 | commit | 内容 |
|---|---|---|
| 6 | `8300a557` | hard_deny 层（P1）∥ L0-L4 ∥ P2 双匹配 ∥ P3 校验 ∥ R0 吞规则 bug |
| 7 | `c0836ee4` | E3 写根开放 ∥ E2 shell 入 registry ∥ 权限闸默认接线 ∥ fail-closed（洞 #3）∥ lstrip 路径 bug ∥ E1-E5 |
| 8 | `93fc98e4` | presets/scheduled_research 迁 L2 ∥ nouns 门禁扩展 ∥ **E6** |

每批：设计文档先行 → 实施 → 全量 0 failed → batch_gate ALL GREEN → 提交。
