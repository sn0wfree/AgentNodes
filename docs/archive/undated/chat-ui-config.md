# Chat UI 配置文件文档

## 概述

聊天界面的视觉样式通过 JSON 配置文件驱动，无需修改代码即可自定义外观。

## 配置文件位置

```
webui/frontend/public/
├── agent-styles.json        ← Agent 样式配置
└── chat-ui-config.json      ← 聊天界面配置
```

## Agent 样式配置（agent-styles.json）

### 结构

```json
{
  "agents": {
    "<agent_id>": {
      "name": "显示名称",
      "icon": "emoji 图标",
      "color": "Tailwind 颜色名",
      "category": "分类"
    }
  },
  "categories": {
    "<category>": {
      "label": "分类标签",
      "color": "颜色"
    }
  },
  "default": {
    "name": "默认名称",
    "icon": "默认图标",
    "color": "默认颜色",
    "category": "默认分类"
  }
}
```

### 支持的颜色

blue, violet, emerald, cyan, amber, red, yellow, pink, orange, slate

### 添加新 Agent

在 `agents` 中添加新条目即可：

```json
"new_agent": {
  "name": "New Agent",
  "icon": "🆕",
  "color": "indigo",
  "category": "research"
}
```

## 聊天界面配置（chat-ui-config.json）

### 结构

```json
{
  "assistant": { ... },      // Agent 消息样式
  "userBubble": { ... },     // 用户消息样式
  "toolCall": { ... },       // 工具调用样式
  "thinking": { ... },       // 思考块样式
  "messageList": { ... },    // 消息列表样式
  "pageShell": { ... }       // 页面布局样式
}
```

### 各部分配置项

#### assistant（Agent 消息）

| 配置项 | 类型 | 说明 |
|--------|------|------|
| avatar.icon | string | 头像图标（lucide-react 图标名） |
| avatar.size | number | 头像大小（px） |
| avatar.gradient | string[] | 头像渐变色 |
| labels.modelPrefix | string | 模型标签前缀 |
| labels.modelSeparator | string | 模型标签分隔符 |
| labels.queuedLabel | string | 排队中标签 |
| colors.streamingDot | string | 流式点颜色 |
| colors.flatLabel | string | 平铺模式标签颜色 |
| colors.bubbleLabel | string | 气泡模式标签颜色 |
| visibility.showVerifiabilityBadge | boolean | 显示验证徽章 |
| visibility.showStreamingStatus | boolean | 显示流式状态 |

#### userBubble（用户消息）

| 配置项 | 类型 | 说明 |
|--------|------|------|
| labels.userLabel | string | 用户标签 |
| labels.editLabel | string | 编辑按钮标签 |
| labels.resendLabel | string | 重发按钮标签 |
| colors.gradient | string[] | 气泡渐变色 |
| sizing.maxWidth | string | 最大宽度 |
| sizing.borderRadius | string | 圆角 |
| visibility.allowEdit | boolean | 允许编辑 |

#### toolCall（工具调用）

| 配置项 | 类型 | 说明 |
|--------|------|------|
| icons.default | string | 默认工具图标 |
| colors.running | object | 运行中颜色 |
| colors.done | object | 完成颜色 |
| colors.error | object | 错误颜色 |
| labels.dangerousBadge | string | 危险工具标签 |
| dangerousTools | string[] | 危险工具列表 |
| visibility.showArgsPreview | boolean | 显示参数预览 |
| visibility.showResultSummary | boolean | 显示结果摘要 |
| visibility.showDuration | boolean | 显示耗时 |

#### thinking（思考块）

| 配置项 | 类型 | 说明 |
|--------|------|------|
| icons.header | string | 头部图标 |
| colors.border | string | 边框颜色 |
| colors.background | string | 背景颜色 |
| labels.streamingFormat | string | 流式标签格式 |
| labels.doneFormat | string | 完成标签格式 |
| sizing.maxHeight | number | 最大高度（px） |
| visibility.showCopy | boolean | 显示复制按钮 |

#### messageList（消息列表）

| 配置项 | 类型 | 说明 |
|--------|------|------|
| labels.roundSeparator | string | 轮次分隔符格式 |
| labels.emptyTitle | string | 空状态标题 |
| labels.emptyDescription | string | 空状态描述 |
| labels.loadMore | string | 加载更多按钮标签 |
| colors.separatorLine | string | 分隔线颜色 |
| colors.separatorPill | string | 分隔符药丸背景色 |

#### pageShell（页面布局）

| 配置项 | 类型 | 说明 |
|--------|------|------|
| layout.headerHeight | number | 头部高度（px） |
| layout.contentMaxWidth | number | 内容最大宽度（px） |
| layout.contentPadding | object | 内容内边距 |
| labels.themeLightLabel | string | 切换浅色模式标签 |
| labels.themeDarkLabel | string | 切换深色模式标签 |

## 修改配置后

配置文件修改后，前端需要重新构建（`npm run build`）才能生效。Vite 会自动处理 JSON 导入。

## 向后兼容

- 未知的 agent ID 使用 `default` 配置
- 缺失的配置项使用内置默认值
- 配置文件格式变更时，旧配置仍然有效（只覆盖新字段）
