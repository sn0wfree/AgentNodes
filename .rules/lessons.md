# AgentNodes L2 教训台账

> **层级**：L2（本项目教训）。上位：`../AGENTS.md`（L0）+ `rules.md`（L1）。
> **只写事故/踩坑案例**：背景 → 根因 → 防法。重复出现 2 次以上 → 升 L1。
>
> **升 L1 去向**（每条已标注）：
>
> | 教训 | 升入 |
> |---|---|
> | `client.chat` 单入口 | R-008（§3.1）|
> | 只比 `LoopResult` | R-009（§3.3）|
> | `budget_max_seconds=0.0` | R-009（§3.3）|
> | 删对照物前去掉对偶 | R-009（§3.3）|
> | grep 找不到变量误伤 | R-011（§3.6）|
> | 审计命令是安全网 | R-004（§1.3）|
> | 双引擎对偶的弱点 | R-009（§3.3）|
> | 并行下的隔离 vs 真实缺陷 | R-017（§3.7）|
> | 安全测试要测真实调用形态 | R-018（§3.8）|
> | worker 崩溃/挂死 = 内存态，先降档再判代码 | R-007 §2.1「`-n` 选取：按空闲内存定」|

---

## 2026-11-11 · V4-2：只 mock `client.chat` → 打到真实网络

**已升 L1**：R-008（`rules.md` §3.1）。

**背景**：`AgentLoop.run()` 改成走 `arun()` 之后，14 个测试文件的 LLM 替身
全部失效。症状不是"红"，而是**"答案变空"或整条用例挂住**——有一次全量
跑到 1% 就慢到不可用，另一次单文件跑了 266 秒。

**根因**：

```python
loop.client.chat = mock.chat      # ← 只挂了同步入口
# 引擎实际调的是 loop.client.achat(...)  → 真客户端 → 真 HTTP + 重试退避
```

`chat` 与 `achat` 是**两个入口**，"挂载测试替身"这件事**没有门禁**。
只挂一个，另一个静默走真网络。

**防法**：

- 新增 `tests/llm_client_mock.py::patch_llm_client()`，两个入口挂同一个替身；
  （该文件随 V4 回退已移除，见 §「2026-11-11 · V4 系列整体回退」；模式仍有效）
- 异步替身必须**真异步**（`await asyncio.sleep`）——用 `time.sleep` 会阻塞
  事件循环，`asyncio.wait_for` 的取消信号送不进去，超时用例测不到东西。

---

## 2026-11-11 · V4-1：只比 `LoopResult` 字段 → 漏掉三条引擎差异

**已升 L1**：R-009（`rules.md` §3.3）。

**背景**：V4-1 建 sync 路径 parity 网时，9 个场景 × 5 列逐字段比对
`LoopResult`（`finished_reason` / `iterations` / `answer` / `success` /
`usage_tokens`）——**全绿**。换成比对**整条事件序列**后，立刻抓出 3 条：

1. legacy 在异常收尾（预算停止 / LLM 抛错）**不发** `loop_end`/`iter_end`；
2. 答案为空时 legacy 仍发 `assistant_message`（前端多一个空气泡）；
3. `budget_exhausted` 事件**只有 legacy 发**——它在事件词表里
   （`event_vocab.py:28`），默认引擎从不发。

**根因**：`LoopResult` 是**结果摘要**，事件序列是**过程记录**。摘要对齐
不代表过程对齐。第 3 条尤其危险：它意味着「因为预算停止」这件事在默认
路径上**没有任何可观测信号**。

**防法**：引擎/路径对比类测试必须比**事件序列**，不能只比结果字段。
已在 V4-1 落地（`test_sync_path_parity.py` 的 C 组；该文件随 V4 回退已移除，
模式仍有效——写对比测试时记住这条）。

---

## 2026-11-11 · V4-1：`budget_max_seconds=0.0` 其实**没**触发预算

**已升 L1**：R-009（`rules.md` §3.3「禁止空集合通过」）。

**背景**：`test_skeleton_equivalence.py` 有个场景叫 `budget_exhausted`，
参数是 `{"budget_max_seconds": 0.0}`。它一直绿——因为**两个引擎都不触发
预算**，比出来的结果当然一样。

**根因**：`BudgetLimits._active()` 把 `0` 视为**禁用**（不是"立即超支"）。
场景名与实情不符，且双引擎对偶形式掩盖了它（两边都是"没触发"，
`A == B` 成立）。

**防法**：

- 换成真正会触发的维度：响应带 `usage`（否则 `tokens_used` 恒 0）+
  `budget_max_tokens=100` vs 每次 150 token；
- 断言改成**绝对值**（`finished_reason == "budget"` / `iterations == 1` /
  `usage_tokens > 0`）。

---

## 2026-11-11 · V4-4a：删对照物之前必须先去掉对偶依赖

**已升 L1**：R-009（`rules.md` §3.3「反对偶」）。

**背景**：V4-4 要删 legacy 引擎。但 `test_engine_parity.py`（7 组）与
`test_skeleton_equivalence.py`（7 条）全是 `legacy == skeleton` 形式。
legacy 删了之后它们变成**自己跟自己比**——`assert X == X` 恒真。

**根因**：「两个引擎一致」这个命题在其中一个被删后**自动成立**，测试从
"验证"退化成"装饰"。这与批 E 的「审计命令不能随兼容层一起死」是同一
类问题：**安全网不能建在被删的那一侧**。

**防法**：

- 删对照物**之前**先单独一批把对偶断言转成**单侧绝对值断言**
  （V4-4a 的 `test_engine_capabilities.py` / `test_skeleton_capabilities.py`；
  这两个文件随 V4 回退已移除，模式仍有效）；
- 转出来的断言比原来更强：原来是"两边一样"，任何一边退化都被另一边掩盖；
  转成绝对值后，单独退化也会红。

---

## 2026-11-11 · E-2 扫尾：grep 找不到"变量被替换"这类误伤

**已升 L1**：R-011（`rules.md` §3.6）。

**背景**：批 E-2 的机械改名脚本扫 `tests/conftest_e2e.py` 时，把局部变量
`STATIC_DIR` 也替换成了 `AGENTNODES_STATIC_DIR`（3 处 f-string）。

**为什么测试没抓到**：那 3 行只在**前端构建产物缺失/过期**时执行——
本机产物齐全，是死代码路径。全量测试全绿。

**发现方式**：对 98 个被改的 .py 文件做**前后上下文逐行比对**，不靠 grep。

**根因**：grep 匹配的是**文本**，机械替换改的是**语义位置**。当一个字符串
既是环境变量名、又是局部变量名时，文本层面无法区分。

**防法**：

- 批量替换后必须做**确定性上下文比对**（改前 vs 改后逐文件 diff 的
  非预期变更）；
- 不要用"测试全绿"当作"没误伤"的证据——死代码路径的误伤测不出来。

---

## 2026-11-09 · E-1：审计命令是删兼容层的安全网

**已升 L1**：R-004（`rules.md` §1.3）。

**背景**：批 E 要删旧环境变量名兼容层。删除之后，用户环境里若还留着旧名，
行为会**静默回落默认**（不报错，只是配置不生效）。

**处置**：先做 `agentnodes config audit-legacy`（E-1，**只读、零行为变更**），
把旧名从"可执行的输入"降级为"可诊断的事实"——读取层不认它们，诊断层认。

**根因**：「删掉 X」和「删掉能发现 X 的机制」是两件事。后者一旦跟着前者
一起删，出问题就没有观测手段了。

**防法**：删任何兼容层/回退路径之前，先建**只读的审计/诊断能力**，并确认
它**不依赖被删的那一侧**。

---

## 2026-11-09 · 双引擎对偶测试的天然弱点

**已升 L1**：R-009（`rules.md` §3.3）。

**背景**：`assert legacy[key] == skeleton[key]` 这种形式，只要两边**同时**
错成一样的值就过。批次 13 实测：skeleton 丢了 6 项在产能力，全量测试仍
全绿——因为那 6 项在 legacy 侧也没有对应断言（legacy 是"参照物"，没人
验证参照物本身对不对）。

**防法**：

- 参照物一侧的断言必须来自**实测的绝对值**，不是"另一个实现"；
- 差异清单要双向钉（清单外出现差异 → 红；清单内差异消失 → 也红）。

---

## 2026-11-11 · V4 系列整体回退：越改越乱的根因

**已升 L1**：R-014（`rules.md` §4）+ R-016。

**背景**：V4-0 到 V4-4b 六批「删 legacy 引擎」的工作，加上后续一次
「统一 loop 规范」的尝试（ADR + LoopProtocol + 契约测试 + 门禁），
最终**整体回退到 V4 之前**。

**根因（三条，都不是技术问题）**：

1. **范围蔓延**：用户要的是「合并 4 处 loop 结构」，我扩成了
   「重新设计构造入口 + RunRequest + 五步迁移」——其中 2 步是我发明的需求，
   没有任何证据表明它们有收益。
2. **测试照模板写，不照实现写**：写 LoopProtocol 时套了通用的
   `init/step/finalize/cleanup` 六阶段模板，然后在测试里
   `phases.append(CLEANUP)` **伪造**了实现没有的阶段。结果测试永远绿，
   而它本该抓到的缺陷（EXIT 路径不收尾）被同一个伪造掩盖。
3. **验证标准错**：「全量 7166 条绿 + batch_gate ALL GREEN」被当成
   「改动安全」的证明。但那 7166 条只比结果字段，**不比出口收尾**——
   删掉一个有 6 个出口全部收尾的引擎，换成一个有 2 个 EXIT 出口不收尾的，
   测试照样全绿。

**代价**：81 个 commit 的分支历史（已存档为 `archive/loop-unify-20261111`）。

**防法**：

- 动手前先给方案，方案里必须写清「验收标准是什么断言」；
- 测试从**实现**导出，不从模板导出——如果测出来的东西实现里没有，
  要么改实现，要么改测试，**不能伪造**；
- 删执行路径前先穷举旧路径的**所有出口**（R-016）；
- 「全量绿」不等于「没破坏」，要说清「这些测试覆盖了什么、没覆盖什么」。

---

## 2026-11-11 · 并行全量：串行 10m51s → `-n 12` 1m18s

**已升 L1**：R-017（`rules.md` §3.7）+ §2.1 默认命令。

**背景**：`acb5ae96`（2026-11-09）已经做过一轮并行化（8m39s → 2m07s，
修了 4 个隔离缺陷，`conftest.py` 的 halt 文件按 worker 隔离），但那次的
结论是「本地手动加 `-n 8` 即可，**不加到 addopts**」。结果 `.rules` 的
验证命令一直是**串行**的，每次全量要跑 10 分 51 秒。

**实测（2026-11-11，本机 12 逻辑核）**：

| 方式 | 耗时 | 结果 |
|---|---|---|
| 串行 | 651.5s（10m51s） | 7154 passed / 114 skipped |
| `-n 8` loadgroup | 96.9s（1m37s） | 完全一致 |
| `-n 12` loadgroup | 77.8s（1m18s） | 完全一致 |

**8.4×**，且 passed/skipped 与串行**逐项一致**。

**踩到的坑**：`-n 12` 下
`test_executor_bridge.py::test_cancel_with_no_in_flight_keeps_pool_alive`
**稳定失败**（连跑 3 次全中），`-n 8` 不失败、单跑通过。

**根因**（不是测试写错，是实现的真实竞态）：

```
主进程                             worker 进程
cancel():   _cancel_evt.set()     # ① 设标志，无在飞任务 → 直接 return
dispatch(): _cancel_evt.clear()   # ② 清标志
            _dispatch_q.put(spec) # ③ 入队
                                  # ④ 取到 spec
                                  # ⑤ if cancel_evt.is_set():   ← 仍看到 True
                                  #      put(error="cancelled before start")
```

跨进程 `Event` 的 `clear()` 从主进程发出到 worker 可见**有延迟**。CPU 满载时
这个延迟会超过 `dispatch → worker 取任务` 的时间窗，worker 误以为还在取消态。

**处置**：给该文件打 `xdist_group("executor_bridge")`（同组固定同一 worker），
注释里写清实测条件与失败症状。**没有蒙混**——`ExecutorBridge` 的竞态本身仍然
存在，只是不再被并行放大；它是 `-n 12` 才暴露的**实现问题**，不是测试问题。

**防法**：

- 并行下失败的测试，先判「隔离问题」还是「真实缺陷」——单跑 / 单文件
  `-n N --dist loadgroup` / 全量并行，三步能分开；
- 用 `xdist_group` 时必须写注释说明**实测条件**（几 worker、什么症状），
  否则半年后没人知道这个 marker 是不是该删；
- 不要用「降 worker 数」代替定位——那会把真实竞态藏起来。

---

---

## 2026-11-11 · 权限系统 64 条测试全绿，但有 3 个可达绕过

**已升 L1**：R-018（`rules.md` §3.8）。

**背景**：评估「权限系统 / 架构门禁 / 骨架-decider」三者价值时，用**真实调用链**
（带 workspace 的 `ToolContext` + registry 里的真实工具名）实测，发现
`kernel/permission/` 的 1086 行实现有 3 个绕过。文档：
`docs/design/asset-value-review-20261111.md` §1。

**三个绕过（都实测复现）**：

1. **`read .env` 读出密钥**（P0）
   `hard_deny` 规则是 `**/.env`，但 `_glob_match("**/.env", ".env") == False`
   ——`**/` 要求目录前缀。实测：

   ```
   path='.env'        -> allow  ← 执行了，泄漏=True
   path='./.env'      -> deny
   path='sub/.env'    -> deny
   path='/abs/.env'   -> deny
   ```

   同样的洞在 `**/.ssh/**` vs `.ssh/id_rsa`。
   **而 `read .env` 是模型最自然的写法——恰好是唯一被放过的形态。**

2. **`webfetch` vs `web_fetch`**
   `Permission.WEB_FETCH.value = "web_fetch"`，registry 里的真实工具名是 `"webfetch"`。
   云 metadata 的 hard_deny 对真实工具名不生效（落到 ASK）。

3. **`run_bg_command` 不在 `Permission` 枚举里**
   registry 18 个工具，12 个不在枚举。`_extract_target("run_bg_command", {...command})`
   返回的是工具名不是命令串 → `sudo` 规则完全失效。

**根因（与本台账 V4 那条同构）**：

64 条测试全绿，但断言用的 target 全是 `/abs/path/.env` 或 `config/.env`，
**没有一条测裸相对路径 `.env`**；也没有一条用 registry 里的真实工具名。
**测试照「假设的调用形态」写，不照「真实的调用形态」写。**

**防法**：

- 安全测试的输入必须来自真实调用链（工具名取 registry，路径取模型真会写的形态）；
- 断言前先确认规则**真的能匹配上**这个输入（打印匹配结果，别假设）；
- 覆盖「枚举名 vs 实际名」的差异。

**当前状态：已修复（2026-11-11 当日）。** 流程按 R-010：先落
`tests/test_permission_real_shapes.py`（7 条 strict xfail 钉红）→ 修实现
→ XPASS 强制摘 marker → 13 passed 全绿，全量 7167 passed / 0 failed。
修法与一处评估错误（`.ssh` 其实当年已手补裸规则 `schema.py:112`，
逐条手补补不完——通用修法在 `_glob_match`）的更正见
`docs/design/asset-value-review-20261111.md` §7。
相邻未修（记录）：shell 整串 target 对无斜杠裸命令（`cat .env`）仍不拦；
`run_bg_command` 在 L2 预设仍 ASK（shell 是 ALLOW）。

---

## 2026-11-11 · 门禁看「0 违规」不够，要看「注入违规会不会红」

**已升 L1**：R-019（`rules.md` §3.9）。

**背景**：同一轮评估。方法从「跑一遍看有没有报错」改成「**注入一条违规** →
跑门禁 → 恢复 → 看 rc」。

**结果（8 道门禁）**：

| 门禁 | 注入 | 结果 |
|---|---|---|
| `check_env_access` | kernel 层 `os.environ.get("AGENTNODES_X")` | rc=1 ✅ |
| `check_kernel_nouns` | `"run_backtest_probe"` | rc=1 ✅ |
| `check_dangling_imports --layers` | L2 反向 import L4 | rc=1 ✅ |
| `check_path_literals` | `parent / "core" / "x"` | rc=1 ✅ |
| `check_path_literals` | `"src" / "agentnodes" / "api"` | rc=1 ✅ |
| `check_path_literals` | `".quantnodes"`（精确） | rc=1 ✅ |
| `check_path_literals` | **`".quantnodes/probe"`** | rc=0 ❌ |
| `check_path_literals` | **`".quantnodes/research/x.db"`** | rc=0 ❌ |

**盲区根因**：

```python
PATTERN_BRAND_DIR = re.compile(
    r'["\']\.quantnodes(-research)?["\']'   # 要求字符串恰好是 .quantnodes
    r'|["\']\.quantnodes_[a-z_]*\.db["\']'
)
```

`".quantnodes/probe"` 后面还有 `/probe` → 不匹配。而**拼路径的常见写法正是这种**。
`src/` 里 `.quantnodes/` 共 56 处命中（18 处非注释非 docstring）。

**探针自身的错误（也要记）**：第一版把 env 违规注入 `settings.py`，门禁没报——
我一度以为门禁有盲区，实际是 `settings.py` 在 `EXEMPT_FILES` 里。
**注入前必须确认目标文件不在豁免列表**，否则测的是豁免逻辑不是检测逻辑。

**防法**：

- 门禁评估用注入法，不用「当前 0 违规」；
- 注入点必须避开 `EXEMPT_FILES`；
- 盲区发现后先看正则覆盖的是「精确形态」还是「实际写法」——
  前者几乎总会被后者的变体绕过。

---

## 2026-11-12 · U5 切引擎：LLM 客户端有 4 个入口，只挂 2 个会打到真网络

**已升 L1**：R-008（`rules.md` §3.1，4 入口）+ R-011 加强（§3.6，机械替换测试代码）。

**背景**：U5 把 ``run()``（sync）从 legacy 切到统一引擎（skeleton）。切之前
**一切正常**——切之后 16 个测试文件、175 处 ``run()`` 调用里，**只挂
``client.chat`` 的 mock 全部落到真网络**（``ConnectError: All connection
attempts failed``；单文件耗时从 2s 变 384s）。

**根因**：``AgentLoop`` 的 LLM 客户端有 **4 个入口**，不是 2 个：

```
openai_client.py:321  chat      (sync, 非流式)
openai_client.py:343  achat     (async, 非流式)
openai_client.py:365  stream    (sync, 流式)
openai_client.py:470  astream   (async, 流式)   ← stream_mode=True 默认走这个
```

legacy 的 sync 路径调 ``client.chat`` → mock 命中；统一引擎走 async →
调 ``client.achat``/``client.astream`` → **真客户端**。

**这是 R-008 的第三次修正**（前两次：V4-2 的 chat/achat、U1 的
"4 个入口"记录）。本批把它做成了**机制**而非文字：

- ``patch_llm_client`` 升级为**默认挂满 4 个入口**（流式入口用单个完整
  响应包装成生成器）；
- 新增 ``patch_client_achat``（自定义 client 对象场景——测试自建 client
  再挂到 loop，绕过了 ``patch_llm_client``）；
- ``as_async``/``as_async_gen`` 自动识别**已经是 async 的**可调用/生成器
  （否则把 async 方法当同步包装 → ``coroutine object has no attribute
  'content'``）。

**防法**：

- 任何「挂 mock 到 LLM 客户端」的地方，一律用 ``patch_llm_client``；
  自定义 client 用 ``patch_client_achat``；
- **流式 chunk 必须是 ``StreamChunk`` 对象**（不是 dict）——消费侧按属性
  访问，用 dict 会走 error 路径，症状是「事件序列不对」而非「mock 挂了」。

---

## 2026-11-12 · 机械批量替换测试代码：3 类错，2 类症状是「假绿」

**已升 L1**：R-011 加强（`rules.md` §3.6）。

**背景**：U5 需要把 73 处 ``loop.client.chat = X`` 改成
``patch_llm_client(loop, X)``。用脚本批量替换。

**三类错**：

| # | 错 | 症状 | 为什么难发现 |
|---|---|---|---|
| 1 | 改了 subprocess 模板（``_INNER = r'''...'''``）里的代码 | ``ImportError: DLL load failed`` | 形态像环境问题；子进程 ``sys.path`` 只有 ``src``，没有 ``llm_client_mock`` |
| 2 | 多行表达式被单行正则破坏（``loop.client.chat = MockLLM([...]).chat``）| **SyntaxError → 整个文件不被收集** | 「测试通过」是**假的**——文件根本没跑 |
| 3 | helper 参数对象错位（``patch_client_achat(loop, ...)`` 而非 ``(loop.client, ...)``）| 静默不生效 → 打到真网络 | 无报错，只是慢 100 倍 |

**根因**：本仓测试代码形态高度不统一（多行表达式 / subprocess 模板 /
自定义 client 对象）。脚本按「单行 + 固定前缀」匹配，漏掉了这些形态。

**防法**（R-011 加强）：

1. 逐文件 ``git diff`` 复核非预期变更；
2. ``pytest --collect-only -q`` 确认**收集数没意外下降**（锚点只查下限，
   语法错误表现为 error 而非下降）；
3. 跑该文件**看耗时**——命中真网络时耗时暴涨 10 倍以上。

**元教训**：批量替换**测试代码**的风险高于收益——症状多为「假绿」，
而假绿比假红危险（假红会拦住你）。

---

## 2026-11-13 · workflow 测试 SQLite DB 锁（既有 flake，未修 · 独立账）

**状态：未修，独立批待办。** HEAD（`8e405bb6`，B2-① T6 后）可复现，
与 T7 改动无关——stash 掉 T7 仍失败，因果判定明确。

**现象**（`-n 12 --dist loadgroup` 全量跑）：
`tests/test_workflow_segment_loop.py::TestAutoFlow` 下不同用例轮流红：
`test_max_segments_hard_limit`、`test_evaluator_replan_loops_to_planner`。
失败点：`store.py:183 sqlite3.OperationalError: database is locked`。

**判定过程**（两次都先证伪"本批引入"）：

| 跑法 | 结果 |
|---|---|
| 单跑该用例（串行）| 1 passed |
| 该文件内 `-n 4` / `-n 12` | 18 passed / 2 skipped |
| 全量 `-n 4` | 偶发 1 failed |
| **HEAD（stash 掉 T7 改动）** | **同样 failed** → 与本批无关 |

**根因**：workflow 测试共享 SQLite 文件（组内用例共用同一 DB 路径）。
该文件已有 `xdist_group("workflow")`——marker 只保证**同组同 worker**
（R-017），挡不住**同一 worker 内串行用例之间**的 DB 文件锁竞争残留
（前一用例的连接未关/未释放，或子进程写锁跨用例存活）。

**与"隔离 vs 真实缺陷"的区别**：R-017 判的是「并行下失败、串行通过」
→ 给 marker。这里 **marker 已经存在**，问题在 marker 的粒度上限之内
——需要的是**用例级 DB 隔离**（临时 workspace/DB 路径 per test），不是
再加 marker。

**防法（待办，未执行）**：给 `test_workflow_segment_loop.py` 的用例注入
独立 `tmp_path` DB 路径（`AGENTNODES_WORKSPACE` 或 store 的 db_path），
使组内用例不再共享文件锁。执行前先按 R-014 落方案。

---

## 2026-11-13 · xdist worker 崩溃后 master 挂死 = 内存态（已升 L1）

**已升 L1**：R-007 §2.1「`-n` 选取：按空闲内存定，不写死」。

**背景**：B2-① T9 收尾时，R-007 标准命令 `-n 12 --dist loadgroup` 三连败，
但代码在 T8 已全绿（同日 `-n 4` 两次 7266 passed / 0 failed）。

**三种症状**（都是内存态，不是代码）：

| # | 症状 | 实测证据 |
|---|---|---|
| 1 | `no tests ran`（exit 5）+ `bringing up nodes...` 打两次 | 全量 `-n 12` 复现 ×2；同命令 `-n 12` 跑**单文件**正常（9 passed）→ xdist 本身没问题，是 12 worker 同时 import 重依赖的累积压力 |
| 2 | `[gw6] node down: Not properly terminated` 后 master **10 分钟零输出** | 全量 `-n 8`：跑到 99% 崩 worker，`ut8.txt` 文件大小 25s/180s/240s 三次采样**恒定 15010 字节**——不是慢，是挂死 |
| 3 | 残留 worker 抢文件锁 | 第一次挂死的 master（PID 9676）被工具超时中断后仍在跑，第二次跑报 `另一个程序正在使用此文件`——**先杀干净再跑**，否则报错会被误读成"新故障" |

**环境证据**：32GB 总内存，空闲仅 **7.2–7.5GB**；12 worker 同时 import
numpy/pymupdf/playwright 峰值远超单 worker 内存。

**处置**（进了 L1，防复发）：
1. 先判环境态（三信号见 R-007 §2.1）→ 查 `FreePhysicalMemory` → 降档
   `12→8→4`，每档要出 summary；
2. **挂死的处置**：按 PID 精确终止（`taskkill /PID <master> /T /F` 或
   `Stop-Process`），**禁 `pkill -f`/模糊匹配**；杀完确认 python 进程数
   归零再重跑；
3. **不许**把规则里的 `-n` 永久改小当"修复"——worker 数是环境参数，
   写死小值 = 用慢档位掩盖环境问题（同 R-017 的反例）。

**元教训**：本会话 `-n 12` 早些时候**连续多次成功**，同命令同代码后来
连败——**同日同机的"绿"不能外推**。规则里写死的 worker 数必须配一个
"什么时候它不算数"的判定条款，否则遇到环境态时会去查代码（浪费轮次）。

**附带踩坑（非本主题，但同批记录）**：本机 shell 是 cmd，**`&&` 是
分隔符、`;` 不是**。用 `;` 串命令时 powershell 参数被吞给 pytest →
`unrecognized arguments: -NoProfile -Command`（exit 4）——这是**假故障**，
我因此误判过一次"收集错误"。凡连串命令一律用 `&&`。

---

## 2026-11-13 · 跨平台测试：Windows 绿 ≠ 代码对（3 个坑）

**背景**：修 chat 主路径的 langgraph 隐式依赖（Windows 全绿、WSL 3 failed），
需要在 Linux 上验证。踩了三个坑，都不是代码问题但都会让人误判。

### 坑 1：Windows 的"绿"是环境债

Windows 环境恰好装过 `langgraph`，掩盖了「chat 第一条消息就炸
``ModuleNotFoundError``」——langgraph 在 ``pyproject`` 里是**独立 extra**，
按文档装 ``[api]`` 的用户根本拿不到它。

**判据**：依赖是否**真的**被需要，只有在「没装它的环境」里测才知道。
Windows 装全了 → 缺陷隐形。

### 坑 2：``/mnt/d`` 上跑测试会污染工作树

在 WSL 里直接跑 ``/mnt/d/Github/AgentNodes``（Windows 盘）→ ``git status``
显示 **2482 个文件"改动"、484670 行进出**——全是 **CRLF/LF 行尾差异**
（Windows 工作树是 CRLF，Linux 检出是 LF，无 ``.gitattributes`` 兜底）。

**防法**（MS 文档明确：Linux 命令行工作应把文件放 WSL 文件系统，
``/mnt/*`` 是跨 OS 慢路径）：**clone 到 WSL 原生盘再跑**——
``git clone --no-hardlinks /mnt/d/Github/AgentNodes ~/agentnodes-test``。
实测工作树 0 改动、LF 行尾、import 指向新克隆。

### 坑 3：未跟踪文件在 clone 后必然缺

``templates/`` 整个目录 **git 跟踪文件数 = 0**（Windows 本地有、git 里没有）
→ WSL 克隆缺它 → 12 条测试失败（``FileNotFoundError: templates/prepare.py``）。

**判据**：clone 后的失败要先查「该文件是否在 git 里」，而不是直接改代码。

### 附带：WSL 侧的其它平台差异（已归因，非回归）

- ``python`` 命令不存在（Linux 只有 ``python3``）→ 需建软链
- ``/ws`` 这类硬编码假路径在 Linux 真的不存在（Windows 上解析为当前盘根，存在）
- 家目录结构不同（``/home/ll`` vs ``C:\Users\1``）→ ``test_sandbox_home_is_outside_real_home`` 断言不成立

### 元教训

**A/B 对照是跨平台改动的唯一硬证据**：在**同一环境**下对比「有改动」与
「还原改动」的两组结果——逐条一致即证明非回归。本次靠它排除了
「Windows 129 failed 是我引入的」这个误判（实际是 ``uuid_utils`` 原生 DLL
被 Application Control 策略拦，HEAD 同样失败）。

**同一环境**是关键：跨环境对比会把平台差异与代码差异混在一起。
