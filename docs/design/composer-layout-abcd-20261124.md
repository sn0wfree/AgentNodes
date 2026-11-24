# 输入框布局对标改造 A/B/C/D 方案（R-014 · 用户已批准 A+B，C+D 待细化）

> 日期：2026-11-24
> 状态：**A+B 已批准实施**；C+D 用户明确「还需要做」，但依赖后端契约调研（并行进行中），细化后另批。
> 依据：`composer-layout-industry-benchmark-20261124.md`（ZCode 一手源码对标）。

## 总览

| 项 | 内容 | 状态 |
|---|---|---|
| **A** | 状态行并入输入框容器，形成 `rounded-2xl` 大卡片（编辑器在上、工具条在下） | 已批准 |
| **B** | 发送按钮改圆形品牌色实心箭头 | 已批准 |
| **C** | 用量弹层加 7 段分段构成（messages/system_prompt/...） | 待调研后端数据源 |
| **D** | 排队消息编辑态（编辑条 + 取消按钮） | 待调研后端协议 |

---

## A · 状态行并入输入框容器（高价值，中成本）

### 目标结构（对齐 ZCode `data-composer-layout`）

```
┌──────────────────────────────────────────┐  ← rounded-2xl 大卡片
│  [条件] 编辑排队消息提示条 / 附件 chips   │  ← D 的落位（后续）
│  ┌────────────────────────────────────┐  │
│  │ textarea（Markdown 编辑器）         │  │
│  └────────────────────────────────────┘  │
│  [persona] [Build] [模型] [思考] [用量]    │  ← 工具条（框内底部）
└──────────────────────────────────────────┘
```

### 改动

1. `Composer.tsx`：把 `ComposerStatusBar` 从输入框容器**外部移到内部**，与 textarea 同处一个 `flex flex-col` 容器；外层容器改用 ZCode 同款圆角/边框/focus-within 样式（`rounded-2xl border-[0.5px] ... focus-within:border-...`）。
2. `ComposerStatusBar.tsx`：去掉 `mb-2`（间距由父容器 `gap-*` 控制）；`ml-auto` 保留（用量仍靠右）。
3. 图片预览/slash 菜单仍在容器上方，位置不变。
4. 测试：新增「状态行在输入框容器内」DOM 结构断言；回归 composer/statusbar 测试。

### 风险

- 视觉基线会变（empty_chat 等含输入框的场景）→ 重录双主题
- 375 宽下工具条仍 2 行，但整卡片高度略增——实机确认

## B · 发送按钮改圆形品牌色实心箭头（低风险）

### 改动

`Composer.tsx:475-482` 发送按钮：
- 现：`ua-primary-action rounded-lg p-1.5` + Send 图标
- 改为：`rounded-full bg-brand text-foreground-inverse ...`（圆形、品牌色、箭头图标）

停止按钮同步改圆形（ZCode 的 `v4-stop` 也是圆形）。

> 注：`bg-brand` / `text-foreground-inverse` 在 ZCode 是 sky 品牌色；AgentNodes 的 `--accent`/`primary-*` 已是 sky 系，直接用现有令牌即可，不新增色。

### 验证

- 回归 composer 测试；双主题基线重录；实机确认圆形 CTA 辨识度

## C · 用量弹层加 7 段分段（依赖后端，待细化）

ZCode 的 7 段来自 LLM usage 的细粒度拆分。AgentNodes 当前只有总量且无实测事件（见 `context-usage-agent-survey-20261124.md` G1）。**前置依赖**：后端先把 `llm_usage` / `session_total_tokens` 透传到前端（P0-a），再谈分段。

调研 agent 正在查：后端是否已有 prompt 拆分数据、最小改造路径。**细化后出独立子方案**。

## D · 排队消息编辑态（依赖后端，待细化）

ZCode 有完整协议：`v4-composer-edit-bar`（容器内顶部）、编辑排队消息、取消、应用时确认模型/模式变更、`steerQueued/steerDrained` 事件。AgentNodes 有队列但无编辑态 UI。

调研 agent 正在查：现有 queue/steer API 与事件。**细化后出独立子方案**。

## 批次与顺序

| 批 | 内容 | 前置 |
|---|---|---|
| G0 | A+B 前端改造 + 测试 + 基线重录 + 提交 | 无（已批准） |
| G1 | C 子方案（后端事件透传 + 分段弹层） | 后端契约调研 |
| G2 | D 子方案（排队编辑协议 + 编辑条 UI） | 后端契约调研 |

## 不做

- 不做 E（语音/worktree/CUA）与恢复 Markdown 按钮
- 不在 G0 动后端
- 不 push

## 回滚

G0 独立提交，可 `git revert`；基线重录单独一笔。
