# TUI 重新设计方案

**日期**: 2026-07-26
**状态**: 设计完成，待实现

---

## 一、设计目标

1. **借鉴 OpenCode**：面板开关系统，灵活组合
2. **借鉴 vibe-trading**：顺序流布局，ChatInput `/` 联想
3. **借鉴 llmwikify**：ToolsRail 状态栏，Goal 进度
4. **保留本项目特色**：三栏布局默认开启，量化研究专用

---

## 二、整体布局

### 默认布局（三栏）

```
┌─────────────────────────────────────────────────────────────────────┐
│ QuantNodes-Research v0.5.0 · minimax/MiniMax-M3           [≡]     │
├──────────────────────────────────────────────────┬──────────────────┤
│ Transcript                                       │ ToolsRail        │
│                                                  │                  │
│ 你: 帮我分析一下均值回归策略                      │ 见下方详细设计   │
│                                                  │                  │
│ 🤖 thinking... (2.3s)                           │                  │
│                                                  │                  │
│ 策略名称: 均值回归策略                           │                  │
│                                                  │                  │
│ 关键指标:                                        │                  │
│ - 年化收益: 12.3%                                │                  │
│ - 最大回撤: -8.5%                                │                  │
│                                                  │                  │
├──────────────────────────────────────────────────┴──────────────────┤
│ ▸ _                                                                │
├─────────────────────────────────────────────────────────────────────┤
│ F1 Help  Ctrl+C Halt  Ctrl+D Quit  Ctrl+X Ctx  Ctrl+1/2/3 Pnl    │
└─────────────────────────────────────────────────────────────────────┘
```

### 面板开关（Ctrl+1/2/3）

| 面板 | 默认 | 快捷键 |
|---|---|---|
| Commands 侧边栏 | **关闭** | Ctrl+1 |
| Transcript | **开启** | Ctrl+2 |
| ToolsRail | **开启** | Ctrl+3 |

---

## 三、ToolsRail 设计（三区域 + 动态布局）

### 区域 1：Info Bar（合并 Session + Stats + Context Window + Status）

**折叠模式**（默认）：
```
┌──────────────────────────────────────────┐
│ ● live  minimax-M3  │ 5 msg   3 tool    │
│ #75e481  1% ctx     │ 2/3 ok            │
│                     │ Last: just now     │
└──────────────────────────────────────────┘
```

**展开模式**（`/debug` 或 `Ctrl+X`）：
```
┌──────────────────────────────────────────┐
│ ● live  minimax-M3  │ 5 msg   3 tool    │
│ #75e481  1% ctx     │ 2/3 ok            │
│ [========-------]   │ Last: just now     │
│ 1.2k / 128k tokens  │                    │
└──────────────────────────────────────────┘
```

**布局**：3 行 × 2 列

```
┌─────────────────────┬────────────────────┐
│ ● live  minimax-M3  │ 5 msg   3 tool     │
│ #75e481  1% ctx     │ 2/3 ok             │
│ [========-------]   │ Last: just now     │
│ 1.2k / 128k tokens  │                    │
└─────────────────────┴────────────────────┘
   左栏（状态+模型+上下文）  右栏（统计+状态）
```

**左栏**：
- 第 1 行：连接状态 ● + 模型名称
- 第 2 行：Session ID（短 hash）+ 上下文百分比
- 第 3 行：（折叠时空，展开时进度条）
- 第 4 行：（展开时 Token 使用量）

**右栏**：
- 第 1 行：消息数 + 工具数
- 第 2 行：成功数/总数
- 第 3 行：最后活动时间

**上下文百分比颜色**：
- `< 50%`：primary color（绿色）
- `50-80%`：warning color（黄色）
- `> 80%`：error color（红色）

---

### 区域 2：Goal Progress（动态高度）

**核心原则**：Goal 区域和 Tool Timeline 共享垂直空间，Goal 逐步扩展，Tools 自动收缩。

#### 状态 1：初始阶段（Goal 刚创建）

```
┌──────────────────────────────────────────┐
│ GOAL                                     │
│ 研究A股低回撤量化策略                    │
│ [==----------] 15%                      │
│ 1/5 criteria                             │
└──────────────────────────────────────────┘
```

**高度**: 4 行

#### 状态 2：中期阶段（Goal 有多个里程碑）

```
┌──────────────────────────────────────────┐
│ GOAL                                     │
│ 研究A股低回撤量化策略                    │
│ [========------] 55%                    │
│ 3/5 criteria                             │
│                                          │
│ ● step_5 风险评估           ⏳ running  │
│ ● step_4 回测验证           ✔ done      │
│ ● step_3 设计策略逻辑       ✔ done      │
│ ● step_2 收集市场数据       ✔ done      │
│ ● step_1 定义研究范围       ✔ done      │
├──────────────────────────────────────────┤
│ ↑↓ scroll  5/5 shown                    │
└──────────────────────────────────────────┘
```

**高度**: 9 行（含里程碑列表 + 滚动提示）

#### 状态 3：后期阶段（Goal 接近完成）

```
┌──────────────────────────────────────────┐
│ GOAL                                     │
│ 研究A股低回撤量化策略                    │
│ [============--] 85%                    │
│ 4/5 criteria                             │
│                                          │
│ ● step_5 风险评估           ⏳ running  │
│ ● step_4 回测验证           ✔ done      │
│ ● step_3 设计策略逻辑       ✔ done      │
│ ● step_2 收集市场数据       ✔ done      │
│ ● step_1 定义研究范围       ✔ done      │
├──────────────────────────────────────────┤
│ ↑↓ scroll  5/5 shown                    │
└──────────────────────────────────────────┘
```

**高度**: 9 行（里程碑列表 + 滚动提示）

---

### 区域 3：Tool Timeline（动态高度）

**核心原则**：与 Goal 共享垂直空间，Goal 扩展时 Tools 收缩。

**默认**：
```
┌──────────────────────────────────────────┐
│ TOOLS                                    │
│ ✔ read_file      0.3s                   │
│ ⏳ run_backtest  12.4s                   │
│ ✔ get_market     0.8s                   │
│ ✘ factor_calc    err                    │
│ ✔ compute_factor 0.5s                   │
│ ⏳ web_search    3.2s                    │
│ ✔ read_url       0.4s                   │
│ ✔ list_history   0.1s                   │
│ ⏳ run_backtest  ...                     │
│ ✔ get_market_data 0.6s                  │
├──────────────────────────────────────────┤
│ 10/12 tools(2 running)                  │
└──────────────────────────────────────────┘
```

**高度**: 10 行 + 滚动 + 计数

---

## 四、动态比例算法

### 总可用空间

```
TUI 高度 - Header(1) - Input(3) - Footer(1) - 边框(6) = TUI高度 - 11
```

### Goal 区域高度计算

```python
def calc_goal_height(criteria_count: int, has_recent_update: bool) -> int:
    """计算 Goal 区域高度"""
    base = 3  # GOAL 标题 + 描述 + 进度条
    milestone_lines = min(criteria_count, 5)  # 最多显示 5 条
    scroll_hint = 1 if criteria_count > 5 else 0  # 滚动提示
    return base + milestone_lines + scroll_hint

# 最小: 4 行 (无里程碑)
# 最大: 9 行 (5 里程碑 + 滚动提示)
```

### Tool Timeline 高度计算

```python
def calc_tools_height(total_height: int, goal_height: int) -> int:
    """计算 Tool Timeline 高度"""
    available = total_height - goal_height - 3  # 减去 Info Bar
    return max(3, min(10, available))  # 最小 3 行，最大 10 行
```

### 动态比例示例

| TUI 高度 | Info Bar | Goal | Tools | 总计 |
|---|---|---|---|---|
| 30 行 | 3 | 4 | 10 | 17 |
| 35 行 | 3 | 9 | 10 | 22 |
| 40 行 | 3 | 9 | 10 | 22 |

---

## 五、里程碑状态显示

### 数据来源

`goals.db` 的 `goal_criteria` 表

### 显示格式

```python
# 每个里程碑一行（倒序：最新在上）
f"● {step_name}       {status_icon} {status}"

# status_icon 映射
"pending"   → "○ pending"
"running"   → "⏳ running"  
"done"      → "✔ done"
"failed"    → "✘ failed"
"skipped"   → "○ skipped"
```

### 滚动行为

- **最多显示 5 条**里程碑
- **倒序排列**：最新在上
- 超过 5 条时显示滚动提示 `↑↓ scroll  5/5 shown`
- 支持 **鼠标滚轮** + **键盘 ↑↓** 滚动

---

## 六、ChatInput `/` 联想系统

### 交互设计

```
┌─────────────────────────────────────────────────────────────────────┐
│ ▸ /go                                                              │
├─────────────────────────────────────────────────────────────────────┤
│ ┌─ Suggested ────────────────────────────────────────────────────┐ │
│ │ /goal   Start / inspect a finance research goal    ⏎ select   │ │
│ │ /export Export current session (md / json)         ⏎ select   │ │
│ └────────────────────────────────────────────────────────────────┘ │
│ ┌─ All Commands ─────────────────────────────────────────────────┐ │
│ │ /help     Show keyboard shortcuts and command list            │ │
│ │ /model    Switch LLM provider and model                       │ │
│ │ /memory   Show / manage persistent memory                     │ │
│ │ /history  Browse and resume prior sessions                    │ │
│ │ /search   Full-text search across all sessions                │ │
│ │ /swarm    Multi-agent presets (committee / quant / risk)      │ │
│ │ /skill    List / load / unload skills                         │ │
│ │ /show     Show prior run by id                                │ │
│ │ /clear    Clear current conversation                          │ │
│ │ /pine     Export current strategy as Pine Script              │ │
│ │ /journal  Analyze trade journal CSV                           │ │
│ │ /shadow   Train / view shadow account                         │ │
│ │ /debug    Toggle debug panel (token usage / latency)          │ │
│ │ /halt     Kill switch — halt long-running loops now           │ │
│ │ /resume   Clear the kill switch                               │ │
│ │ /quit     Exit (also: q, exit, :q)                            │ │
│ └────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────┤
│ F1 Help  Ctrl+C Halt  Ctrl+D Quit  Ctrl+X Ctx  Ctrl+1/2/3 Pnl    │
└─────────────────────────────────────────────────────────────────────┘
```

### 触发条件

- 输入 `/` 时自动弹出联想菜单
- 继续输入字符 → 模糊匹配（前缀 > 子串 > 子序列）
- `↑↓` 导航候选列表
- `Tab` / `Enter` 选中
- `Esc` 关闭菜单

### 实现位置

- 新增 `ChatInputAutocomplete` 组件（在 ChatInput 上方浮动）
- 复用 `slash_router.match_commands()` 模糊匹配逻辑

---

## 七、快捷键汇总

| 快捷键 | 动作 |
|---|---|
| `Ctrl+1` | 切换 Commands 侧边栏 |
| `Ctrl+2` | 切换 Transcript |
| `Ctrl+3` | 切换 ToolsRail |
| `Ctrl+X` | 展开/折叠 Context Window |
| `Ctrl+B` | 切换全部面板 |
| `Ctrl+↑/↓` | ToolsRail 内部焦点切换 |
| `↑↓` | 滚动 ToolsRail 内容 |
| `Tab` | 从 ToolsRail 切换到 Transcript |
| `F1` | 帮助 |
| `Ctrl+C` | 中断 |
| `Ctrl+D` | 退出 |
| `Ctrl+L` | 清屏 |

---

## 八、默认面板状态

| 面板 | 默认状态 |
|---|---|
| Commands 侧边栏 | **关闭** |
| Transcript | **开启** |
| ToolsRail | **开启** |
| Context Window | **折叠** |

---

## 九、配置项

```json
{
  "tools_rail": {
    "goal_max_lines": 5,
    "tools_min_lines": 3,
    "tools_max_lines": 10,
    "milestone_truncate_length": 20,
    "ctx_window_default_collapsed": true
  },
  "chat_input": {
    "autocomplete_max_suggestions": 3,
    "autocomplete_max_all": 16
  }
}
```

---

## 十、实现计划

| 步骤 | 文件 | 改动 | 工作量 |
|---|---|---|---|
| 1 | `tui/app.py` | 添加 `PanelState` + `action_toggle_panel` + 动态 compose | 中 |
| 2 | `tui/styles.tcss` | 添加 `.panel-hidden` 样式 | 小 |
| 3 | `tui/keybindings.py` | 添加 Ctrl+1/2/3/B/X 绑定 | 小 |
| 4 | `tui/widgets/tools_rail.py` | 新增 ToolsRail 组件（三区域 + 动态布局） | 大 |
| 5 | `tui/widgets/input_autocomplete.py` | 新增 ChatInputAutocomplete 组件 | 大 |
| 6 | `tui/widgets/input_bar.py` | 集成 `/` 联想触发 | 中 |
| 7 | `tui/widgets/hint_footer.py` | 状态栏显示面板开关状态 | 小 |
| 8 | `tui/session.py` | 工具事件路由到 ToolsRail | 中 |
| 9 | `tui/widgets/sidebar.py` | 默认隐藏 | 小 |

---

## 十一、设计总结

| 维度 | 借鉴来源 | 实现 |
|---|---|---|
| 面板开关 | OpenCode | Ctrl+1/2/3 切换 |
| Info Bar 合并 | llmwikify | Session + Stats + Context + Status |
| Goal Progress | 本项目需求 | 量化研究专用，动态高度 |
| Tool Timeline | llmwikify | 10 条可滚动，与 Goal 共享空间 |
| Context Window | llmwikify | 折叠/展开 |
| `/` 联想 | vibe-trading / OpenCode | ChatInput 浮动菜单 |
| 里程碑倒序 | 用户确认 | 最新在上，最多 5 条 |

---

## 十二、待实现

1. ✅ 设计完成
2. ⬜ 实现 ToolsRail 组件
3. ⬜ 实现 ChatInputAutocomplete 组件
4. ⬜ 实现面板开关系统
5. ⬜ 测试验证
