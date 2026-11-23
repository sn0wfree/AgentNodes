# 用户管理与设置合并方案（R-014 · 待批准 · 零代码）

> 日期：2026-11-23
> 性质：方案。批准后才动代码；本文不改任何实现。
> 触发：用户请求「用户管理和设置合并，将设置合并到用户管理中去」。

## 1. 事实核查与方向裁决

### 1.1 两页现状（agent 实测）

| | AdminUsersPage（用户管理） | SettingsModal（设置） |
|---|---|---|
| 入口 | IconNav **仅 admin**（`IconNav.tsx:92-114`）+ `/admin/users` + `AdminGuard`（`App.tsx:105-112`） | IconNav **所有用户**（`:118-124`）+ 命令面板（`CommandPalette.tsx:157-165`），App 全局挂载（`App.tsx:130`） |
| 可见性 | 前端 AdminGuard + 后端 `require_admin`（`admin_users.py:32-59`）双层 | 无角色判断；`/system/*` 无 admin 依赖 |
| 内容 | 建用户/改角色/禁用/代重置密码/看数据（7 项） | LLM 配置（含 API key）/AccountSection（标题字面就是"用户管理"，自助改密码）/工作区/聊天布局/主题（5 分区） |
| 状态源 | 路由 + 页面本地 state | zustand `layout.settingsOpen`（非持久化）+ Dialog.Root |

### 1.2 字面方案的权限倒退（必须避免）

「把设置塞进 admin-only 的用户管理页」→ 普通用户失去主题/LLM key/自助改密码入口（`IconNav.tsx:118` 当前无角色条件）。这是权限倒退，不可接受。

### 1.3 采用的裁决

**将「用户管理」页升级为统一的「账户与设置」页**，保留所有用户可达性：
- 用户管理表格成为该页的 **admin-only 分区**（沿用 `user?.role === 'admin'` 判断 + 后端 `require_admin` 兜底）
- 设置的 5 个分区搬入该页（以 tab 或纵向分区呈现）
- 普通用户访问同一页，只看到自己的设置项，看不到用户管理表格
- 消除 `AccountSection` 的「用户管理」重名 → 改名「个人账号」

这既满足「设置并入用户管理页」的字面方向，又不剥夺普通用户能力。

## 2. 实施方案

### 2.1 路由与入口

1. `/admin/users` 路由改名为 `/account`（或保留 `/admin/users` 但去掉 AdminGuard）——建议新路由 `/account`，所有登录用户可达
2. `App.tsx`：`<Route path="/account" element={<AuthGuard><AccountPage /></AuthGuard>} />`；保留 `/admin/users` → `<Navigate to="/account" replace />` 兼容旧链接
3. `IconNav.tsx`：
   - 删原 admin-only「用户管理」项（`:92-114`）
   - 新增所有用户可见的「账户与设置」项（指向 `/account`，图标 Settings）
   - 删原设置按钮（`:118-124`，设置已并入页面）
4. `CommandPalette.tsx`：`nav-settings` 命令从「打开弹窗」改为 `navigate('/account')`（`:157-165`）

### 2.2 新页面 AccountPage（由 AdminUsersPage + SettingsModal 合成）

- 文件：`pages/AccountPage.tsx`（新建；AdminUsersPage 与 SettingsModal 的内容迁入）
- 结构：纵向分区（沿用 `components/settings/shared.tsx` 的 `Section` 原语），不引入新 tab 组件：
  1. **个人账号**（原 AccountSection，改标题）：改自己密码
  2. **LLM 配置**（原 LLMSection）：provider/model/key
  3. **聊天布局**（原 ChatLayoutSection）
  4. **外观**（原 AppearanceSection）
  5. **工作区**（原 WorkspaceSection）
  6. **用户管理**（原 AdminUsersPage 表格，**仅 admin 渲染**，沿用 `isAdmin` 判断）
- 数据加载：原 SettingsModal 的 `GET /system/info` + `GET /system/llm`（`:37-58`）在页面 mount 时加载；用户管理表格仅 admin 时 `api.adminUsers.list()`

### 2.3 删除/退役

- `components/common/SettingsModal.tsx`：删除（内容已迁入 AccountPage）
- `pages/AdminUsersPage.tsx`：删除（内容已迁入 AccountPage）
- `stores/layout.ts`：`settingsOpen` / `setSettingsOpen` 退役（设置不再弹窗）
- `App.tsx`：移除 `<SettingsModal />` 全局挂载（`:130`）
- 相关测试：`adminUsersPage.test.tsx` / `settingsSections.test.tsx` 迁移为 `accountPage.test.tsx`（分区契约 + admin 条件渲染 + 普通用户无用户管理表格）

### 2.4 后端零改动

所有 API 原样保留（`/system/*`、`/auth/change-password`、`/admin/users/*`）——合并纯前端。

## 3. 权限验证（核心）

| 场景 | 预期 |
|---|---|
| 普通用户访问 `/account` | 看到个人账号/LLM/布局/外观/工作区 5 分区，**无**用户管理表格 |
| admin 访问 `/account` | 5 分区 + 用户管理表格（完整 7 项操作） |
| 普通用户直接调 `/api/admin/users` | 403（后端 require_admin 兜底，UI 隐藏不是唯一防线） |
| 旧链接 `/admin/users` | 重定向 `/account`，admin 看到完整页，普通用户看到无表格页 |

## 4. 风险与回滚

| 风险 | 处置 |
|---|---|
| LLM 配置原本任何登录用户可改（服务端全局写 `.env`），合并后仍如此 | 保持现状（本批不收紧后端权限，另案） |
| 弹窗→页面改变用户习惯 | 命令面板与图标仍可一键进入 `/account`；绝对路径可分享 |
| 页面比弹窗重（路由切换 vs 浮层） | 设置项不多（5 分区），可接受 |
| 测试大批量迁移 | adminUsersPage.test.tsx（269 行）+ settingsSections.test.tsx 改为 AccountPage 测试，保留自我防护/空 key 不覆盖等关键断言 |

单笔 revert 可回滚；无数据迁移。

## 5. 验证

- 前端全量 / tsc / lint；双主题基线重录（IconNav 少一项、设置页变页面）
- 新增 `accountPage.test.tsx`：普通用户/admin 两种角色的分区可见性断言
- 实机：admin 与普通用户分别登录 → `/account` 分区差异正确；旧 `/admin/users` 重定向正常

## 6. 不做

- 不改后端 API 与权限（require_admin 继续兜底）
- 不引入新 tab 组件（用现有 Section 纵向分区）
- 不新增 feature flag
- 不删除 `components/settings/*` 分区组件（迁入 AccountPage 复用）
