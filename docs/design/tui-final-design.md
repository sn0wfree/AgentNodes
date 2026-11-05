# TUI 最终设计方案

**日期**: 2026-07-26
**状态**: 设计确认，待实现

---

## 一、设计原则

1. **Header 扩展**：token + ctx + stats 合并到 Header（一行）
2. **ToolsRail 精简**：只保留 Goal + Tools（去掉 Info Bar）
3. **Transcript 不变**：主内容区保持原样
4. **Input/Footer 不变**：底部保持原样
5. **面板开关**：Ctrl+1/2 切换 Commands/Transcript

---

## 二、最终布局

```
┌─────────────────────────────────────────────────────────────────────┐
│ ● live  minimax-M3  │ 5 msg  3 tool │ 1.2k/128k [====] │ 2/3 ok │
├──────────────────────────────────────────────────┬──────────────────┤
│ Transcript                                       │ ToolsRail        │
│                                                  │                  │
│ 你: 帮我分析一下均值回归策略                      │ GOAL             │
│                                                  │ 研究A股低回撤量化│
│ 🤖 thinking... (2.3s)                           │ [======------] 55%│
│                                                  │ 3/5 criteria     │
│ 策略名称: 均值回归策略                           │                  │
│                                                  │ ● step_5 ⏳      │
│ 关键指标:                                        │ ● step_4 ✔      │
│ - 年化收益: 12.3%                                │ ● step_3 ✔      │
│ - 最大回撤: -8.5%                                │ ● step_2 ✔      │
│ - 夏普比率: 1.42                                 │ ● step_1 ✔      │
│ - 胜率: 58.2%                                    ├──────────────────┤
│                                                  │ TOOLS            │
│                                                  │ ⏳ risk_analysis │
│                                                  │ ✔ backtest 5.2s │
│                                                  │ ✔ analysis 1.2s │
├──────────────────────────────────────────────────┴──────────────────┤
│ ▸ _                                                                │
├─────────────────────────────────────────────────────────────────────┤
│ F1 Help  Ctrl+C Halt  Ctrl+D Quit  Ctrl+X Panel  Ctrl+1/2 Pnl    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 三、组件设计

### 1. Header（扩展状态栏）

**一行显示**：
```
● live  minimax-M3  │ 5 msg  3 tool │ 1.2k/128k [====] │ 2/3 ok
```

**布局**：4 个区域，用 `│` 分隔

| 区域 | 内容 | 来源 |
|---|---|---|
| 左 | 连接状态 ● + 模型名称 | LLMConfig.load().model |
| 中左 | 消息数 + 工具数 | ctx.history + 工具计数 |
| 中右 | Token 使用量 + 进度条 | len(text)/4 + 128k |
| 右 | 成功率 | 工具成功数/总数 |

**颜色**：
- 连接状态 ●：绿色（live）/ 灰色（idle）/ 红色（error）
- Token 进度条：<50% 绿 / 50-80% 黄 / >80% 红

**实时更新**：200ms 轮询

---

### 2. ToolsRail（精简版）

**区域 1：Goal Progress**

```
GOAL
研究A股低回撤量化策略
[======------] 55%  3/5
● step_5 ⏳ running
● step_4 ✔ done
● step_3 ✔ done
● step_2 ✔ done
● step_1 ✔ done
```

- 标题：`GOAL`
- 描述：截断为 20 字符
- 进度条 + 百分比 + 完成数
- 里程碑列表：倒序（最新在上），最多 5 条，可滚动
- 无目标时显示提示

**区域 2：Tool Timeline**

```
TOOLS
⏳ risk_analysis ...
✔ backtest_result 5.2s
✔ factor_analysis 1.2s
─────────────────────
10/12 tools(2 running)
```

- 标题：`TOOLS`
- 工具列表：10 条可滚动，自动滚动到最新
- 计数：`完成/总数 tools(N running)`
- 状态图标：✔（成功）/ ⏳（运行中）/ ✘（失败）

---

### 3. Transcript（不变）

保持现有行为：
- 用户输入显示
- LLM 流式回复
- Slash 命令输出
- 工具调用内联（可选）

---

### 4. Input（不变）

保持现有行为：
- 单行输入
- Enter 发送
- `/` 触发联想菜单（新增）

---

### 5. Footer（不变）

保持现有行为：
- 快捷键提示
- 品牌色

---

## 四、快捷键

| 快捷键 | 动作 |
|---|---|
| `Ctrl+1` | 切换 Commands 侧边栏 |
| `Ctrl+2` | 切换 Transcript |
| `Ctrl+X` | 展开/折叠 ToolsRail |
| `Ctrl+↑/↓` | ToolsRail 内部焦点切换 |
| `↑↓` | 滚动 ToolsRail 内容 |
| `Tab` | 从 ToolsRail 切换到 Transcript |
| `F1` | 帮助 |
| `Ctrl+C` | 中断 |
| `Ctrl+D` | 退出 |
| `Ctrl+L` | 清屏 |

---

## 五、默认面板状态

| 面板 | 默认状态 |
|---|---|
| Commands 侧边栏 | **关闭** |
| Transcript | **开启** |
| ToolsRail | **开启** |

---

## 六、动态比例

### ToolsRail 高度

**最小**：6 行（Goal 3 行 + Tools 3 行）
**最大**：TUI 高度 - 11 行（Header 1 + Input 3 + Footer 1 + 边框 6）

### Goal 区域高度

**最小**：3 行（无里程碑）
**最大**：9 行（5 里程碑 + 滚动提示）

### Tool Timeline 高度

**最小**：3 行
**最大**：10 行

---

## 七、配置项

```json
{
  "header": {
    "show_connection_status": true,
    "show_model_name": true,
    "show_message_count": true,
    "show_tool_count": true,
    "show_token_usage": true,
    "show_success_rate": true
  },
  "tools_rail": {
    "goal_max_milestones": 5,
    "goal_truncate_length": 20,
    "tools_min_lines": 3,
    "tools_max_lines": 10
  }
}
```

---

## 八、实现计划

| 步骤 | 文件 | 改动 | 工作量 |
|---|---|---|---|
| 1 | `tui/widgets/header.py` | 新增 StatusHeader 组件 | 中 |
| 2 | `tui/widgets/tools_rail.py` | 重写为 Goal + Tools（去掉 Info Bar） | 中 |
| 3 | `tui/app.py` | 修改 compose：Header 替换为 StatusHeader | 小 |
| 4 | `tui/styles.tcss` | 更新 Header 样式 | 小 |
| 5 | `tui/session.py` | 工具事件路由到 ToolsRail | 中 |
| 6 | 测试验证 | 验证布局和交互 | 中 |

---

## 九、与现有代码的兼容性

| 现有组件 | 改动 |
|---|---|
| `TUIHeader` | 替换为 `StatusHeader` |
| `ActivityRail` | 替换为 `ToolsRail`（Goal + Tools） |
| `CommandSidebar` | 保留，默认隐藏 |
| `TranscriptView` | 不变 |
| `ChatInput` | 不变 |
| `HintFooter` | 不变 |

---

## 十、待实现

1. ✅ 设计确认
2. ⬜ 实现 StatusHeader 组件
3. ⬜ 重写 ToolsRail 组件
4. ⬜ 修改 app.py compose
5. ⬜ 更新 styles.tcss
6. ⬜ 测试验证
