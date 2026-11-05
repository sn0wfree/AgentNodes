# TUI 界面对比分析报告

**日期**: 2026-07-26
**目的**: 对比 quantnodes-research 与 vibe-trading / Claude Code / Codex / OpenCode 的 TUI 界面，分析优劣，为后续改进提供依据。

---

## 一、本项目 vs vibe-trading

| 维度 | quantnodes-research (本项目) | vibe-trading |
|---|---|---|
| **框架** | Textual (全屏固定布局) | Rich + prompt_toolkit (流式布局) |
| **布局** | 三栏固定: 侧边栏 + 对话 + 活动轨 | 顺序流: 无固定面板，工具输出内联 |
| **输入** | 单行 Textual Input | 多行 prompt_toolkit (Alt+Enter 换行) |
| **补全** | 点击侧边栏 (无模糊匹配) | 模糊匹配 typeahead (Tab) |
| **流式** | AgentStreamDelta 消息机制 | Rich.Live 瞬态仪表盘 |
| **主题** | Textual CSS + brand_tokens 桥接 | Rich Style 对象 + 品牌色系统 |
| **成熟度** | 开发中 (Commit 1-5) | 生产级 (v0.1.11) |

### vibe-trading 更好的地方
- 多行编辑 (Alt+Enter)
- 模糊 slash 补全
- 瞬态活动仪表盘 (不占固定空间)
- ThinkSpinner (品牌橙色旋转点)

### 本项目更好的地方
- 固定三栏布局 (信息密度高)
- 正式 widget 抽象 (可测试)
- Modal 对话框 (更现代)
- Ctrl+L 清屏

---

## 二、四大 TUI 对比

| 维度 | Claude Code | Codex | OpenCode | 本项目 |
|---|---|---|---|---|
| **布局** | 单栏滚动 | 单栏滚动 | 多栏 + 侧边栏 + 命令面板 | 三栏固定 |
| **输入** | 单行 + @引用 + 图片粘贴 | 单行 + pipe | 多行 + @ + !bash + 外部编辑器 | 单行 |
| **流式** | token-by-token | token-by-token | token-by-token + markdown 渲染 | token-by-token |
| **快捷键** | 极简 (Esc, Shift+Tab) | 标准终端 | leader 键系统 (20+ 快捷键) | 5 个绑定 |
| **主题** | 终端原生色 | 终端原色 | 12+ 内置主题 + JSON 自定义 | 品牌橙 + dark/light |
| **Undo** | 检查点快照 (最强) | 基础 | Git-based undo/redo | 无 |
| **会话管理** | 恢复/fork/后台 agent | 恢复/web 会话 | 会话列表/分享/多会话 | 恢复对话框 |
| **扩展性** | Skills/Hooks/MCP/子 agent | 插件/skills/apps | Skills/agents/MCP/LSP | Skills/agents |

---

## 三、各项目优劣分析

### Claude Code — 极简但强大

**优势**:
- 检查点系统 (每次编辑前快照，可恢复任意历史点)
- 5 级权限模式 (Manual → Accept Edits → Plan → Auto → Bypass)
- 子 agent (保持主上下文清洁)
- CLAUDE.md (项目级持久指令)
- 最佳文档

**劣势**:
- 无主题定制
- 单栏 (看不到文件树/diff 视图)
- 付费订阅
- 厂商锁定

### Codex — 生态整合

**优势**:
- ChatGPT 生态 (现有订阅直接用)
- AGENTS.md (项目级指令)
- Record & Replay (macOS Desktop)
- 企业级 (RBAC, 合规 API)
- 多平台 (CLI/IDE/Desktop/Web)

**劣势**:
- 文档受限 (部分 403)
- TUI 相对简单
- 用量限制
- 社区贡献较少

### OpenCode — 最丰富的 TUI

**优势**:
- 12+ 主题 + JSON 自定义 (最强主题系统)
- Leader 键系统 (20+ 快捷键 + which-key 覆盖)
- Plan/Build 模式切换 (Tab)
- 多行输入 + @引用 + !bash + 外部编辑器
- 会话分享 + 多会话并行
- LSP 集成
- 75+ LLM 提供商
- 免费模型

**劣势**:
- Agent 系统不如 Claude Code 成熟
- 无检查点快照
- 需要 truecolor 终端
- Go 实现 (资源占用较高)

### 本项目 — 有潜力但未完成

**优势**:
- 固定三栏布局 (信息密度高)
- 正式 widget 抽象 (可测试)
- Modal 对话框 (现代 UX)
- 品牌主题桥接 (Rich → Textual CSS)
- 金融领域专用 (goal/hypothesis/validation)

**劣势**:
- 单行输入 (无多行编辑)
- 无模糊补全 (只有点击侧边栏)
- 无 spinner/thinking 指示器
- 无 undo/redo
- 无主题切换
- 未完成 (Commit 1-5 计划中)

---

## 四、改进建议优先级

| 优先级 | 改进项 | 参考 | 工作量 |
|---|---|---|---|
| **P0** | 多行输入 (Alt+Enter 换行) | vibe-trading / OpenCode | 中 |
| **P0** | 模糊 slash 补全 (Tab) | vibe-trading | 中 |
| **P1** | ThinkingSpinner (品牌橙旋转点) | vibe-trading | 小 |
| **P1** | 工具调用内联渲染 (树形 └) | OpenCode | 中 |
| **P2** | Plan/Build 模式切换 | OpenCode | 中 |
| **P2** | 主题切换 (内置 3-5 个) | OpenCode | 大 |
| **P2** | Git-based undo/redo | OpenCode | 大 |
| **P3** | 检查点快照 | Claude Code | 很大 |
| **P3** | 子 agent 面板 | Claude Code | 很大 |

---

## 五、借鉴后的目标界面

### 布局结构（保留三栏 + 增强）

```
┌─────────────────────────────────────────────────────────────────────┐
│ QuantNodes Strategy-Research                            v0.5.0  🟢 │
├──────────┬──────────────────────────────────────┬──────────────────┤
│ Commands │ Transcript                           │ Activity         │
│          │                                      │                  │
│ /help    │ QuantNodes-Research                  │ ● read_file      │
│ /model   │ ===========                          │   config.yaml    │
│ /memory  │ 策略研究 · Quant · Nodes             │   ✔ 0.3s         │
│ /history │                                      │                  │
│ /goal    │ ─────────────────────────────────    │ ● run_backtest   │
│ /search  │ 你: 帮我分析一下均值回归策略在     │   strategy.py    │
│ /swarm   │ 沪深300上的表现                      │   ⏳ 12.4s       │
│ /skill   │                                      │                  │
│ /show    │ ─────────────────────────────────    │ ● get_market_data│
│ /clear   │ 🤖 thinking... (2.3s)               │   daily_ohlcv    │
│ /pine    │                                      │   ✔ 0.8s         │
│ /journal │ 策略名称: 均值回归策略               │                  │
│ /shadow  │ (Mean Reversion)                     │ ─────────────── │
│ /export  │ 标的: 沪深300指数                     │ ● factor_analysis│
│ /debug   │                                      │   momentum       │
│ /halt    │ 关键指标:                            │   ✔ 1.2s         │
│ /resume  │ - 年化收益: 12.3%                    │                  │
│ /quit    │ - 最大回撤: -8.5%                    │ ⏳ 累计 14.7s    │
│          │ - 夏普比率: 1.42                     │ 3 tools · 1.2k   │
│          │ - 胜率: 58.2%                        │ tokens · $0.003  │
├──────────┴──────────────────────────────────────┴──────────────────┤
│ ▸ 帮我分析一下均值回归策略在沪深300上的表现                        │
│                                                    ⏎ Enter  ⌥⌥⏎   │
├─────────────────────────────────────────────────────────────────────┤
│ F1 Help  Ctrl+C Halt  Ctrl+D Quit  Ctrl+L Clear  Tab Switch Mode  │
└─────────────────────────────────────────────────────────────────────┘
```

### 增强特性

1. **多行输入** — Alt+Enter 换行，输入框自动扩展
2. **模糊 Slash 补全** — 输入 / 后模糊匹配，Tab 选中
3. **ThinkingSpinner** — 品牌橙旋转点 + 计时器
4. **工具调用树形渲染** — └ 嵌套，✔/✘ 状态
5. **Plan/Build 模式切换** — Tab 切换，Header 显示
6. **检查点 Undo/Redo** — 每次工具调用前快照
7. **主题切换** — 内置 5 个主题，运行时切换

### 快捷键汇总

| 快捷键 | 动作 | 来源 |
|---|---|---|
| Enter | 发送消息 | 通用 |
| Alt+Enter / Shift+Enter | 换行 | vibe-trading / OpenCode |
| Tab | 模糊补全 / 切换模式 | vibe-trading / OpenCode |
| ↑↓ | 历史导航 / 补全候选 | 通用 |
| Ctrl+C | 中断/停止 | 通用 |
| Ctrl+D | 退出 | 通用 |
| Ctrl+L | 清屏 | 本项目 |
| F1 | 帮助 | 本项目 |
| Esc | 中断当前操作 | Claude Code |
| Ctrl+Z | 挂起 | OpenCode |

### 与现有界面对比

| 维度 | 现在 | 借鉴后 |
|---|---|---|
| 输入 | 单行 | 多行 + 换行 |
| 补全 | 点击侧边栏 | 模糊匹配 + Tab |
| 思考 | 无指示 | Spinner + 计时 |
| 工具 | 文本日志 | 树形渲染 |
| 模式 | 无 | Plan/Build 切换 |
| Undo | 无 | 检查点快照 |
| 主题 | dark/light | 5+ 内置主题 |
| 快捷键 | 5 个 | 12+ 个 |

---

## 六、参考链接

- Claude Code: https://docs.anthropic.com/en/docs/claude-code
- Codex: https://github.com/openai/codex
- OpenCode: https://opencode.ai
- vibe-trading: https://github.com/HKUDS/Vibe-Trading
- Textual: https://textual.textualize.io
- Rich: https://rich.readthedocs.io
