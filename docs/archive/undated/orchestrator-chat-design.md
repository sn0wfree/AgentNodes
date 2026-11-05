# 编排助手设计（Orchestrator Chat Design）

> 状态：定稿（2026-08-11，v3：复用 chat 会话体系 + 路由级 SSE 隔离）
> 前置：`docs/design/workflow-module-design.md`（DAG 定义 6 节点类型 + definitions API）、DAG 编辑器（`webui/frontend/src/components/workflow/WorkflowEditor.tsx`）、chat 会话体系（`routers/chat.py` + `core/agent/chat_loop.py` + `useSSE` 协议）
> 决策历史：
> - **v1**：一次性 SSE 端点生成完整 DAG。废弃。
> - **v2**：~~无状态专用端点~~ → **DAG 绑定的特殊 chat 会话**（`session_id = "dag:{definition_name}"`）：完整 agent loop + 完整上下文持久化，验证失败自动回传 LLM 自修复；增量循环；前端携带 DAG 快照；草稿自动保存。
> - **v3**（本次）：
>   - ~~自研 `OrchestratorChat` 420 行~~ → **复用 chat 全套**（`Composer`/`MessageList`/`ToolCallBlock`/`StreamingText`/`useSSE` + 全局 `chatStore.messages`），`OrchestratorChat` 退化为 ~110 行薄壳
>   - **路由级 SSE 隔离**：`AppShell` 在 `/dag` 路由时把 `currentSessionId` 设为 null（`useSSE` 不订阅 chat 流），编排薄壳单独订阅 `dag:{id}` ——**始终 1 条 EventSource**
>   - **ChatSessionContext** 注入：8 个 chat 组件从"读全局 currentSessionId"改为"读 Context，回落全局"——向后兼容（无 Provider 行为不变），同时为未来"主 chat + 编排同屏"铺路
>   - **副作用唯一出口**：`useDagStepApply` hook 用 `useChatStore.subscribe` 监听 `submit_dag_step` 工具完成 → applyDag + 自动存草稿。SSE handler 层零改动（10 个 handler 模块继续零 store import）。
>   - **修正 1**：`MessageList.tsx:29` 加 `session_id` 过滤（升 P0）——否则编排消息会污染主 chat 视图；同时修既存缺口（三处过滤行为不一致统一）。
>   - **修缺陷 1**：`OrchestratorChat` 用错 `es.onmessage`（应是 `addEventListener`）导致 SSE 全部死代码——薄壳化后用正确 API 自然修掉。
>   - **修缺陷 2**：`controlHandlers.clearAllStreamingParts` 在 immer 冻结对象外直接赋值会抛 TypeError（断线/取消/错误时防御失效）——改用 `updateMessage` 在 draft 内操作。
>   - **修缺陷 3**：文档 §3.2 修正："实现落点在 `core/agent/chat_loop.py:95-110`（按 `session_id.startswith('dag:')`），不在 `SessionService`。"
>   - **侧边栏隐藏 `dag:` 会话** + **persona 选择器在编排 session 锁定为 `workflow_orchestrator`**（保留按钮置灰）—— 防"切到研究员后 prompt 错乱"。
>   - **`Composer.readOnly?: 'image'|'all'`**：编排薄壳传 `'image'`，禁用附件按钮、slash command、拖拽上传。
>   - **`ToolCallBlock` 给 `submit_dag_step` 加图标/结果摘要**（`applied ? "已应用 · N 节点 / M 连线" : "N 个错误"`）。
>   - **`chat.py` `labels` 补 `workflow_orchestrator` 条目**——主 chat 受益（不再裸 id）。

## 一、目标与边界

**做**：
- 编排页（`/dag` → 编排 Tab）左侧聊天栏：输入任务 → 自动循环增量编排（每步 LLM 只改一处 DAG）→ 每步校验通过后应用到画布（入撤销栈）→ 直到 `agent_done` 或用户停止
- **校验自修复**：`submit_dag_step` 工具服务端校验，失败返回错误列表 → agent loop 回传 LLM 修正重试
- **刷新恢复**：聊天消息在 `dag:{id}` 会话持久化；画布草稿自动保存；刷新后两者一致恢复，可继续编排
- 6 节点类型限定：`llm_agent` / `planner` / `evaluator` / `approval` / `python` / `tool`
- 会话只挂一个工具 `submit_dag_step`（「其他都不干」）

**不做（本阶段）**：画布历史版本（仅撤销栈）、草稿多版本、合并追加（每步替换）、自动「保存定义」（由用户确认保存）、并发协同编辑（单用户，最后写者胜）。

**不改**：`SessionService` 核心循环、`WorkflowDefinition.validate()`、definitions CRUD、现有编辑器节点/保存逻辑、后端认证。

## 二、循环协议

```
用户输入 → 前端在消息尾部附加当前画布 DAG 快照 JSON
→ POST /api/chat/send_async { session_id: "dag:{name}", agent_id: "orchestrator", content }
→ AgentLoop（服务端，自动循环）:
    LLM(编排人设 + 当前DAG快照 + 用户输入)
    → 调用 submit_dag_step(dag_json)          # 每次只改一处（人设约束）
    → 服务端校验: 通过 → {applied:true, diff} ; 失败 → {applied:false, errors:[...]}
    → 错误回传 LLM 自修复，循环重试
    → 任务完成 → 不再调用工具，输出完成说明 → agent_done
→ 前端订阅 /api/chat/events SSE:
    工具调用事件 → 拿到 DAG → 前端 diff 摘要 → 应用画布(撤销栈) → 立即存草稿
→ 用户可继续发下一轮指令（携带最新画布快照），直到满意
```

- 无消息裁剪特例：编排会话按常规会话持久化；用户消息中的 DAG 快照段前端渲染时隐藏
- 停止：`POST /api/chat/cancel`（复用现有端点）；`max_rounds` 走会话级上限

## 三、后端

### 3.1 编排人设

`templates/.prompts/orchestrator.md`（`agent_id = "orchestrator"`，`PromptBuilderFactory` 按 role 加载，未知 role 回退默认——需注册）：
1. 唯一职责：增量编排 DAG；拒绝编排之外请求
2. 每次**只修改一处**（增/删/改节点或连线），输出完整新 DAG 并通过 `submit_dag_step` 提交
3. 节点类型限 6 类；`planner`/`evaluator`/`approval` 各最多 1 个；依赖无环
4. 校验失败 → 按返回的错误列表修正后重新提交（可多次）
5. 用户目标达成 → 停止调用工具，输出完成说明
6. 用户消息尾部的 DAG 快照 JSON 为「当前画布」，以它为基准修改

### 3.2 `submit_dag_step` 工具（`routers/workflow.py`）

- 入参 `dag_json`：`{nodes:[{id,type,label,config}], edges:[{source,target}]}`
- 校验：复用 `core/workflow/definition.py` 的 `WorkflowDefinition.validate()`（类型白名单、三类型各 ≤1、无环、id/edge 引用、节点数 ≤50 追加限制）
- 返回（工具结果，回传 LLM）：
  - 通过：`{"applied": true, "nodes": n, "edges": m, "diff": "新增 X、移除 Y…"}`
  - 失败：`{"applied": false, "errors": ["…"]}`（错误逐条可读，LLM 据此修正）
- 工具挂载：**实现落点在 `core/agent/chat_loop.py:95-110`**（按 `session_id.startswith("dag:")` 路由到编排工具集），**不在 `SessionService`**。`SessionService` 只透传 session_id 到 `build_chat_agent_loop`。会话级工具白名单无独立存储，靠 session_id 前缀路由（最轻）。

### 3.3 会话

- `POST /api/goal/workflow/orchestrate/session` `{dag_id}` → 返回已存在或新建的 `dag:{dag_id}` 会话 id
- `WebSessionCreate` 加可选 `id`（编排会话用固定 id；普通会话仍由服务端 uuid 生成）

### 3.4 草稿自动保存

- `workflows.db`（`_definition_workspace()` 下）加表：
  `drafts(dag_id TEXT PRIMARY KEY, nodes_json TEXT, edges_json TEXT, updated_at REAL)`
- 端点（`/api/goal/workflow/orchestrate/draft`）：
  - `PUT {dag_id, nodes, edges}` → upsert（覆盖旧草稿）
  - `GET /{dag_id}` → `{dag: {nodes, edges}}` 或 `{dag: null}`
  - `DELETE /{dag_id}` → 保存定义成功后清除
- 草稿与正式定义分离：草稿是编辑态，正式定义为已保存版本；打开定义时草稿优先显示

## 四、前端

### 4.1 布局（WorkflowEditor 内部分栏）

```
[编排聊天 w-72 可折叠] [节点库 w-56] [画布 flex-1] [节点配置 w-72]
```

- 聊天面板渲染在 `WorkflowEditor` 内部（无 forwardRef），闭包调用编辑器内部 `applyDag`/`autoLayout`/草稿保存
- 折叠：头部按钮收成 w-10 窄条

### 4.2 `dagSpec.ts`（新，纯函数，便于单测）

- `validateDag(nodes, edges)`：与后端同构预检（类型白名单 / 三类型 ≤1 / 环检测 / id 引用）
- `diffDag(prev, next)`：`{addedNodes, removedNodes, updatedNodes, addedEdges, removedEdges}`
- `sanitizeSpec(spec)`：id 清理去重、缺 label → `TYPE_META` 默认、补 `agentColor`、config 键按 `CONFIG_FIELDS` 白名单过滤

### 4.3 `OrchestratorChat.tsx`（新）

- 独立消息状态（**不复用全局 chatStore**——单会话模型会与主聊天页冲突）
- SSE 订阅：`GET /api/chat/events?session_id=dag:{name}`（EventSource + token query，复用现有协议）
- 渲染：用户消息（隐藏 DAG 快照段）/ 助手流式文本（`StreamingText` + `MarkdownRenderer`）/ 工具调用块（「第 n 步：提交 DAG 修改」+ 校验结果 ok/错误列表，仿 `ToolCallBlock` 视觉）
- 工具结果 `applied:true` → `diffDag` 摘要 → 调 `applyDag` → 立即存草稿 → toast
- 可停止（`POST /api/chat/cancel`）；空态示例 chips（自建简版，`QuickStartChips` 绑 session store 不可复用）

### 4.4 `WorkflowEditor.tsx` 改造

- 左聊天栏嵌入；`applyDag(spec)`：`sanitizeSpec` → 校验 → 替换画布 → 自动布局 → `pushHistory`
- **自动存草稿**：`rfNodes`/`rfEdges` 变化 debounce 2s → PUT draft（无聊天面板时同样生效——编辑即自动保存）

### 4.5 `DefinitionWorkflowPage.tsx` 改造

- 加载正式定义后 → GET draft → 有草稿则以草稿为画布 + toast「已恢复上次未保存的编辑」
- 「保存定义」成功 → DELETE draft
- 新建未命名定义：dag_id 用 `new:{当前展示名}`

### 4.6 `client.ts`

- `orchestrateSession(dagId)`、`saveDraft` / `getDraft` / `clearDraft`、`sendOrchestrate(sessionId, content, dagSnapshot)`

## 五、测试计划

| 层 | 用例 |
|---|---|
| 后端 pytest | `submit_dag_step`：合法 DAG / 非法类型 / 双 approval / 环 / 缺失引用 / 节点超限；会话创建与复用（`dag:` 前缀）；草稿 PUT/GET/DELETE（含覆盖更新） |
| 前端 vitest | `dagSpec.ts`：validateDag 各违规、diffDag 全分支、sanitizeSpec 清理/过滤；OrchestratorChat：mock SSE 事件流 → 流式渲染、工具 ok → 应用画布 + 存草稿、工具 errors → 显示错误列表 |
| 回归 | 全量 vitest（726+）+ tsc + vite build |

## 六、验收标准

1. `/dag` 编排 Tab：左聊天栏可折叠；编辑画布 2s 后自动存草稿；刷新后画布恢复 + 消息恢复，可继续编排
2. 发「把 X 拆解为 DAG」→ 自动循环逐步应用（每步 diff 摘要 + 撤销栈）→ `agent_done` 完成
3. 构造校验失败场景（如要求双 approval）→ 错误回传 LLM 自修后继续
4. 发无关请求 → 助手婉拒（人设）
5. LLM 未配置 → 现有 chat 端点错误路径提示
6. 「保存定义」成功后草稿清除；dark/light 两主题正常
