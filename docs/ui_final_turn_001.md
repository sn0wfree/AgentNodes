# Agent 产品竞品设计语言研究：布局范式、交互模型与生成式 UI 策略

Agent 产品的界面正在从「聊天窗口加工具输出」迁移到「多任务并行的工作台」。决定这次迁移质量的并不是配色或圆角，而是三个更底层的问题：界面里的状态从哪里来，审批这件事在屏幕上占据什么位置，模型生成的界面代码被允许触达什么范围。

本报告对十四个产品与子产品的公开设计材料逐条核对后，看到的结论是：四个家族的界面语言表面差异很大，底层却收敛到同一条原则——界面是权威状态的投影，而不是权威状态的副本。两条互相独立的技术路线给出了同一个答案。omp 作者的《Harness Playbook》把「视图即投影」列为五条架构推论之一，明确主张 TUI、Web、远程客户端渲染同一份状态，而不是各自成为新的权威【1】；DeepSeek Harness 用另一套语言抵达同一处——会话是类型化的、带版本的事件日志，系统状态是这份日志的折叠与投影【2】。两个团队之间没有承继关系，这种独立收敛比任何单一来源的论证都更能说明该原则的必要性。

对改版而言，这条原则有直接后果。任何「前端各自保存一份会话状态」的设计，都会在并行、重连、回放这三种场景下立刻分裂成互相矛盾的事实。可复用的资产不是某个产品的配色，而是它对「什么必须独立、什么必须共享」的切分方式。

材料按四个家族组织：终端 TUI 系、桌面 ADE（Agent 桌面环境）系、Web 控制面板系、生成式 UI 层。分族依据是界面的承载形态与首要任务，不是厂商归属——Claude Code 的终端与桌面落在两个家族，OpenClaw 与 DeepSeek Harness 共享同一形态却来自完全不同的团队。

## 一、研究口径与证据分级

三类材料的价值不同，本报告对它们分别赋值。

一手代码与官方文档，包括仓库源码、官方快捷键参考、官方发布说明、官方博客，可以直接支撑界面行为的陈述，例如快捷键、状态词表、面板结构、审批触发条件。逆向解码得到的规格，例如把桌面端打包产物反解后整理出的设计说明，能提供令牌级信息，但性质是第三方重建而非官方文档，本报告引用时显式标注。社区与二手报道提供语境与时间线，其中的数字多为厂商自述或转述，本报告只在能追溯到具体出处时使用，并标注为自述数据。

据此，以下产品的公开设计材料有限，本报告对它们的描述只到可验证的粒度：ZCode 有 API 契约但缺完整视觉规范，MiMo Code 以仓库 README 为主，DeepSeek Harness 的架构文档充分而界面规范不足，Hermes Agent 核心的终端界面资料稀少，Gemini CLI 以外的 Gemini 形态缺少公开材料，Pi 的主题体系分散在多个互不相干的包上。个别产品的文档页在本次抓取中返回失败，相关证据改由 GitHub PR 正文与搜索结果摘要支撑，详见第九节。

## 二、终端 TUI 系的设计语言

### 2.1 Codex CLI 的颜色语义与子代理行内记录

Codex CLI 的 TUI 用 Rust 与 Ratatui 实现【4】【5】，是这一族里把颜色语义写进规范最明确的一个。它的多智能体记录行是范式级参考：当子代理被创建或收到输入时，对话流里插入一行「• Spawned 〈昵称〉 [角色]」，昵称用青色加粗，角色用暗色方括号，派生请求参数（模型与推理档位）用洋红，缩进的详情行以暗色「 └ 」起头【4】。同一份行文案表还规定了「Sent input to」「Waiting for N agents」「Finished waiting」「Closed」「Resuming」「Resumed」六个状态词，以及状态色：运行中青色加粗、完成绿色、错误红色、挂起与关闭灰暗【5】。

值得注意的不是颜色本身，而是同一套词表同时服务两处。对话流里的历史行与 `/agent` 选择器里的列表项共享「昵称 [角色]」的标签契约。早期版本两者并不一致，历史行直接渲染 UUID 而选择器显示昵称，这被作为可读性缺陷提交为 issue【3】。这件事给出的设计约束很具体：同一实体在界面不同位置的标识必须同源，不能由各视图各自拼装。

并行代理的切换键是 `Alt+←` 与 `Alt+→`，macOS 上在终端不支持增强键盘报告时降级为 `Option+B` 与 `Option+F`，且仅在输入框为空时启用以保留单词移动语义【4】。除这两个键外还有一个独立的操作者面板：v0.149.0 引入的 `codex agents` 全屏 TUI 提供搜索、启动、打开、重命名、停止五类操作，与模型侧的六个协作工具并存，前者面向人，后者面向编排者【6】。

生成式 UI 方面，Codex CLI 不内置。它的对应物是桌面端的内置浏览器与预览面板，属于「渲染模型产物」而非「让模型生成界面」。

设计哲学：把并行拆成可被逐行阅读的记录，而不是可被逐行滚过的日志。

### 2.2 Claude Code TUI 的脚手架式对话与后台任务三级检视

Claude Code 终端界面的设计重点从「对话」移到了「后台任务的可见性」。v2.1.290（2026-10-06）给后台任务对话框加了两级结构：底部 `bg_agent` 药丸进入列表，列表项再进入本地代理的详情屏；列表本身改为随窗口宽度收缩的紧凑形态，并对权限请求单独着色【7】。

这个三级结构值得单独注意。它把「有哪些东西在跑」与「这个东西到底在做什么」拆成两个不同粒度的表面，而不是塞进同一份流式输出。脚手架式对话与详情的分离，对应的是「扫一眼」和「追进去看」两种量级不同的认知负荷。

生成式 UI 方面，Claude Code 的终端把会话输出发布成一个活页面。Artifact 是部署在私有 claude.ai URL 上的交互式页面，随会话继续而原地更新，典型用途是带注释 diff 的 PR 导览、由会话数据构建的仪表盘、随任务推进填充的调查时间线【8】。当用户通过 Remote Control 从 claude.ai 或移动端发起提示时，会话所在机器不会开标签页，浏览器会在下一次发布时在那一端打开【8】。

发布与能力声明的约束同样明确。官方文档列出的提问时机有四类【8】：

- 会话处于 plan 模式时，每个 artifact 首次发布前都问
- 页面声明了运行时能力（连接器调用、文件下载）时，在该次发布前问
- 用户公开分享时，每次对话问一次；Claude 在未获批准时无法读取文件时，发布前问
- 针对 `toolArtifact` 的放行规则不绕过以上任何一条

另有一条独立约束：artifact 是一次工作的捕获，一个没有后端的独立页面，无法服务多条路由【8】。

设计哲学：把「终端不适合承载的输出」整体推给一个可分享的活页面，而不是在终端里做缩水的富媒体。

### 2.3 Gemini CLI 的形态收缩与审批模式常驻

Gemini CLI 是这一族里形态收缩做得最激进的一个。它把「最小界面 / 完整界面」做成了可用 Tab+Tab 切换的常驻形态，选择会被记住【10】。同一份快捷键参考里，审批状态也被提到一等位置：`Shift+Tab` 在 default、auto_edit、plan 三种审批模式间循环，`Ctrl+Y` 切换 YOLO 自动批准，`/plan` 直接进入只读模式并查看当前计划【10】【11】。

更独特的是后台 shell 的焦点管理。`Ctrl+B` 切换后台 shell 列表，`Ctrl+O` 把焦点从 Gemini 输入移到 shell 输入，`Ctrl+K` 从后台 shell 移回，`Ctrl+L` 杀掉当前后台 shell，`Shift+Tab` 把焦点从后台列表移回【10】。它同时给出焦点离开 shell 时的警告，把「焦点在别处」这件事显式化，避免误触。

此处有一处需要读者注意的来源冲突。较早一代的 Gemini CLI 快捷键文档里，`Ctrl+O` 是调试面板、`Ctrl+S` 是展开被截断的长响应、`Ctrl+Y` 是 YOLO、`Ctrl+B` 是光标左移一个字【9】；新参考文档把 `Ctrl+O` 改为聚焦 shell 输入、`F12` 用于错误详情、`Ctrl+B` 分配给后台 shell【10】。新表里 `background.toggle` 与 `background.toggleList` 两个命令还共用 `Ctrl+B` 一个键【10】。改版若要参考 Gemini 的焦点模型，应以新表为准，并把焦点切换与命令绑定解耦。

生成式 UI 方面，Gemini CLI 不内置。

设计哲学：把「当前信任级别」做成键盘上随时可达的显式状态，而不是配置项。

### 2.4 OpenCode 的双 agent 语义与两代并存

OpenCode 当前维护的仓库发布 npm 包 `@opencode/cli`，v2 安装脚本另行分发，桌面应用（macOS arm64 与 x64、Windows x64、Linux 的 deb / rpm / AppImage）标注为 BETA【12】。

内置 agent 只有两个，用 Tab 切换：`build` 是默认的全权限开发 agent，`plan` 是只读的分析 agent，默认拒绝文件编辑并在执行 bash 前询问权限【12】。此外还有一个 `general` 子代理处理复杂检索，可用 `@general` 在消息中调用【12】。这两个 agent 的权限语义是显式定义的，而不是从模型行为中推断出来的，这在一个产品同时要覆盖「让模型改代码」和「让模型只读代码」两种场景时是必要的。

更值得产品设计注意的是版本代际问题。Macaron Artifacts 为 OpenCode 做了两代适配：v1 固定 `@opencode-ai/sdk@1.18.29` 并连接本地 CLI 服务器，v2 使用 `@opencode/client@2.0.20` 的 `OpenCode.make` 与 `/api/session`；由于两代各自打开不兼容的 `opencode.db` 结构，v2 子进程必须改用独立的 `macaron-artifacts-v2.db`【37】。落到界面上的后果是，会话选择器里「OpenCode v1」与「OpenCode v2」必须并列为两个相邻选项且互不迁移。同时，早前的 `opencode-ai/opencode` 仓库（Go 与 Bubble Tea 实现）已被归档为「保留出处」，项目以 Crush 的名字继续演进【13】——评估 OpenCode 界面时必须先确认指的是哪一个代际，两者的交互模型完全不同。

生成式 UI 方面，OpenCode 本身不生成界面；但它的插件体系为第三方预留了宿主侧 UI 槽位。这是「生成式 UI」与「可插拔 UI」两种不同机制的边界，改版时值得明确区分。

设计哲学：用两个写明权限语义的主 agent 覆盖全部任务，把复杂度放进工具与配置。

### 2.5 MiMo Code 的上下文预算与停止判定

MiMoCode（仓库 `XiaomiMiMo/MiMo-Code`）是小米开源的终端原生编码助手。README 自述的核心定位是 terminal-native：一个会话可以处理办公、设计、编码与多模态创作，多个会话可以分工协作；桌面应用以 MiMo Code 为内核，提供模型、框架与工具的动态编排，多 agent 并行，区域级精确编辑与版本回滚，以及内置浏览器控制【14】。

对界面设计最有参考价值的是它把「运行状态」做成了可读数字而不是氛围元素。提示符页脚显示 `33.0K/260K↓ (13%)`，`↓` 表示当前生效的是一个被强制调低的压缩预算，`/status` 给出分项拆解【14】。`/context-limit` 允许按模型设定 200K、300K、500K、1M 的工作预算，且只能调低不能调高，理由写得很直白：同一个模型经不同渠道（订阅、官方 API、第三方中转）拿到的可用窗口可能不同，标称 1M 不等于这条链路给你 1M【14】。这是一条通用的界面规范——上下文预算必须显示实际生效值而非名义值。

三个把可靠性做成界面机制的设计同样值得记录。`/goal` 设置停止条件，由一个独立的裁判模型在 Agent 试图停止时评估条件是否真的满足，用来防止过早的「乐观停止」；`/dream` 扫描近期会话轨迹，把持久知识提取进项目记忆并清理过期条目；`/distill` 从近期工作中发现重复的手工流程，打包成可复用的 skill、子代理或命令【14】。

持久记忆是四层文件而非单一向量库：`MEMORY.md` 放项目知识与规则，`checkpoint.md` 是由 checkpoint-writer 子代理维护的结构化状态快照，`notes.md` 是草稿区，`tasks/<id>/progress.md` 记录任务级进度；会话恢复时按预算与重要度排名自动注入【14】。配置同样分层，`mimocode.jsonc` 管模型与 provider，`tui.json` 管 TUI 偏好，两者都发布 JSON Schema 供编辑器补全【14】。

远程形态是 `mimo serve --port 4096` 加上本地 `mimo attach http://127.0.0.1:4096` 的端口转发配对，官方明确建议 SSH 场景下把渲染留在本地以避免 TUI 卡顿【14】。同一条「渲染在本地」的逻辑也体现在 `/vivid` 与 `ctrl+p` 命令面板里的 Vivid visuals 选项上，官方给出的关闭理由就是装饰性动画造成卡顿【14】。

生成式 UI 方面，仓库未内建；桌面端「生成 PPT、网页、3D 资产与应用并在会话内预览与操作」属于产物预览，不是可交互界面的生成式 UI【14】。

设计哲学：把上下文预算、停止判定与记忆维护都做成看得见的机制，而不是模型内部的黑箱。

### 2.6 Pi 的五层骨架与社区扩展边界

Pi 的界面材料分散在多个互不相同的包上，混起来看会得出错误结论。以 `pi-one-ui` 为例，它定义的是五层垂直结构：Header、Transcript、WorkingLine、Editor、Footer，外加一个覆盖层；0.9.1 版本把其中的 Context 改名为 Transcript，并在编辑器处提供 Minimalist 极简样式，可一键切回 Pi 原生界面【15】。

而 droid、gemini、cli-dock、nvim 这四套编辑器风格预设，属于 `@mightguy/pi-ui` 与 `@sting8k/pi-droid-styling`，是社区扩展，不是 `pi-one-ui` 的内置能力【16】。两者的设计取向也相反：`pi-one-ui` 是固定的五层骨架加一个极简开关，`pi-ui` 是让用户在多套风格之间挑选。

对改版而言，这条区分是必需的。把「预设风格」当成某个产品的原生能力，会把社区扩展的机制误记为产品基线。Pi 核心与社区扩展在这一点上的界线，与 omp 的做法一致：omp 仓库把 TUI 库 `@oh-my-pi/pi-tui`（差分渲染）、子代理编排、Agent Hub（`Alt+A`，显示每个子代理的状态、成本与转录）都放在 monorepo 的独立包里，而不是混进核心【17】。

生成式 UI 方面，Pi 本身不生成界面；但它已被 Macaron Artifacts 作为一等宿主接入，见第五章。

设计哲学：界面骨架固定为可数的几层，把风格变化交给可替换的扩展。

## 三、桌面 ADE 系的设计语言

### 3.1 Codex 桌面的并行工作台骨架

Codex 桌面端是「并行工作台」这一命题最完整的产品化。三栏分区在多份材料中一致：左栏管理项目、线程与自动化，中栏是对话与工作区，右栏是审阅面，承载 Review、Diff、文件改动、Git 状态与预览【20】。底部控制条负责切换运行模式（Local / Worktree / Cloud）、选择模型与执行环境，右上角直接放置 Open、Commit 与 diff 统计，把「改完之后怎么办」从「切到终端敲命令」压缩成「在同一个窗口点一下」【20】。

官方口径补上了两处结构性变化。侧栏可以直接打开文件，并对 PDF、表格、演示文稿与文档提供富文本预览；新增的摘要面板用于实时追踪 Agent 的任务计划、参考来源与产出成果【18】。2026 年 7 月起，Codex 桌面端整体升级为新版 ChatGPT 桌面应用，ChatGPT 下分 Chat 与 Work 两段，Codex 与之并列并保留独立视图与独立历史【19】。

并行能力的物理前提是 Worktree 隔离：每个任务在独立的 `git worktree` 副本中操作，避免并行 Agent 互相覆盖【20】。这条约束反过来定义了右栏的形态——当五个线程各自改了不同文件时，审阅面板必须把五个并行任务的改动聚合到一个视图里，否则并行的收益会被审阅成本吃掉【20】。Computer Use（2026-04-17）进一步把操作面从代码扩到桌面：Codex 拥有独立光标，能读屏、点击、输入，并允许多个 Agent 在同一台 Mac 上并行工作而互不干扰【18】。

生成式 UI 方面，Codex 桌面端提供的是内置浏览器与预览面板，可以在页面上直接标注，把元素级的意见转化为对智能体的精确指令【18】。这是「渲染并操控模型产物」，不是「模型生成界面代码」。

设计哲学：把并行的产出聚合到一个可操作的审阅面，而不是让用户自己在多个窗口间比对。

### 3.2 Claude Code 桌面端的面板拓扑与产物权限

Anthropic 对 Claude Code 桌面端的改版（2026-04-14）给出了同一命题的另一种解法：面板拓扑交给用户，而不是固定分栏。官方的描述是「为智能体编程的真实感受而建——同时有多件事在飞，而你坐在编排者的位置」【21】。

侧栏收纳全部活跃与近期会话，可按状态、项目、环境过滤，也可按项目分组；会话的 PR 合并或关闭后自动归档，使侧栏保持聚焦于活着的工作【21】。侧聊（`⌘+;` 或 `Ctrl+;`）从主线程取上下文但不回写，以免误导正在进行的任务【21】【22】。集成终端、应用内文件编辑器、为大变更集重建的 diff 查看器、扩展到 HTML 与 PDF 的预览面板都进了应用内，每个面板都可拖拽成任意网格【21】。此外还有三档视图模式（Verbose、Normal、Summary）用来调节工具调用细节的透明度，以及一个同时显示上下文窗口与会话用量的用量按钮【21】。会话隔离依赖 Git worktree，路径默认为 `<项目根>/.claude/worktrees/`，并配有新建与切换会话的快捷键【23】。

与 Codex 桌面端对照，两者的差别不在功能清单，而在编排权的归属。Codex 把审阅面固定为右栏并把提交动作压缩到右上角，隐含假设是「并行任务有固定形态」；Claude Code 则让面板拓扑本身由用户决定，隐含假设是「并行任务的形态取决于任务」。

Routines 与 `/team-onboarding` 是这次改版里体量最大的两项新增。Routines 把一段提示词、一组仓库与所需连接器打包成可按需或按计划运行的配置，分定时、API 与 webhook 三种形态，运行在 Anthropic 托管的云基础设施上，因此合上笔记本后仍会继续；日限额为 Pro 5、Max 15、Team 25、Enterprise 25【24】。`/team-onboarding` 读取本地 Claude Code 的会话、提示词、连接器与常用仓库，生成一份给新同事上手的引导文档【24】。这两项把产品从「操作当下」推向「预置未来」。

生成式 UI 方面，Claude 的 Artifacts 是最早把模型产物一等公民化的实现之一。Artifact 在主对话右侧的专属窗口中打开，可编辑、可迭代、可回看、可分享；创建判据被明确写出——内容显著且自足，通常超过 15 行，用户可能想编辑或复用，无需额外对话上下文即可独立成立，用户之后可能回头引用【25】。模板体系覆盖 Docs、Slides、Design、Data，Sheets、Whiteboards、Motion 处于 beta【25】。持久化存储区分个人与共享两档，共享档首次使用有确认弹窗；新 artifact 默认 100 MB 与 25,000 条记录，上传文件单个不超过 20 MB，一个 artifact 内最多 5,000 个文件、合计 1 GB，而 2026-09-16 之前生成的旧版 artifact 仍受 20 MB 且仅限文本的限制【25】。

对改版最有参考价值的是 Artifacts 的权限设计。MCP 集成让 artifact 可以读写 Asana、Google Calendar 这类外部服务，但用户首次交互时会被提示批准该 artifact 的访问权限，且批准结果对该 artifact 后续使用持续生效；组织管理员可以在组织层面关闭 artifact 的 MCP 访问，却不能管理单个 artifact 使用哪些 MCP 服务器【25】。这条分工把「能做什么」和「能被谁改」分成两个独立的权限维度。

设计哲学：让并行面板的拓扑由用户定义，而让产物的能力边界由系统显式声明。

### 3.3 Codex 桌面设计令牌的逆向解码规格

这一节的产品是规格本身。第三方把 Codex 桌面端打包产物反解后整理出一份设计说明，给出的是一套可直接查表的令牌层级：

| 令牌类别 | 取值 |
| --- | --- |
| 基准单位 | 4 px |
| 间距 | 4、6、8、10、12、16、20、24、32 |
| 中性色阶 | `#FFFFFF` `#F9F9F9` `#F3F3F3` `#EDEDED` `#AFAFAF` `#5D5D5D` `#4F4F4F` `#414141` `#303030` `#282828` `#212121` `#181818` `#0D0D0D` |
| 文本色 | `#1A1C1F`（浅色背景）、`#FFFFFF`（深色背景） |
| 强调色 | `#339CFF`；强调面 `#E5F3FF`（浅）、`#00284D`（深） |
| 成功色 | `#00A240`（浅）、`#40C977`（深） |
| 圆角 | 2 至 24 px |
| 字号与字重 | 12、13、14、16、18、20、24、28 px；字重 445 至 500 |
| 版式 | 内容最大宽度 48 rem；页面边距 20 px；侧栏默认 300 px，夹在 240 至 520 px 之间 |
| 工具栏高度 | 46、36、40、28 px |
| 首页 | 恰好四张快速开始卡片 |

这份表最值得注意的地方是中性色阶占了十三级，而整张表里带语义的彩色只有两处：强调蓝与成功绿。层次表达几乎全部由灰阶承担。

同样重要的是它禁止了什么：渐变、辉光、玻璃拟态、粗描边、饱和的层级关系，以及同一视图内出现多个同等分量级的行动号召【26】。这是一份相当克制的视觉语法——颜色只用来标记状态与品牌，不用来划分层级。

另一份社区皮肤文档给出了同一产品被换肤之后的面貌：冷灰渐变侧栏、反色的用户气泡、细线式过程记录、大圆角输入区、默认 360 px 的右栏，以及横跨对话区与工作台的顶栏【27】。两份材料并置可以看出，令牌化之后允许变化的只有皮肤（颜色与圆角），而布局骨架（顶栏、对话、右栏、输入区位置）是不变量。

需要明确性质：这两份都是第三方逆向与社区复刻产物，不是官方设计文档【26】【27】。它们的价值在于给出一套可检验的具体数值，而不是可以直接引用的官方规范。

设计哲学：用灰阶承担层次，让颜色只做状态标记与品牌标记。

## 四、Web 控制面板系的设计语言

### 4.1 OpenClaw Control UI 的多侧栏与常驻仪表盘

OpenClaw 的控制界面由网关托管的 Web 单页应用构成。Chat 与 Dashboard 并置：每个线程既有对话，也有一块由实时 widget 组成的仪表盘网格，widget 的能力被划为 net、data、actions、prompt 四类，授权绑定到被批准的确切字节与修订号上【28】【29】。聊天区可停靠到左、右、下三边，可调整尺寸也可隐藏，标签页会记住停靠位置，尺寸预设提供 small、medium、large、extra-large 四档；仪表盘在 `/new` 与 `/reset` 之后依然保留【29】。

最能说明问题的是 2026 年 7 月合并的多侧栏改造。Chat、Details 与 Discussion 三栏默认各自独立打开，也可以合并为标签页，或被拖出并调整宽度；面板的 DOM 在标签切换、拖拽与响应式变化中保持挂载，窄屏下多栏折叠为单个标签列而不丢状态；布局、宽度与折叠的活动面板按「规范化会话加网关作用域」持久化【28】。注意这里持久化的是每个会话的布局偏好，而不是一份全局设置。

这个设计的价值在于它承认了一件事：控制台的使用者不是「一个人看一个线程」，而是「一个人同时看几个线程的不同侧面」。多侧栏因此不是布局花样，而是把同一个会话的不同侧面摆到同一屏。

生成式 UI 方面，MCP 应用可以被固定为 widget，纳入同一套仪表盘网格【29】。

设计哲学：把一个会话拆成可独立开启的侧面，并让它们的布局随会话持久化。

### 4.2 DeepSeek Harness 的事件日志折叠与投影

DeepSeek Harness 于 2026-08-13 以 MIT 协议开源，定位是「一切皆插件」的 agent harness，运行时基于 Cordis 微内核，Agent 循环、工具与 Web UI 都是可以组合、扩展或替换的插件【2】。Web UI 由 `npx @deepseek-ai/dsh web` 在 127.0.0.1:3080 上提供，默认只监听回环地址；界面左侧是可折叠的 Workbench 侧栏（展开列或 56 px 窄轨），中间是工作区，右上角是明暗主题切换【30】。

对界面设计而言最有价值的是它的状态模型：会话是类型化的、带版本的事件日志，系统的全部状态都是这份日志的折叠与投影；会话日志默认 zstd 压缩，可无密钥回放，在快照测试中复现转录【2】。这与 omp《Harness Playbook》主张的唯一权威会话是同一个结论的两种实现语言【1】【2】。另有五种子代理后端（进程内 spawn、进程内 fork、ACP、Claude Code、Codex）被统一成同一种任务外观，用户不需要知道背后是哪种集成【2】。

生成式 UI 方面，官方仓库未内建；社区另有 `dsh-generative-ui` 插件把 UI4A 接到其 Web 客户端上【37】。

设计哲学：界面上的一切都能由会话日志重建，前端不持有独立事实。

### 4.3 ClawPanel 的多引擎外壳与视觉代价

ClawPanel 是把多个异构 harness 装进同一个外壳的尝试：Tauri v2 加原生 JavaScript 与 Vite 前端，占用 1420 与 18789 两个端口，README 列出 OpenClaw、Hermes Agent 与 DeepSeek Harness 三个一等引擎【31】。界面层面是十个页面、玻璃拟态风格的 CSS 变量主题、十一语言，以及一个用于安装、诊断与修复的内置 AI 助手【31】。

这个产品与第三章的中性令牌方向形成直接对照：ClawPanel 选择玻璃拟态，而 Codex 桌面端的逆向规格明确禁止玻璃拟态【26】。这不是谁对谁错的问题。ClawPanel 服务的是「一个界面监控多个不同引擎」的运维场景，Codex 桌面端服务的是「一个窗口内并行多个编码任务」的高密度工作场景，两者对视觉噪声的容忍度本就不同。真正可迁移的经验是它的引擎抽象：不同引擎的能力与状态在同一个外壳里被归一化。

需要标注的不确定性是：仓库的标签与镜像标题里还出现过 OpenCode 与 Pi，但 README 正文只声明三个引擎，二者是否属于一等引擎未在公开材料中确认【31】。

生成式 UI 方面，未见公开证据。

设计哲学：一个外壳容纳多种引擎，代价是必须容忍各引擎视觉语言的不一致。

### 4.4 Hermes 三个同名项目的界面归属

「Hermes」这三个字在公开材料里指向三个不同项目，混谈会得出错误的界面结论。`NousResearch/hermes-agent` 是 Python 内核；`pyrate-llama/hermes-ui` 是一个单文件 HTML 应用，React 18 加玻璃拟态，通过 Python 代理提供聊天、内联图片、实时终端、会话日志、任务表、统计面板、skill 浏览、定时任务管理、MCP 连接与工作区文件浏览【32】；`nesquena/hermes-webui` 则是另一套深色三栏界面，带环形上下文指示器、输入栏底部的模型与配置与工作区控件，以及 Tasks 看板、Spaces 与 Profiles【33】。

玻璃拟态来自前者，环形上下文指示器来自后者。把两者并置成一个「Hermes 界面」，会把两个项目的特征错误归因。`hermes-agent` 核心本身的终端界面设计资料相当有限【34】。ClawPanel 把 Hermes Agent 列为一等引擎时，指向的应是内核而非上述两个 Web 界面【31】。

生成式 UI 方面，Macaron Artifacts 通过 Hermes 的无头 JSON-RPC 网关接入它【37】；两个 Hermes Web 界面自身未见内建生成式 UI。

设计哲学：可观测性优先——把上下文预算、终端输出与任务状态同时暴露在同一个面上。

## 五、生成式 UI 层的设计语言

### 5.1 ZCode Gen UI 与 UI Plugin 的组件级状态契约

ZCode 是 Apache-2.0 协议的开源项目（`zai-org/ZCode`），2026-09-20 开放源码，采用桌面、Web、服务端与 CLI 的 monorepo 结构【35】。生成式 UI 与 UI Plugin 在 v3.15.1（2026-10-12）引入，这一版本号与官方发布说明一致【35】。

Gen UI 的 API 契约有三条值得记住【36】：

- `window.zcode.widgetState` 暴露当前组件状态，`setWidgetState({modelContent, privateContent})` 用于回写，其中 `privateContent` 明确不进入模型上下文，状态体积上限 16 KiB
- `sendFollowUpMessage({prompt, title})` 把组件内的交互转成新一轮对话
- `zcode:set_globals` 用于全局变量注入

UI Plugin 走 MCP Apps 规范，通过 `ui.surfaces` 声明可出现的表面，通过 `tool_meta.ui.resourceUri` 指向界面资源，并通过 `requestDisplayMode(inline | fullscreen)` 请求显示模式；SDK 为 `@modelcontextprotocol/ext-apps` 2.0.0 与 MCP server SDK 1.29.0【36】。一个现实约束是：交互式页面只支持在本地桌面工作区中使用【36】。

这三条契约合起来定义了一种与 Macaron 截然不同的生成式 UI 哲学。ZCode 把生成式 UI 建模成有明确可见性边界的组件：状态被拆成对模型可见与不可见两半，体积有硬上限，显示模式由组件提出请求但受宿主约束。

设计哲学：给生成式 UI 一份写明可见范围、体积上限与显示模式的契约。

### 5.2 Macaron Artifacts 与 UI4A 的双载体与宿主令牌

Macaron Artifacts 是一个本地 Web 界面（默认 127.0.0.1:43860），把 Claude Code、Codex、OpenCode、pi、Hermes、OpenClaw 六个 harness 装进同一套对话、审批、Canvas 与主题中【37】。它的生成式 UI 层 UI4A 有两种载体：内联的 `ui4a/tsx` 围栏块在助手消息里随源码到达而渲染，Canvas 文件 `.artifacts/canvases/<name>.ui4a.tsx` 在侧面板打开并跨轮次保留【38】。

渲染机制上，UI4A 使用 `partial-react` 的 `pushCode` 与 `finish` 协议，语法不完整时保留上一帧可用画面，并共享同一个 React 实例，使状态在增量更新之间存活【38】。宿主能力被拆成三个独立通道：`$ui4a/chat` 在显式用户动作后发出一条可见消息，`$ui4a/state` 提供会话与表面作用域的 JSON 持久化，`$ui4a/fs` 只能读写 `.artifacts` 目录以下【38】。生成规则里有一条明确的显式动作原则：会发消息或写文件的生成式 UI 回调必须由用户显式动作触发；但「为回答问题而写 Canvas」不需要额外确认【38】。

主题机制对改版的参考价值可能高于渲染机制本身。Shiki 主题调色板被映射成语义化 CSS 变量，由宿主的 UI 与生成的组件共用同一套令牌；控件颜色沿用 VS Code 的组件色角色（`input.*`、`dropdown.*`、`sideBar.*`、`titleBar.*`、`editorWidget.*`、`menu.*`），缺省边框保持透明而焦点边框独立；半透明颜色按所属区域合成后再做对比度校验【37】。也就是说，生成式 UI 不自带设计系统，它寄生在宿主的令牌体系上。

安全边界写得同样直接：生成的代码作为可信 React 代码在宿主页运行，这不是针对不可信第三方代码的隔离沙箱；文件桥接访问限制在所选工作区的 `.artifacts` 树内并做符号链接检查【37】【38】。文档给出的撰写规则本身也是一份设计规范——事实用散文，机制与对比用聚焦的内联探索器，量级用带基线与单位的紧凑图表，完整总览放 Canvas 而选中阶段内联；阅读型控件（标签、筛选、图表选中）保持本地状态，只有发送消息与写文件才需要显式动作【38】。

供应商自述数据需谨慎对待。项目方称在 Mind Lab 自建的 48 例图库上，UI4A 相比原始 HTML 少用约 45% 的输出 token【37】。该数字来自项目方自己的评测，未经独立复现；仓库的 npm 周下载量为 0、维护者 1 人，采纳面仍然很窄【37】。

设计哲学：生成式 UI 寄生在宿主令牌上，用两个载体分别承接「一次看完」与「反复使用」。

## 六、跨产品的设计原理

### 6.1 视图即投影

三个互不相关的团队从三个方向逼近同一条结论。omp 的《Harness Playbook》把唯一权威会话与视图即投影并列为五条架构推论，并给出失败证据：pi 与 omp 的日志只覆盖消息树，权威状态（todo、重试计数、子 Agent 注册表、流式标志）活在日志之外，存在两个真相来源，于是 rewind、fork、resume 全都在「说谎」；官方 78 个扩展示例中 60 个无状态，17 个有状态的只有 2 个正确【1】。Codex CLI 在实现层面做了同样的事：每个子线程有独立的事件通道，非活动线程的事件被缓冲进 store 而不转发，切换线程时从 store 取快照、重放事件、再接住快照与激活之间的增量【5】。DeepSeek Harness 用第三种语言表达了同一件事：状态是事件日志的折叠与投影【2】。

这条原理的界面含义是具体的：切视图、刷新页面、重连、切换终端与 Web，都不应该导致状态丢失或分叉；任何一个视图都不该持有别的视图不知道的事实。它同时解释了为什么 OpenClaw 把布局偏好按「规范化会话加网关作用域」持久化而不是做成全局设置【28】——布局本身也是投影的一部分。

### 6.2 审批面与观察面的分离

第二批产品不约而同地把「需要人介入」从消息流里抽出来。

- Codex CLI 用颜色承担语义，运行中青色、成功绿色、错误红色、模型与推理档位洋红【5】
- Gemini CLI 把审批模式做成键盘上循环切换的常驻状态【10】
- Claude Code 的后台任务详情页对权限请求单独着色，与其他状态行区分开【7】
- Codex 桌面端把逐行批注与按块接受、回滚放进固定的右栏【20】

这些做法共同回答了同一个问题。一个审批请求如果混在流式输出里当作普通工具结果渲染，人在并行场景下根本不会注意到它。Claude Code 对 artifact 发布的处理又进一层，它把提问条件写成四类明确规定，并规定 `toolArtifact` 的放行规则不得绕过【8】。改版时应当给审批一个独立于消息流的视觉身份与位置，并且让它可排队、可计数，而不是每次弹窗。

### 6.3 内联与画布的分工

生成式 UI 的两个主流做法都显式区分了内联与画布，但分工依据不同。

- ZCode 让组件自己提出 `requestDisplayMode(inline | fullscreen)` 的请求【36】
- Macaron 的撰写规则按内容性质分配，需要持续状态、多控件、要反复迭代的进 Canvas，一次看完的小交互内联【38】
- Claude Code 的 Artifact 是「当终端文本不是合适载体时才用」的兜底产物，且明确它是「一次工作的捕获」，不是能服务多路由的有后端应用【8】
- Claude 桌面端的预览面板承接 HTML、PDF 与本地服务【21】，Codex 桌面端承接文件与富文档预览【18】

一致的结论是，分工依据不应是「内容多大」，而应是「这个界面接下来还会不会继续被用」。内联承载一次性交互，画布承载有生命周期的对象。

## 七、横向对比矩阵

矩阵按家族排序，用于查找同类产品之间的相对位置，而不是评分。

| 产品 | 布局模型 | 交互模型 | 视觉密度 | 生成式 UI 能力 |
| --- | --- | --- | --- | --- |
| Codex CLI 【4】【6】 | 单列对话流加底部 composer | 模型编排与操作者面板双轨 | 高（多行 diff、事件行、状态色） | 无；产物由桌面端预览承接 |
| Claude Code TUI 【7】 | 单列对话加脚手架式详情 | 后台任务三级检视 | 中高 | 会话输出发布为活页面 Artifact |
| Gemini CLI 【10】 | 单列对话，可切最小与完整形态 | 审批模式常驻，后台 shell 焦点管理 | 中 | 无 |
| OpenCode 【12】【13】 | 单列对话加两级 agent | Tab 切换 build 与 plan，plan 默认拒绝写文件 | 中 | 无；插件槽位供宿主侧注入 |
| MiMo Code 【14】 | 单列对话加页脚上下文预算 | 独立裁判模型判定停止条件 | 中 | 无；桌面端仅产物预览 |
| Pi（pi-one-ui）【15】【16】 | 五层垂直结构加覆盖层 | 极简样式开关；风格预设属社区扩展 | 低 | 无 |
| Codex 桌面 【18】【20】 | 三栏加底部模式控制条 | 并行线程加 Worktree 隔离加逐块审阅 | 高 | 内置浏览器与预览，非生成式 |
| Claude Code 桌面 【21】 | 可拖拽面板网格加会话侧栏 | 侧聊不污染主线程；三档视图模式 | 中高 | Artifacts：侧栏、模板、MCP、持久存储 |
| OpenClaw Control UI 【28】【29】 | 多侧栏加仪表盘网格 | 停靠与合并，布局按会话持久化 | 高 | MCP 应用可固定为 widget |
| DeepSeek Harness 【2】 | 左侧可折叠 Workbench 侧栏加中部工作区 | 状态由日志折叠投影；子代理后端统一外观 | 中 | 未内建；社区插件接入 |
| ClawPanel 【31】 | 十页多引擎外壳 | 跨引擎统一入口加内置诊断助手 | 中（玻璃拟态） | 未见公开证据 |
| Hermes（两个 Web 项目）【32】【33】 | 单文件玻璃拟态应用；深色三栏加环形上下文 | 两套独立实现，非同一产品 | 中 | 无 |
| ZCode 【35】【36】 | 对话加交互式页面 | 组件级状态回写，model 与 private 分离 | 中 | Gen UI 与 UI Plugin，走 MCP Apps |
| Macaron Artifacts 【37】【38】 | 统一对话加 Canvas 侧面板 | 六个 harness 共用审批与主题 | 中高 | UI4A 内联加 Canvas，寄生宿主令牌 |

矩阵读出的三条规律。第一，布局模型与交互模型强相关：凡是「一个人同时管多件事」的场景（Codex 桌面、Claude Code 桌面、OpenClaw）都长出了多栏或可拖拽网格；凡是「一个人盯一个会话」的场景（Codex CLI、MiMo Code、Pi）都收敛为单列。第二，视觉密度由并行度决定，不由功能量决定——Codex CLI 的视觉密度高于 Claude Code 桌面，不是因为它功能更多，而是因为它必须在终端行宽内表达更多状态。第三，生成式 UI 能力与宿主令牌体系强相关：真正实现了生成式 UI 的两个项目（ZCode、Macaron）都把组件状态做了可见性切分，Macaron 进一步让生成组件寄生宿主令牌；反过来说，一个没有令牌体系的宿主很难安全地开放生成式 UI。

## 八、改版决策建议

以下五条按可实施顺序排列，每条给出判断依据与落地形态。

### 8.1 单一权威会话日志与视图投影

依据是 omp 与 DeepSeek Harness 独立收敛到同一结论【1】【2】，Codex CLI 的线程事件缓冲与快照重放提供了可照搬的实现形态【5】。

落地方式是会话以类型化事件日志为唯一真相，每个前端只做 fold 与渲染，不持有第二份状态；切视图、刷新、重连、跨终端与 Web 切换都必须能由日志重建。为这条性质补上的测试不是「界面显示了什么」，而是「同一份日志在不同前端投影后是否得到同一组事实」，即快照回放测试。

### 8.2 中性令牌体系与克制的视觉语法

依据是 Codex 桌面端的逆向规格给出可检验的数值体系（4 px 基准、灰阶承担层次、单一强调蓝、成功绿、字号 12 至 28、字重 445 至 500），并明确禁止渐变、辉光、玻璃拟态与多个同级行动号召【26】；对照可见 ClawPanel 与 Hermes 界面选择玻璃拟态【31】【32】，说明这是一条需要主动选择的路线而非默认路线【26】。

落地方式是定一份中性色阶加一个强调色加一个成功色，把渐变、辉光、玻璃拟态与粗描边写进禁止清单，同一视图只允许一个主行动号召。令牌以机器可读形式存在并被生成式 UI 组件共用，照 Macaron 的做法让编辑器调色板映射成语义化 CSS 变量，再同时供宿主 UI 与生成组件使用【37】。

### 8.3 审批请求的一等表面化

依据是五个产品各自独立地把审批从消息流中分离出来【5】【7】【10】【20】【8】。

落地方式是审批获得独立于对话流的视觉身份与固定位置，可排队、可计数、可批量处理；当前信任级别（自动批准、提示、只读）常驻可见，用键盘直接循环而不是藏进设置；对「会声明新能力的产物」单独设一条审批通道，且配置放行规则不得绕过它【8】。

### 8.4 生成式 UI 的载体分工与作用域契约

依据是 ZCode 把状态拆成 `modelContent` 与 `privateContent` 并设 16 KiB 上限、要求组件显式请求显示模式【36】；Macaron 拆出 chat、state、fs 三条宿主能力通道并要求显式动作原则【38】；Claude Artifacts 对 MCP 访问采用「首次交互批准、结果对该 artifact 持续生效、管理员只能整体开关」的三层权限【25】。

落地方式是内联承载一次性交互，画布承载有生命周期的对象。每个生成式组件必须声明可见范围（对模型可见或不可见）、可执行动作与体积上限；组件不得自带设计系统，必须消费宿主令牌；未声明作用域契约的生成式 UI 一律不渲染——按 Macaron 的措辞，生成代码按可信代码在宿主页运行，它不是隔离沙箱，隔离责任在宿主侧【37】【38】。

### 8.5 运行中任务的统一检视面

依据是四组并存的证据：

- Codex CLI 的 `codex agents` 面板提供搜索、启动、打开、重命名、停止五类操作【6】
- Claude Code 的后台任务三级检视【7】
- OpenClaw 的仪表盘跨 `/new` 与 `/reset` 保留【29】
- Codex 桌面端把并行线程收进侧栏并用 Worktree 隔离物理冲突【20】

落地方式是所有形态共享同一个「运行中任务」列表，能搜索、切换、停止、回看；任务状态与布局按会话持久化，切走再切回不得丢失；并行任务的产物聚合到一个审阅面，而不是让用户在多个 diff 之间来回比对。

## 九、证据边界与来源冲突

第一类冲突来自版本演进。Gemini CLI 的快捷键在两代文档之间发生了实质变化，`Ctrl+O` 从调试面板变为聚焦 shell 输入，`Ctrl+B` 从光标左移变为后台 shell 列表，且新表中 `background.toggle` 与 `background.toggleList` 共用同一按键【9】【10】。Gemini CLI 的命令面也经历过扩展，`/shells`、`/rewind`、`/restore`、`/stats`、`/compress` 等命令的职责边界与旧版不同【11】。ZCode 的 Gen UI 与 UI Plugin 版本号，官方发布说明为 v3.15.1（2026-10-12）【35】，与二手来源所称的 v3.16.1 不一致，本报告采信官方。

第二类冲突来自同名与版本代际。

- Hermes 指向三个不同项目【32】【33】【34】
- OpenCode 至少有「已归档并更名为 Crush 的 Go 版」与「当前维护的 TypeScript 版」两个代际【12】【13】
- Pi 的 `pi-one-ui` 与 `@mightguy/pi-ui` 是不同包，后者才提供 droid、gemini、cli-dock、nvim 四套风格预设【15】【16】
- MiMo Code 的 agent 命名为 `build` 与 `plan`【14】，二手来源常写作 Plan、Agent、YOLO，与仓库实际不符

第三类冲突来自形态定位。MiMo Code 的仓库自述为 terminal-native，其远程形态是 `mimo serve` 加 `mimo attach` 的端口转发配对，端口为 4096【14】；此前流传的「Web 三栏、localhost:4280」与「星空背景」在官方材料中找不到对应，唯一能对应的是名为 Vivid visuals 的装饰动画配置，官方给出的关闭理由是动画造成卡顿【14】。

第四类需要降级处理的是数字。Codex 桌面端周活、Claude Code Routines 的日限额、Macaron 的「少用约 45% 输出 token」与首渲染加速比、OpenClaw 的 widget 能力分类等，前两类有官方或二手出处但口径不统一，后一类属项目方自述的内部评测，未经独立复现，且 Macaron 仓库自身在 npm 上周下载量为 0、维护者 1 人【24】【37】。这些数字可以用于判断方向，不适合写进对外材料。

第五类是证据性质本身。Codex 桌面端的两份设计材料是逆向解码与社区换肤产物，不是官方规范【26】【27】；OpenClaw 仪表盘文档页在本次抓取中返回失败，相关描述来自搜索结果摘要与 GitHub PR 正文【28】【29】；omp《Harness Playbook》的原文由社区解读文章转述，本报告引用的界面章内容（单遍 RichText 流原语、类型化组件模型、转录协议与 TLA+ 形式化验证、渲染耗时从 267 秒降到 90 毫秒）均出自该解读文，未直接核对原文【1】。

最后是本次核验对既有草稿的三处更正。Codex CLI 的代理切换键是 `Alt+←` 与 `Alt+→`，不是 Ctrl+Shift 组合【4】。Gemini CLI 的 `Ctrl+B` 切换的是后台 shell 列表，不是嵌入式 PTY 面板【10】。Pi 的四套编辑器风格预设属于社区扩展，而非 pi-one-ui 的内置能力【15】【16】。

## 参考资料

【1】《The Harness Playbook — Stencil》深度解读，一起港湾，https://yxlz.com/thread-8200-1-8.html

【2】DeepSeek Harness 官方仓库（README），GitHub，https://github.com/deepseek-ai/deepseek-harness

【3】openai/codex Issue #24581：TUI subagent history should render nickname/role instead of raw thread ids，https://github.com/openai/codex/issues/24581

【4】codex-rs/tui/src/multi_agents.rs 源码镜像，Gitee，https://gitee.com/kouzi0521/codex/blob/main/codex-rs/tui/src/multi_agents.rs

【5】Codex Multi-Agent System Architecture（事件流与渲染规则解析），GitHub Gist，https://gist.github.com/serialx/f842f7b41d0f74ff5f64845e4afbc260

【6】The Codex Agents Dashboard: Interactive Task Management Arrives in v0.149.0，Codex Knowledge Base，https://codex.danielvaughan.com/2026/08/21/codex-agents-dashboard-interactive-task-management-v0149-subagent-monitoring-tui

【7】Claude Code v2.1.290 变更日志：Background tasks dialog、detail screen and compact list，https://changelogs.core-directive.com/v/2.1.290/e/background-tasks-dialog-detail-screen-and-compact-list

【8】Claude Code 文档：Bagikan output sesi sebagai artifacts（官方文档印尼尼西亚语版），https://code.claude.com/docs/id/artifacts

【9】Gemini CLI 键盘快捷键文档（较早一代，仓库镜像），https://github.com/bakieler/qwen-code/blob/main/docs/keyboard-shortcuts.md

【10】Gemini CLI 快捷键参考（新版全表，含 app 与 background 命令名），https://gemini.openml.io/reference/keyboard-shortcuts

【11】Gemini CLI 命令总览，https://geminicli.com/docs/cli/cli-reference

【12】OpenCode 官方仓库（README），https://github.com/anomalyco/opencode

【13】opencode-ai/opencode 仓库（已归档声明，项目更名为 Crush），https://github.com/opencode-ai/opencode

【14】MiMoCode 官方仓库（README），https://github.com/XiaomiMiMo/MiMo-Code

【15】pi-one-ui（npm 包说明），https://www.npmjs.com/package/pi-one-ui

【16】@mightguy/pi-ui（npm 包说明），https://www.npmjs.com/package/@mightguy/pi-ui

【17】oh-my-pi（omp）官方仓库（README 与包结构），https://github.com/can1357/oh-my-pi

【18】Codex：全能型助手（官方产品页），OpenAI，https://openai.com/zh-Hans-CN/index/codex-for-almost-everything

【19】迁移到新的 ChatGPT 桌面应用，OpenAI 帮助中心，https://help.openai.com/zh-hans-cn/articles/20001276

【20】Codex 桌面界面分析，三个皮匠报告（转引 W3Cschool 与腾讯云开发者社区），https://www.sgpjbg.com.cn/searchtag/27341926.html

【21】Redesigning Claude Code on desktop for parallel agents，Claude 官方博客，https://claude.com/blog/claude-code-desktop-redesign

【22】Anthropic Redesigns Claude App on Desktop for Parallel Agents，Thurrott，https://www.thurrott.com/?p=334911

【23】Claude Code Desktop Redesign: Multiple Sessions, New Sidebar, and a Drag-and-Drop Layout，AIToolsRecap，https://aitoolsrecap.com/Blog/claude-code-desktop-redesign-multiple-sessions-sidebar

【24】Claude Code Desktop Redesign Makes Parallel Agents the Default，AgentMarketCap，https://agentmarketcap.ai/blog/2026/04/16/claude-code-desktop-redesign-multi-session-sidebar-agent-ide

【25】What are artifacts and how do I use them?，Claude 帮助中心，https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them

【26】Codex 桌面 UI 规格（打包产物逆向解码所得），Wegent 仓库，https://github.com/qt-pay/Wegent/blob/main/wework/DESIGN.md

【27】Codex 桌面皮肤说明，oh-my-dsh 仓库，https://github.com/gulagala001/oh-my-dsh/blob/e47a094bbe34a3f0fb292b49546784d6f5a87b0c/docs/codex-desktop.md

【28】OpenClaw PR #113712：feat: add flexible multi-sidebar chat layout，https://github.com/openclaw/openclaw/pull/113712

【29】OpenClaw 仪表盘文档（本次抓取失败，内容取自搜索结果摘要），https://claw.shayna.cc/zh-CN/web/dashboards

【30】DeepSeek Harness 快速开始（Web UI 布局与目录结构），https://deepseekdocs.com/en/docs/getting-started/quickstart

【31】ClawPanel 官方仓库（README），https://github.com/qingchencloud/clawpanel

【32】hermes-ui（单文件玻璃拟态 Web 界面），GitHub，https://github.com/pyrate-llama/hermes-ui

【33】hermes-webui（深色三栏界面），GitHub，https://github.com/nesquena/hermes-webui

【34】hermes-agent（Python 内核），GitHub，https://github.com/NousResearch/hermes-agent

【35】ZCode 官方仓库（README 与发布说明），https://github.com/zai-org/ZCode

【36】ZCode UI Plugin 文档，https://github.com/ken00H/ZCode/blob/main/UI_PLUGIN.en.md

【37】Macaron Artifacts 官方仓库（README），https://github.com/MindLab-Research/macaron-artifacts

【38】GenUI：UI4A 运行时架构文档，https://artifacts.macaron.im/docs/architecture/genui
