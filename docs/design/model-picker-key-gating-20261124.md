# Chat 模型下拉按密钥状态置灰方案（R-014 · 已批准 · 零代码起步）

> 日期：2026-11-24
> 状态：方案。用户已批准「置灰 + 标注 + 禁止选择」方案（在隐藏/置灰二选一中选置灰）。本文不改实现。
> 触发：清除 nvidia/openai/siliconflow 等 provider 密钥后，Chat 模型下拉仍能选中这些无密钥模型，选中后发送必然失败且界面无提示。

## 1. 根因（已自证）

- 后端 `system.py:300-343` `_provider_catalog()`：provider 名取自 `set(_REGISTRY) | set(profiles)`，返回全部内置 provider；每个带 `key_configured: bool(env or dotenv)`。**后端只标记、不过滤**。
- 前端 `stores/model.ts:48-56`：`loadProviders()` 从 `/api/system/llm` 拉取后原样存，无过滤。
- Chat 下拉 `ComposerStatusBar.tsx:124-145`：`providers.map(p => p.models.map(...))` 直接渲染全部模型，**完全没读 `key_configured`**，无禁用、无提示。
- 对照组：设置页 `LLMSection.tsx:110` 正确使用 `p.key_configured ? ' ✓' : '（未配置密钥）'`。数据字段齐全，只是 Chat 组件漏用。

## 2. 方案

只改 `ComposerStatusBar.tsx` 下拉渲染（约 10 行），不动 store / 后端。

1. 每个 provider 选项根据 `p.key_configured` 分两种渲染：
   - **有密钥**（true）：保持现状，可点击选择，样式不变。
   - **无密钥**（false）：`disabled` + 视觉置灰（`text-slate-600` / `cursor-not-allowed` / `opacity-50`），末尾追加小字标注「未配置密钥」；`onClick` 不绑定选择逻辑（disabled 天然阻止）。
2. 「Default (… )」选项代表当前生效 profile（必然有密钥），保持可选。
3. 不影响 `p.label` / `m` 的显示文案与 `fullModel = provider/model` 的取值规则。
4. 置灰项仍出现在列表里（用户能看到有哪些 provider 可接入），与设置页口径一致。

## 3. 测试

`ComposerStatusBar.test.tsx` 新增 3 例：
- 无密钥 provider 的模型渲染为 disabled + 带「未配置密钥」标注；点击不改变 `sessionModels`
- 有密钥 provider 的模型可点击并正常写入 `sessionModels`
- Default 选项始终可点击清除覆盖

## 4. 验证

- 受影响测试 → 前端全量（约 1365）/ tsc / lint
- 实机：Vite 5173 打开模型下拉，nvidia/openai 等置灰带标注，MiniMax 可选
- 路径限定提交，不 push

## 5. 不做

- 不改后端 catalog（保留全 provider 标记，设置页依赖）
- 不改 store（不过滤数据，只改展示层）
- 不做「点击无密钥项跳转到设置页配置」的引导（可选后续）

## 6. 回滚

`git revert` 本批提交即可；无持久化状态、无迁移。
