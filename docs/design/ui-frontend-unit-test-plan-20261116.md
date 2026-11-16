# 前端单元测试计划（R-014）

> 2026-11-16 · 分支 `ui-workbench` · 状态：**计划稿（待确认后实施）**
> · 实测来源：`npm run test:coverage`（v8 provider，reportOnFailure 显式开启）+
> coverage-summary.json 逐文件解析（tmp/probe/fe_unit_inventory.py / cov_parse.py / cov_zero.py）

## §0 结论

1. 现状总覆盖：**语句 73.4% / 分支 78.8% / 函数 55.4% / 行 73.4%**（106 测试文件 /
   915 测试，38s）。逻辑层骨架健康：utils 几乎全绿（9/11 文件 100%）、stores 12/18
   全绿；**缺口集中在 4 处**，不是全面荒漠。
2. 四个缺口（按严重度）：
   ① `hooks/studyHandlers.ts` 57.4% 语句 / **35.1% 函数**（549 行、36 exports，最大缺口）；
   ② `api/client.ts` **函数 11.3%**——根因是 30 个测试文件 `vi.mock` 掉了它，
   真 client 从未执行；③ `hooks/handlers.ts` 函数 **0%**（分发注册表从未被直调）；
   ④ 快捷键/重连类 hooks 的函数档（useKeyboardShortcuts 21.4% / useSSE 56.2%）。
3. 目标：逻辑层（stores+hooks+utils+api）**函数 ≥85%、语句 ≥90%、分支 ≥85%**；
   组件函数 58.6% → ≥75%。分两批（T1 逻辑层 / T2 组件缝隙），每批一 commit。
4. 附带发现两个（都不混入测试批）：① definitionWorkflowPage 有 2 个 flaky 测试
   （coverage 开销下 waitFor 超时，裸跑通过）；② vitest `coverage.reportOnFailure`
   默认 false——失败时不出覆盖率报告，建议 config 显式开启。

## §1 现状实测

### 1.1 基建

- vitest.config.ts：jsdom + forks pool（注释注明 threads pool 有 jsdom 挂起问题）
  + coverage 已配（v8 / include src/** / exclude tests）；`npm run test:coverage`。
- **坑**：`reportOnFailure` 默认 false，测试失败时覆盖率报告不生成（首轮跑实测
  踩中：coverage/ 只剩 .tmp 残片）。
- 106 测试文件集中在 src/test/（105）+ src/utils（1）；全部 import 映射见 §1.3。

### 1.2 逻辑层逐文件（语句/分支/函数 %，按语句升序，仅列 <100%）

| 文件 | stmts | branch | funcs | 行 |
|---|---|---|---|---|
| hooks/studyHandlers.ts (549行/36exp) | 57.4 | 72.2 | **35.1** | 57.4 |
| api/client.ts (1400行) | 75.2 | 93.5 | **11.3** | 75.2 |
| hooks/messageHandlers.ts | 74.9 | 84.9 | **45.5** | 74.9 |
| hooks/controlHandlers.ts | 79.7 | 83.3 | 85.7 | 79.7 |
| hooks/handlers.ts (注册表) | 82.9 | 100 | **0.0** | 82.9 |
| hooks/useSSE.ts (262行) | 87.4 | 90.0 | **56.2** | 87.4 |
| stores/auth.ts | 70.8 | 100 | 60.0 | 70.8 |
| stores/model.ts | 72.2 | **37.5** | 33.3 | 72.2 |
| stores/personas.ts | 79.2 | 70.0 | 75.0 | 79.2 |
| stores/mode.ts | 80.7 | **55.5** | 50.0 | 80.7 |
| stores/thinkingPref.ts | 81.6 | **45.5** | 50.0 | 81.6 |
| stores/theme.ts | 83.8 | **60.0** | 50.0 | 83.8 |
| stores/study.ts (174行) | 85.0 | 92.8 | **46.7** | 85.0 |
| stores/session.ts (443行) | 86.2 | **67.6** | 88.9 | 86.2 |
| stores/layout.ts | 89.4 | 65.2 | 86.7 | 89.4 |
| stores/chat.ts (561行) | 91.4 | 81.6 | 81.5 | 91.4 |
| hooks/useKeyboardShortcuts.ts | 100 | 93.1 | **21.4** | 100 |
| hooks/metaHandlers.ts | 93.7 | 88.5 | 100 | 93.7 |
| utils/time.ts | 95.8 | 88.9 | 100 | 95.8 |
| utils/equityCurve.ts | 97.1 | 84.0 | 100 | 97.1 |
| api/agentSchemas.ts | 93.8 | 90.9 | 100 | 93.8 |
| 其余 stores×6 + hooks×12 + utils×9 | 100 | 68-100 | 100 | 100 |

### 1.3 组件/页面聚合

| 组 | n | stmts | funcs | 备注 |
|---|---|---|---|---|
| components | 108 | 72.7 | 58.6 | 4 个 0% 文件：AgentList / ErrorBoundary / EquityCurveCard / study/dashboard/types |
| pages | 6 | 29.3 | 9.4 | AdminUsers 2.7% / DAGPage 6.9% / FactorLibrary 6.3% / StrategyLibrary 9.5% / Monitor 53% / Study 97.4%——页面由 catalog/visual 基线承担视觉回归 |
| catalog | 3 | 30.5 | 0 | catalog 页自身无需测 |
| 其余（contexts/data） | 2 | 100 | 100 | — |

### 1.4 已知 flaky（本批不改，记录在案）

`definitionWorkflowPage.test.tsx` 2 测试：coverage 开销下 `waitFor('保存')` 超时，
裸跑（无 --coverage）EXIT_OK 通过。修法候选：该测试 waitFor timeout 上调或拆慢
渲染——独立小修，不混入 T1。

## §2 缺口分析与测试策略

| # | 目标 | 根因 | 策略 | 验收 |
|---|---|---|---|---|
| 1 | studyHandlers 36 exports | 现有 studySseHandlers.test 只覆盖部分 handler | 逐 handler 契约测试：事件载荷 → store 变迁断言（复用现有 study store 测试夹具） | funcs 35→90%+ |
| 2 | api/client.ts 函数 11% | **30 个测试文件 vi.mock 掉了真 client**，真实代码从未执行 | 新建 `clientDirect.test.ts`（不 mock）：fetch stub 层测请求构造（URL/方法/头/auth 注入）、错误降级、超时、各资源组抽样 | funcs 11→60%+（schema 型函数抽样覆盖） |
| 3 | handlers.ts 函数 0% | 分发注册表从未直调 | 注册表契约测试：每事件类型 → 对应 handler 被调（vi.fn 注入断言） | funcs 0→100% |
| 4 | 四个 store 分支档 | 分支未走：localStorage 持久化 roundtrip、toggle 边界、非法值防御 | 各补 3-6 测试：持久化 roundtrip / 非法输入防御 / 初始值边界 | branch 37-60→85%+ |
| 5 | useKeyboardShortcuts | handler 函数仅经真实按键触发才计入 | keydown 派发逐快捷键断言 | funcs 21→90%+ |
| 6 | useSSE 重连/退避 | 现有 887 行测试未走异常路径 | 断线重连 / 退避序列 / 心跳超时分支 | funcs 56→85%+ |
| 7 | messageHandlers 剩余 | 分块/排序/错误路径分支未走 | 补边界：乱序 delta、空 text_id、超大分块 | funcs 45→85%+ |
| 8 | stores/study.ts | round/event reducer 分支 | 补 round 状态机分支 | funcs 47→85%+ |

## §3 计划批次

### T1 逻辑层补洞（8 项，~9 个测试文件扩展/新增，一天级）

| 顺序 | 项 | 新文件/扩展 |
|---|---|---|
| T1.1 | §2#3 handlers 注册表 | 新 `handlers.test.ts`（小，先行验证策略） |
| T1.2 | §2#2 client 直测 | 新 `clientDirect.test.ts` |
| T1.3 | §2#1 studyHandlers | 扩展 `studySseHandlers.test.ts`（或拆 `studyHandlersContract.test.ts`） |
| T1.4 | §2#4 store 分支 ×4 | 扩展 mode/thinkingPref/model/theme 各自 store 测试 |
| T1.5 | §2#7 messageHandlers | 扩展现有 test |
| T1.6 | §2#8 study store | 扩展 studyStoreRecentEvents.test 或新文件 |
| T1.7 | §2#5 useKeyboardShortcuts | 扩展现有 test |
| T1.8 | §2#6 useSSE 异常路径 | 扩展现有 887 行 test |
| 随批 | config：`reportOnFailure: true` + reporters 加 `json-summary` | vitest.config.ts 小改 |

### T2 组件缝隙（半天级，低优先级）

| 项 | 说明 |
|---|---|
| ErrorBoundary（0%） | 子组件 throw → fallback 渲染断言（React error boundary 测试模式） |
| AgentList（0%） | 渲染 + 空态 |
| EquityCurveCard（0%） | 渲染断言（utils 层 equityCurve 已 97%，只补组件壳） |
| study/dashboard/types.ts（0%） | 先核实是否纯类型文件——纯类型则加入 coverage exclude（虚警） |
| pages 内嵌逻辑 | 仅在有逻辑处补；纯渲染页交由 catalog/visual 基线（现 32% 组件覆盖 + 页面基线） |

### §3.1 逐项用例规格（实施级；T1.1 已细化，T1.2+ 实施时按同格式回填）

**T1.1 `handlers.test.ts`（分发注册表契约，6 用例）**

| 用例 | 断言 |
|---|---|
| 完备性（双向） | 运行时 `EVENT_TYPES`（83 项）与 `HANDLERS`（83 键）互为子集——零异常（实测：完全对齐）；新增事件漏注册 / 悬空注册即红 |
| TS union 备注 | `SSEEventType` union 中 4 个类型（agent_text_started/ended、agent_llm_usage/response）不在运行时数组——handlers.ts 过时头注释随 T1.1 修正 |
| 接线正确性 | EXPECTED map（76 个命名 handler，与 handlers.ts 同源 import）逐键 `toBe` 同引用；键数相等（无多余注册） |
| 值健康 | 所有 HANDLERS 值 `typeof 'function'`（防坏 import 产出 undefined） |
| 内联 handler ×6 行为 | `agent_thinking_start/done`、`agent_tool_call/result`、`agent_assistant_message`、`agent_loop_end` → 真实 study store `addLiveEvent` → `recentEvents[0]` 的 message 前缀/round/raw.type 断言 + 缺省 fallback |
| 内联 noop | `agent_text_delta` 调用后 `recentEvents` 长度不变（高频降噪契约） |

**T1.2 `clientDirect.test.ts`（client 直测，不 mock client）**

| 用例组 | 断言 |
|---|---|
| 请求构造 | URL 拼接 / 方法 / Content-Type / auth 头注入（token 存在时） |
| 错误降级 | 4xx/5xx → 抛出形态 / error 字段；网络异常 → 统一错误路径 |
| 资源组抽样 | chat / study / workflow / goal / system 五组各抽 2-3 个代表端点 |
| 超时/abort | AbortSignal 传递 |

**T1.3 studyHandlers 契约（36 exports 逐个）**：事件载荷 → store 变迁断言，逐 handler 一表（实施时展开）；优先补 0% 的 24 个。

**T1.4 store 分支 ×4**（model/mode/thinkingPref/theme）：localStorage roundtrip / toggle 边界 / 非法值防御 / 初始值，各 3-6 用例。

**T1.5 messageHandlers**：乱序 delta、空 text_id、超大分块、part 边界。

**T1.6 study store reducer**：round 状态机分支（funcs 47→85%）。

**T1.7 useKeyboardShortcuts**：逐快捷键 keydown 派发断言。

**T1.8 useSSE 异常路径**：断线重连 / 退避序列 / 心跳超时。

## §4 工序与验证（R-020）


- 一批一 commit：T1 可再按 T1.1-T1.8 分小 commit（每项独立可回退）；
  每 commit 前 `npm run test:coverage` 全绿 + 覆盖数字对比表写进 commit message。
- config 改动随 T1.1 首个 commit。
- flaky 修复（definitionWorkflowPage）单独小 commit，不混批。
- 总验收：逻辑层 funcs ≥85% / stmts ≥90% / branch ≥85%；组件 funcs ≥75%；
  总 stmts 73.4% → ≥85%。

## §5 不做的事

- `api/types.gen.ts`（openapi 生成物，6749 行）——coverage exclude 考虑（生成物）。
- 组件全量补齐——视觉回归由 50 基线（A-0 后 84）+ catalog 承担，单元测试只补
  逻辑组件。
- 后端 pytest 覆盖率——另一轨，不在本计划。
- thresholds 硬门——暂不设（避免绑架组件测试）；T1 完成后再议是否对逻辑层
  设独立 glob 阈值。

## §6 决策记录

> 2026-11-16 用户指示「先写文档，再开始」——按本文推荐默认定案，逐条留痕：

1. **目标阈值**：按推荐（逻辑层 funcs ≥85% / stmts ≥90% / branch ≥85%；总 stmts ≥85%）。
2. **节奏**：T1 八项连续做，每项一 commit；T1.1 先行。
3. **flaky（definitionWorkflowPage）**：本轨处理，独立 commit，放 T1 末尾。
4. **types.gen.ts**：加入 coverage exclude（生成物）。
5. 覆盖率报告的 `reportOnFailure: true` + `json-summary` reporter 随 T1.1 config 小改落地。
