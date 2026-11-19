# 待批准方案集：步骤 6（兼容迁移与删除公告）+ 步骤 4c（隔离生产化）

> **性质**：R-014 方案先行 · **状态**：待维护者批准 · **日期**：2026-11-19
> **背景**：七步路线步骤 1-5 已全部完成（详见兼容审查 §10）；余下步骤 4c
> 与步骤 6 均涉及不可逆变更（生产接线 / 旧 API 删除），必须单独审批。

## A. 步骤 6：兼容期公告 + 外部迁移接口 + 删除期限

### A1. 事实基础（来自兼容审查 §2/§6 + 步骤 5）

- 旧队列 API 共 9 个出口（`SessionService.send_message` / `cancel` /
  `cancel_session` / `resume_queue` / `wait_for_attempt` /
  `is_session_processing` / `mark_session_processing` /
  `list_active_attempts` / `get_available_actions`），**src 生产调用全 0**；
- `send_message` 已由裁决 1 定为兼容 API（保留 + policy 注入能力 + drift 测试）；
- Gateway 对应物已全部落地（`api_chat_request` + `cancel_session` /
  `resume_session` / `wait_turn` / `chat_session_state` / `attempts_query`）；
- R-016 出口网 14/14 有对应面与测试；2 项专项断言列删除批补测；
- 重导出链 2 处（`kernel/session/__init__.py:62` /
  `interfaces/api/session/service.py:14` 的 `_CHAT_ATTEMPT_TIMEOUT`）；
- repo 有 `CHANGELOG.md`，无 `docs/migration.md`。

### A2. 公告内容（三处发布）

1. **CHANGELOG.md** 新增 `## Deprecated` 段：列出 9 个旧出口的 deprecated
   状态、对应新入口、期限（下一版本删除）；声明 `send_message` 兼容承诺
   在期限内不变。
2. **新文档 `docs/guides/legacy-chat-api-migration.md`**：逐出口迁移表
   （旧调用 → 新入口代码示例）、语义差异说明（wait_turn 会话代际语义 ≠
   指定 attempt 等待、STEER 单次通知、超时能力声明四级）、仓库外消费者
   自查方法（动态 import / entry_points / 反射）。
3. **兼容审查文档 §6** 从「需维护者确认」改为「已定稿」并链接上述两处。

### A3. 期限与删除形式（需裁决点）

- **期限建议**：「下一版本」——与批 2 死代码、3a.4 过渡面、seq_generator
  同批删除，避免多批次公告。
- **删除形式**：R-014 方案先行 + R-015 四项 + R-016 出口网 + R-020 独立
  最后批次；独立提交、独立回滚；删除前后各跑全量 + batch_gate。
- **删除批内容**：9 个旧出口 + 2 处重导出 + 旧 helper（3a.4 DEPRECATED
  面）+ seq_generator（顺序⑥审计退役候选，4 触点同步后）+ 2 项补测
  （entry_rejected 400 / legacy send_message 专项）。

### A4. 风险与回滚

- 仓库外消费者无法证明为 0（静态扫描盲区：动态 import / entry_points /
  反射）——公告与自查指南是唯一缓解手段；
- 删除批独立提交 → 回滚即恢复旧 API；公告文档可单独回滚。

**待确认**：①「下一版本」期限是否接受？② 三处公告形态（CHANGELOG +
新迁移指南 + 审查 §6 定稿）是否接受？③ 删除批是否合并 seq_generator /
3a.4 过渡面，还是各独立批？

## B. 步骤 4c：隔离生产化（真实工具 + 上下文 + POSIX）

### B1. 事实基础（超时设计 §9.7 原型结论）

- 原型已验证 5 实验（启动/关停、上下文数据字段、权限 IPC 往返、子进程树
  终止、截止+lease 确认退出后释放），全部 PASS；
- 环境约束：宿主处于受限 Job → 本主机验证的是 `taskkill /T` 树终止降级；
  生产 uvicorn 预期可用 Job Object（需部署时验证）；
- 未验证：POSIX（未实现）、`data_store`/`sandbox` 跨界、真实 BaseTool
  工具、breakaway 子进程；
- 不必然解决 sync-only 客户端 xfail（工作线程桥不在进程隔离范围）。

### B2. 生产化方案（分层）

| 阶段 | 内容 | 前置 |
|---|---|---|
| B2.1 工具准入 | 给 `BaseTool` 增 `isolatable` 声明的准入门槛文档：必须通过原型五实验类别核对；默认仍 `unstoppable` | 4a 能力声明已落地 |
| B2.2 进程池与 worker | 实现 kernel 侧 `IsolatedToolRunner`：spawn worker（Job Object 首选 / taskkill 降级 / POSIX `setsid`+`killpg`）、JSON Lines IPC、审批回调代理、结果回收 | 平台验证（Windows 已测 / POSIX 需 CI） |
| B2.3 ToolContext 跨界 | 可序列化字段直传；emit/permission/data_store 走 IPC 代理；sandbox 配置子集传递 | B2.2 |
| B2.4 deadline 接线 | attempt 截止 → 终止 worker 树 → 确认退出 → 拒收迟到结果 → 才释放 lease（原型 E5 序列生产化） | 4a 实际状态事件面 |
| B2.5 真实工具试点 | 选 1-2 个同步工具（如 file_write / shell）过五实验清单，验证副作用边界与失败传播 | B2.1-B2.4 |

### B3. 明确不做

- 不宣称所有工具可强中断——远端已接受操作需服务端确认取消（§10.4）；
- 不把隔离原型当作生产可用（演示替身 ≠ 真实工具）；
- 不解决 sync-only 客户端线程 xfail（范围外）；
- 不改超时四级能力语义（4a/4b 已落地）。

### B4. 验收与回滚

- 每阶段独立 commit、独立回滚；B2.5 前不接生产路径；
- 验收：真实工具过五实验清单 + POSIX CI + E5 序列测试 + 两个既有 xfail
  保持（不误报消除）；
- 回滚：逐阶段撤回，恢复现有进程内工具执行。

**待确认**：① 是否按 B2.1→B2.5 分批实施？② POSIX 支持是否本轮范围（需 CI）？③ 试点工具选哪 1-2 个？④ 4c 是否接受"不解决 sync-only xfail"的边界？

## C. 与现有裁决的衔接

- 步骤 6 不违反裁决 1（send_message 兼容面期限内不变）；
- 步骤 4c 不改 4a/4b 已落地的能力声明与事件面；
- 两者都不触碰 legacy 旧队列的**实现**，直到步骤 6 删除批获批。

## D. 维护者裁决记录（2026-11-19）

> 用户原文要点：批准步骤 6 的三处弃用公告/迁移指南，但删除仍需单独审批；
> 兼容期至少一个完整正式版本周期；旧队列与 3a.4 可协同但独立提交，
> seq_generator 单独批次。4c 按 B2.1→B2.5 分阶段、逐阶段验收；POSIX 在
> 跨平台生产启用前必须验证；首个真实试点限临时工作区 `file_write`，
> shell 暂缓；sync-only xfail 明确留在范围外。

| 项 | 裁决 | 执行状态 |
|---|---|---|
| 步骤 6 · 三处弃用公告 | ✅ **批准并已执行**：CHANGELOG `## Deprecated` + `docs/guides/legacy-chat-api-migration.md` + 兼容审查 §6 定稿 | ✅ 本日落地 |
| 步骤 6 · 兼容期 | ≥ 一个完整正式版本周期 | ✅ 写入公告 |
| 步骤 6 · 删除 | ❌ **未批准**——须单独审批；旧队列与 3a.4 协同但独立提交；seq_generator 单独批次 | ⏸ 不执行 |
| 步骤 4c · 分阶段 | ✅ 按 B2.1→B2.5 分批、逐阶段验收 | ⏸ 未开始（待逐阶段批准） |
| 步骤 4c · POSIX | 跨平台生产启用前必须验证 | ⏸ 未验证 |
| 步骤 4c · 首个试点 | 限临时工作区 `file_write`；**shell 暂缓** | ⏸ 未开始 |
| 步骤 4c · sync-only xfail | 明确留在范围外 | ✅ 已记录 |
| examples 陈旧路径 | **先独立审计**（发现不止两处旧路径：多个旧 import/类名失效，其他 examples 类似），再决定更新或归档 | ⏸ 待审计 |

**用户不可用，未取得进一步批准；除三处公告外，代码和文档均未改动。**

（三处公告文档已落地；其余为方案 + 裁决记录，零代码改动。）

## E. 步骤 4c · B2.1 执行记录（2026-11-19）

按裁决启动 B2.1（工具准入），**零执行路径变化**：

- `kernel/agent/tools/core.py` 新增 `check_isolation_admission(tool)`：
  声明 `isolatable` 且 effects 含 `db`/`net` → 拒绝（进程终止不能撤销
  已受理的远端/DB 操作，需服务端确认取消——契约 §10.4 尚未实现）；
  仅 FS/只读 → 允许。首试点限临时工作区 file_write，shell 暂缓。
- `ToolRegistry.timeout_capability(name)` 用准入门槛兜底：不合规的
  isolatable 声明回落 `unstoppable`（诚实默认）。
- `tests/test_tool_isolation_admission.py` 6 用例覆盖四级别准入与 registry 回落。

**验收**：6 passed；层方向 / 门禁 / 全量按批次验证。
**边界**：sync-only xfail 在范围外；POSIX 在跨平台生产启用前验证；
本阶段不接生产隔离执行路径（B2.2 起）。
