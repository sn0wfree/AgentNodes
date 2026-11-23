# 移除顶栏 SessionTabs（多会话标签条）方案（R-014 · 待批准 · 零代码）

> 日期：2026-11-23
> 性质：方案。批准后才动代码；本文不改任何实现。
> 触发：用户明确——「顶栏标题下面有三个新会话的 tab，这 tab 感觉很无用，旁边的加号也无用，所有新建/切换 session 功能都由会话侧栏承载」。

## 1. 事实核查

| 项 | 实现 | 处置判断 |
|---|---|---|
| SessionTabs 组件 | `components/chat/SessionTabs.tsx`（70 行）：渲染 `openSessionIds` 对应的会话标签 + 星标 + 关闭按钮 + 末尾「新建」加号 | **删除整个组件** |
| 挂载点 | `components/layout/MainSplit.tsx:3,9`（顶栏下方的 tab 条） | **删除挂载**，省一行 40px 高 |
| 侧栏能力 | `SessionSidebar.tsx`：会话列表（今天/7天/更早）+ 搜索 + 新建会话 + 星标/归档/删除 + 点击切换 | **已完整覆盖** SessionTabs 的全部用户能力 |
| `openSessionIds` 状态 | `stores/session.ts:34`，被 switchSession/createNewSession/openSession/closeSession 维护 | **保留**（AppShell 初始化恢复 + 快捷键用；是「已打开集合」而非 UI 专属） |
| `closeSession` 状态方法 | `stores/session.ts:340-360`：关闭 tab 并切到相邻会话 | **保留**（快捷键 Cmd+Shift+W 用；语义「关闭当前并切邻居」仍有意义） |
| 键盘 `Cmd+1..9` | `useKeyboardShortcuts.ts:37-45`：按 `openSessionIds[i]` 切换 | **保留**（无 tab 也可按序号切换，与浏览器标签栏习惯一致） |
| 键盘 `Cmd+Shift+W` | `useKeyboardShortcuts.ts:49`：关闭当前会话 | **保留** |
| AppShell 初始化 | `AppShell.tsx:72`：`validOpenIds = openSessionIds.filter(...)`，恢复已打开会话 | **保留**（决定是否恢复 last session；删状态会改变启动行为） |
| 测试 | `test/SessionTabs.test.tsx`（7 条） | **删除整个测试文件** |

## 2. 实施范围（批准后）

1. 删除 `webui/frontend/src/components/chat/SessionTabs.tsx`
2. `MainSplit.tsx` 移除 import + `<SessionTabs />` 挂载
3. 删除 `webui/frontend/src/test/SessionTabs.test.tsx`
4. `stores/session.ts` / `useKeyboardShortcuts.ts` / `AppShell.tsx` **不改**（状态与快捷键仍有意义，且改动会扩大风险面）
5. 视觉基线重录：顶栏下方少一条 40px tab 条 → `message_bubble` / `user_bubble` / `assistant_message` / `empty_chat` 等主会话基线会变化，需按附 E 程序重录

## 3. 不做什么

- 不删 `openSessionIds` / `closeSession` 状态（快捷键与初始化依赖）
- 不改 `SessionSidebar`（用户明确侧栏功能不动）
- 不改后端 / API
- 不把快捷键 `Cmd+1..9` 的语义改为「按侧栏列表序号」（保持 openSessionIds，最小改动）

## 4. 验收标准

1. 前端 `npm test`：SessionTabs 相关 7 条用例随文件删除；其余测试全绿
2. tsc / lint：0 错误
3. 双主题视觉基线重录后自证 82 passed（受影响 baseline 更新，其余不变）
4. 实机：`/chat` 顶栏下方无 tab 条；侧栏点击会话可切换；侧栏「新建会话」可用；`Cmd+1..9` 仍可切换到已打开会话
5. 全量 pytest 8139 + gate 13/13（前端纯 UI 改动，后端门不受影响）

## 5. 风险与回滚

| 风险 | 处置 |
|---|---|
| 用户习惯「tab 上点 × 关闭但保留历史」 | 侧栏有归档按钮（同样保留历史），语义等价；快捷键 Cmd+Shift+W 保留 |
| 删 tab 后 `closeSession` 不再有 UI 触发点（仅快捷键） | 可接受（用户要求「全部交给侧栏」，归档在侧栏）；若后续需要可加回侧栏 hover 关闭按钮 |
| 视觉基线大面积变化 | 只影响含 tab 条的截图（主会话类），catalog 组件基线不受影响 |

单笔 revert 代码 + 基线两笔提交即可回滚，无数据迁移。

## 6. 与并行方案的关系

本方案独立于 `docs/design/trace-panel-and-dag-hide-20261123.md`（Trace 独立 + DAG 隐藏）与 `rename-entry-consolidation-20261123.md`（重命名合并）——三者互不依赖，可分别批准/实施。
