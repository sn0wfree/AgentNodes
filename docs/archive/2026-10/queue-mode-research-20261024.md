# queue_mode 业界调研 — 2026-10-24

> 用途：为 E1 统一入口 `WorkRequest.queue_mode` 默认值拍板提供依据（纪要 §7 遗留问题①：API chat 默认 steer 还是 followup）。
> 对象：OpenClaw · Claude Code · Codex CLI · OpenCode（MiMo Code 上游）· Amp · Gemini CLI · ZCode。
> 结论先行：**业界 6/7 家默认「注入当前 run」（steer），Gemini CLI 是唯一 Enter=followup 默认；我们推导的「steer + injectable=false 显式转 followup」正是 OpenClaw 的原生行为。API chat 定为 steer。**

---

## 1. 总表

| 产品 | 运行中发消息（默认键） | 行为归类 | 显式排队 | 打断 | 注入时机 |
|---|---|---|---|---|---|
| **OpenClaw** | 按渠道/入口配置 | steer/followup/collect/interrupt 四模式 | —（模式本身即配置） | interrupt 独立模式 | runtime 声明 injectable；不支持则留队列 |
| **Claude Code** | 输入即排队，尽快送入 | **steer**（排队只是传输） | 无独立键 | Esc-Esc | 下一个工具调用边界 |
| **Codex CLI** | Enter | **steer** | Tab = followup | Escape | turn 内安全点 |
| **OpenCode**（MiMo Code 上游） | Enter | **steer** | Ctrl+X→Enter | — | 活跃 session |
| **Amp** | 输入即 steering | **steer** | — | Esc-Esc | 尽快（排队即送） |
| **ZCode** | 输入进 turn 队列 | **steer**（「turn 是队列不是函数」） | — | — | 执行循环中注入 |
| **Gemini CLI** | Enter | **followup** | Enter 即排队 | Meta+Enter 立即发 | 当前任务结束后 |

注：Gemini 行为依据第三方文档快照（geminicli.com，官方页面抓取超时），置信度略低。

## 2. 各家细节（附出处）

**OpenClaw**（docs/concepts/session-controller）——语义最完整，四模式 + heartbeat：
- steer：「尝试注入被捕获的活跃 owner」。runtime 明确拒绝该能力则**留在队列**；steer 输入在目标 turn 的 mailbox 上排队，等更早的输入被接受/认领/了结后注入；detached attempt 没有 turn，无从 steer。
- followup：「等待输入保持 FIFO，准入栅栏放行后开下一个 turn」。
- collect：「安静窗后合并**兼容的**等待输入；保留各自的 custody 与路由」——要求发送方权限/可见性/投递契约匹配。
- interrupt：「取消被捕获的活跃 turn，打断输入优先排 next，旧等待输入保序其后」；优先级属于具体输入，撤销旧 interrupt 不能剥夺新预约。
- injectable 不是布尔：是否 owned / admissible / active / **Injectable（后端能力）** / abortable / settled 分开裁决；`allowCollaborativeSteering` 允许同人协作注入他人 turn（沿用现任 turn 身份）。

**Claude Code**——输入显示 queued，在**下一个工具调用边界**送入当前 turn；官方在持续加速（Boris Cherny：「send the queued message as soon as possible, much faster than it did before」）；社区在要求 mid-generation 实时注入（issue #64624）；另有 issue #50246 要求「排队而非被迫打断」。跨 session 消息同样是「在工具调用之间读取，绝不打断运行中的工具」。

**Codex CLI**——三键分工明确：**Enter = steer 当前 turn**，**Tab = 排队下一 turn（followup）**，**Escape = 打断**（community 帖明确区分 steering 与 queuing；issue #8604 记录扩展端曾「发消息即打断」的坑，CLI 端默认排队不打断）；另有云端 `codex queue` / Agents Dashboard 批量下发指令。

**OpenCode（MiMo Code 的上游，MIT fork）**——TUI 文档明文：**「While OpenCode is working, Enter steers the active session; press Ctrl+X, then Enter to queue the prompt for later」**；git 历史显示曾加入排队后很快改为 steer 优先（Reddit 考古）；Web UI 更激进——运行中发送**一律 steer，无排队选项**（issue #44108 是抱怨没有排队口）。

**Amp**（Sourcegraph 独立出的公司）——Thorsten Ball 原话：「**Steering allows you to send a queued message as soon as possible. (Or hit Esc-Esc to interrupt the agent and send the message immediately.)**」——把「排队」与「steer」的关系说透了：排队是传输，steer 是目的地。

**ZCode**（智谱 GLM 桌面工作台）——第三方拆解：「turn 不是一个函数，是一个队列」是内核最反直觉处：用户中途插话（steering）即时注入当前执行循环，follow-up 则等 agent 停下后处理；官方反馈仓有「任务执行中用户输入应走 turn steering 注入」的功能请求（zai-org/feedback#805）。

**Gemini CLI**——Enter 排队、当前任务完成后处理；**Meta/Option+Enter 立即发送**；排队消息可编辑（「Queued Message Editing」）；已知 bug：排队消息可能在工具调用间与响应交错（issue #17282）。

## 3. 共性规律（对我们的校准意义）

1. **「排队」与「steer」不对立**——Amp/Claude Code 的实现都是「排队缓冲 + 尽快注入当前 run」。分界线只有一条：消息进**当前 run**（steer）还是等 **turn 结束**（followup）。
2. **注入永远发生在安全点**（工具边界 / turn mailbox / 阶段边界），从不打断原子操作——印证我们 injectable 真值 + ResearchRuntime 仅 phase 边界可注的设计。
3. **不具备注入能力 → 留在队列**——OpenClaw 明文；与我们「injectable=false 显式转 followup + 事件可见」的规则一字不差，且证明这不违反「不静默降级」（OpenClaw 同样把它当作队列仲裁而非能力降级）。
4. **interrupt 永远是独立显式动作**（Esc / Esc-Esc），没有一家把「发消息」默认做成打断——印证「interrupt 仅 automation + 用户显式 cancel」。
5. **collect 是最稀有模式**——OpenClaw 留给渠道批量（安静窗 + 同权限合并），编码 agent 无一家实现——印证「留枚举位、v1 不实现」。
6. **演进方向站在 steer 一边**——Claude Code 在加速注入、社区在要 mid-generation steering、OpenCode 曾直接砍掉 followup 队列。

## 4. 对 E1 的定稿（2026-10-24 用户确认）

| 来源 | queue_mode | 说明 |
|---|---|---|
| API chat（**含 WebUI**——同走 chat router + SSE，非独立来源） | **steer** | 待定 → steer（业界 6/7 默认 + 我们 run 长的特有代价） |
| CLI / TUI | followup | 不变 |
| scheduler / automation | interrupt | 不变 |
| Director propose | followup | 不变 |
| 用户显式 cancel | interrupt | 不变 |
| （枚举位） | collect | v1 不实现，再评估触发条件见下 |

**steer 契约细则（写进 E1，五条）**：
1. 注入点 = 下一次模型调用 / runtime 声明的安全边界；
2. `injectable=false` → 显式转 followup + 事件可见（OpenClaw 原生同款，属队列仲裁非静默降级）；
3. 注入只允许追加（目录区之后），不得重排已有上下文（保缓存边界）；
4. steer mailbox 内 FIFO；
5. **同一安全点上的多条注入合并为一批渲染**——多条消息作为一个 user 消息组追加，不逐条打断；ContextHub 将注入批次作为整体进追加段（细则 5 把 collect 的合批精髓收进 steer，覆盖「run 活跃时连发碎片消息」的主场景）。

**collect 不实现的理由**：① 主场景已被 steer mailbox + 细则 5 覆盖；② 我们的输入形态（多行输入框/完整提交）不产生 IM 式碎片，七家编码 agent 无一实现；③ 其难点（多发送方 custody/routing/权限兼容检查）在单信任域全是空转。

**再评估触发条件**（可观测，不拍脑袋）：
- 事件流指标「turn 开启后首个安全点内即发生 steer 注入」的频率显著偏高——说明 idle 突发碎片化成为真实浪费；
- 将来接 IM 渠道（飞书/微信机器人下达研究指令）——碎片消息成为媒介天性，collect 从可选变必选。

配套：WebUI 前端可做 150–300ms debounce（纯 UI 优化，不进契约）。

## 5. 出处

- OpenClaw session controller：https://docs.openclaw.ai/concepts/session-controller
- Claude Code：issues #64624 · #50246 · Boris Cherny (Threads) · code.claude.com/docs cross-session messaging
- Codex CLI：openai/codex#8604 · community.openai.com（steer vs queue）· learn.chatgpt.com slash commands
- OpenCode：https://opencode.ai/v2/docs/cli/tui · r/opencodeCLI 考古帖 · anomalyco/opencode#44108 · #8685
- Amp：x.com/thorstenball/status/2052047171692736567 · ampcode.com
- Gemini CLI：geminicli.com/docs/reference/keyboard-shortcuts · google-gemini/gemini-cli#17282
- MiMo Code：github.com/XiaomiMiMo/MiMo-Code（fork 自 OpenCode，行为同上游）
- ZCode：jlj98.top（「turn 不是一个函数，是一个队列」）· zai-org/feedback#805
