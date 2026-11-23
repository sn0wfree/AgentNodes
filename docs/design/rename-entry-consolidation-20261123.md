# 重命名入口合并方案（R-014 · 待批准 · 零代码）

> 日期：2026-11-23
> 性质：方案。批准后才动代码；本文不改任何实现。
> 触发：用户标注截图红框（顶栏会话标题区），反馈「与侧边框功能重复」。

## 1. 事实核查（已自证，无需用户回答）

| 项 | 顶栏（TopBar.tsx） | 侧栏（SessionSidebar.tsx） |
|---|---|---|
| 位置 | L42–100：`/` + title + Pencil 按钮 | L83–94 逻辑 + L373 Edit3 hover 按钮 |
| 编辑形态 | 点击 → input 替换标题（`maxLength=80`） | hover → 行内 input 替换条目名 |
| 提交 | Enter / onBlur → `updateSessionMeta(id,{title})` | Enter / onBlur → 同一 API |
| 取消 | Escape → 不改 | Escape → 不改 |
| 作用范围 | **仅当前会话** | **任意条目**（传 `sess.id`） |
| 可发现性 | 常驻可见、键盘可达、窄屏可用 | hover 才出现，窄屏难触达 |
| 测试 | 无直接重命名用例 | `sessionSidebar.test.tsx` 2 条（L123/L139） |

**结论**：两处是**同一操作的两个独立实现**（各约 40 行 + 各自 state），重复的是实现与交互，不是信息本身。顶栏标题是「当前上下文标识」，侧栏条目是「导航态」，两者保留（与 ChatGPT/Claude 一致）；**重复的是重命名操作**。

## 2. 合并目标

1. **单一实现**：重命名逻辑只在 TopBar 存在一份（删除 SessionSidebar 的 `editingId`/`editValue`/`editRef` + `handleStartEdit`/`handleCommitEdit` + 行内 input 分支）
2. **能力不丢**：侧栏 hover 的重命名按钮**保留**，但行为改为「先切换到该会话，再让顶栏进入编辑态」
3. **窄屏改善**：折叠侧栏时点重命名 → 自动切会话 + 顶栏编辑（不再依赖侧栏内的窄输入框）
4. **测试调整**：删除 2 条侧栏内联重命名用例，新增 1 条「hover 重命名切换到目标会话并触发顶栏编辑」用例

## 3. 实现设计

### 3.1 跨组件触发机制

在 layout store 增加一次性意图信号（不引入新的全局状态机）：

```ts
// stores/layout.ts
renameRequestId: string | null      // 目标会话 id
requestRename: (id: string) => void // set({ renameRequestId: id })
clearRenameRequest: () => void      // set({ renameRequestId: null })
```

- `SessionSidebar` hover 按钮 `onClick` → `switchSession(sess.id)`（若非当前）+ `requestRename(sess.id)`
- `TopBar` `useEffect` 监听 `renameRequestId`：若等于 `currentSessionId` → `startEdit()` + `clearRenameRequest()`
- 信号消费后即清理，不残留；重复点击同一会话也可再次触发（清理后再 set）

**备选（更简单但耦合）**：TopBar 暴露 imperative handle 经 context 传给 Sidebar——不采用，layout store 信号更符合现有架构（两组件已共用 layout store）。

### 3.2 删除范围（SessionSidebar）

- import 移除 `Edit3`（若 hover 按钮改用 Pencil 图标则保留图标 import，按视觉一致性选 Pencil，与顶栏统一）
- state：`editingId` / `editValue` / `editRef` 及相关 `useEffect`
- 函数：`handleStartEdit` / `handleCommitEdit`
- `SessionItem` props：`editing` / `editValue` / `editRef` / `onEditChange` / `onEditCommit` / `onEditCancel`
- 行内 input 分支（L308 附近）
- hover 的 Edit3 按钮保留，onClick 改为上述触发逻辑（title 仍为「重命名」）

### 3.3 TopBar 改动

- 新增 `renameRequestId` 订阅 + `useEffect` 触发 `startEdit`
- `startEdit` 改用传入 id（当前仅 `currentSession`，保持一致即可）
- 其余（input/Enter/Escape/onBlur）不动

## 4. 影响面与风险

| 风险 | 处置 |
|---|---|
| 切换会话是异步（switchSession 含 loadMessages/loadSessionState）， renameRequestId 可能在 currentSessionId 更新前被消费 | TopBar effect 以 `currentSessionId === renameRequestId` 为条件，自然等待切换完成；不额外加超时 |
| 用户点非当前会话的重命名但切换失败（404） | 信号仍驻留但条件不满足，不会误改别的会话；下次成功切换再消费——可接受，或 clear on 切换失败（实施时按测试覆盖决定） |
| 窄屏侧栏收起后点重命名 | switchSession 里已 `setSidebarOpen(false)`；顶栏编辑可见 ✓ |
| 删除侧栏内联编辑后「不切会话就地改名」的体验丢失 | 这是有意的合并代价（统一入口）；如用户要求保留，改为「不删侧栏编辑、只统一 state 到 store」的更温和方案 |

## 5. 验收标准

1. `sessionSidebar.test.tsx` 原 2 条内联重命名用例删除；新增 1 条：点非当前会话的「重命名」→ `switchSession` 被调用 + layout store `renameRequestId` 置位
2. `TopBar` 新增用例：store 置 `renameRequestId===currentSessionId` → 编辑态 input 出现；提交后 `updateSessionMeta` 调用一次；信号清零
3. 现有全量前端测试保持绿（基线 1357）
4. 双主题视觉基线：侧栏 hover 按钮图标从 Edit3 改为 Pencil（视觉近等价），需重录受影响 baseline（`session_sidebar`/`command_palette` 等）
5. 实机：窄屏（320px）下从侧栏点非当前会话重命名 → 顶栏输入框可编辑

## 6. 回滚

单笔 revert 本批代码 + 基线提交即可（改动集中在 3 文件 + 测试 + baseline）；layout store 新增字段若后续无用可随之删除。无后端/数据迁移。

## 7. 不做

- 不合并「标题显示」本身（顶栏=当前上下文，侧栏=导航态，业界均保留）
- 不改后端 `updateSessionMeta` 契约
- 不动 tag/星标/归档/搜索/时间分组（上一轮已评估：tag 是侧栏内的半截功能，属另一个议题）
- 不改顶栏 input 的视觉与键盘行为（已达标）
