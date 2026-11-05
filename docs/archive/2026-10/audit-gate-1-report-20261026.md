# 审计门 ① 报告 — 阶段 2 末尾（2026-10-26）

> 按 replan §5：阶段 2 末尾审计门，检查正确性机器是否被绕过、契约是否被绕行、依赖方向是否腐烂。
> **结论：发现 1 个 HIGH（已修复）+ 2 个 MEDIUM（已修复）+ 3 项检查通过。**

---

## 1. HIGH：llm 节点绕过预算检查（已修复）

| 项 | 内容 |
|---|---|
| 症状 | `llm.py:26` 直接 `client.chat(messages)`，**完全没有预算检查**；react 节点每迭代有检查 |
| 危害 | 两条 LLM 路径两种预算姿态——chat 类会无限花预算（正是 pre-mortem 里的「控制面绕行」模式） |
| 根因 | 预算检查实现在 react 内部，llm 节点独立写时没人约束 |
| 修法 | 抽出 `core/runtimes/run_budget.py`：`plan_call()`（调用前唯一检查点）+ `record()`（记账）；两个节点统一使用；分层复用 `strategy/budget.py` 的记账与三维判定，不复制逻辑 |
| 防复发 | **注册 INV-9**：结构性检查——runtimes 下凡含 `client.chat(` 的模块必须同时含 `plan_call()`。这条检查器**本来就能抓住原 bug** |

## 2. MEDIUM：`gateway.workers` 钉死可被绕过（已修复）

| 项 | 内容 |
|---|---|
| 症状 | ConfigHub 的文件层/env 层会拒绝 `workers != 1`，但**直接构造** `GatewayConfig(workers=4)` 成功 |
| 危害 | 多 worker = 多 Gateway = mailbox/状态机分裂——这是 INV-1 的核心承诺，之前只是「装载路径的约定」 |
| 修法 | `GatewayConfig.__post_init__` 抛 ValueError——**任何构造路径**（直接构造、replace、文件、env）都失败 |
| 发现方式 | 写 INV-1 检查器时执行 `GatewayConfig(workers=4)` 发现它不抛 |

## 3. MEDIUM：executor 的 cancel 会杀死空闲池（已修复）

| 项 | 内容 |
|---|---|
| 症状 | `cancel()` 用 `_busy()` 判断（结果队列空=忙）——**空队列被误判为忙**，无在飞任务时也硬杀全部 worker |
| 危害 | 一次 cancel 后池变死，后续任务全部超时 |
| 修法 | 改为 `_in_flight` 显式计数；无在飞任务只设标志不动 worker；新增 `ensure_alive()` 在被硬杀后自愈重建 |

## 4. 通过项（无问题）

| 检查 | 结果 |
|---|---|
| `core/gateway` 零 FastAPI import | ✅ 无命中 |
| `core/orchestration` 零实现层 import（不依赖 gateway/runtimes/agent） | ✅ 无命中 |
| core 零 `api.` 反向依赖 | ✅ 无命中 |
| 无绕过 fence 直接写 sqlite | ✅ gateway/runtimes 下无 write 路径 |
| 契约绕行（是否有代码绕过 EntryPoint 直调内部） | ✅ 切片与测试全部经 EntryPoint；`controller._build_engine` 注入点已标注为「1b 演示，正式接线在 A1」 |

## 5. 遗留到后续刀（不是本次问题）

| 项 | 移交给 |
|---|---|
| `controller._build_engine` 的演示级 monkey-patch | A1（preset 注册表落位后改为正常构造路径） |
| T3 第二段沙箱（WSL2 bubblewrap+seccomp） | P3 前 |
| Windows Job Object 资源上限 | A1 平台层 |
| worker 断网手段（防火墙规则） | A1 实测 |

## 6. 状态

- 全量：**144 passed / 1 skipped**
- live（真实 MiniMax）：**13 passed**
- 新增不变量：INV-9（LLM 调用必须过预算）
- 不变量总数：5 条（INV-1/3/5/8/9）

**审计门 ① 通过，准予进入阶段 3a。**
