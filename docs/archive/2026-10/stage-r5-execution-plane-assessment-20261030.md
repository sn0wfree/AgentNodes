# R5 评估：执行面扩展 —— 勘查后判定「大部分不做」（2026-10-30）

> 计划依据：`roadmap-next-20261030.md` R5（原估 2-3d：cpu/graph 节点、
> RuntimeHub、subagent lane）。**勘查结论：三项中有两项不该做，一项
> 只做最小必要件。** 这与 B3（swarm/workflow 不迁）同型——验收标准明确
> 允许「判定不做」，前提是给出证据。

---

## 1. 逐项勘查

### 1.1 cpu 节点（进程池）——⚠ 动机已被满足，不建

| 事实 | 证据 |
|---|---|
| 回测**已经**支持 `background=True` → 子进程（`subprocess.Popen` + 日志 + 心跳 + 硬杀） | `backtest_tools.py:106-112` → `bg_proc.py:174-260`（`run_bg`/`kill_bg`/`wait_bg`/`is_stalled`） |
| 进程池已存在且**已接线**：fanout-t2 → `ExecutorBridge`（取消连坐 + 5s 硬杀） | `graph_engine.py:415-523`；R1 勘查时已确认 |
| study 的图是**阶段图**（researcher→novelty→claims→execution→evaluation），每阶段是 agent 节点，不是 CPU 密集步骤 | `graph_preset.py:50-58` |

**判定**：cpu 节点要解决的「烧 CPU 的重活不能阻塞事件循环」已经由
(a) 回测后台子进程 和 (b) fanout-t2 进程池 两条路覆盖。再建一个
「cpu 节点类型」是**新增第三条等价路径**——违反 less-is-more。

**重开条件**：出现一个**不是回测**的 CPU 密集步骤需要嵌进图内并行，
且 fanout-t2 + 注册函数不能满足时。

### 1.2 graph 子图节点——⚠ 无用户，不建

**事实**：所有图都是**扁平 add_node**（study 阶段图、chat 单节点退化图、
fanout preset）。没有任何调用方需要「图里嵌套子图」。

**判定**：递归图执行是引擎原语级的复杂度（子图状态隔离、中断传播、
预算切片、hop 计数），当前零用户。**建它就是为假想需求付真代价。**

**重开条件**：出现「一个阶段内部本身是有环/多分支的子流程」的真实用例。

### 1.3 RuntimeHub——✅ 该做（本刀唯一实件）

**事实**：`LLMConfig.load()` 在 **20+ 处**被直接调用
（`role_factory`、`executor`、`singleton`、`chat.py`、`system.py`、
`scenario_router`、`strategy_acceptance`…），每处各自决定 provider/model。
这正是图上 RuntimeHub「解析每个 turn 用什么执行」要收口的散点。

**但**：图上画的 RuntimeHub 还包含「preset 图绑定」——那部分**没有散点**
（preset 由各链自己选定，无需解析）。所以本刀只做**模型侧收口**，
不做「图绑定」那一半（无用户）。

### 1.4 subagent lane——⏸ 无实际并发问题，不做

| 事实 | 证据 |
|---|---|
| subagent 是**同步**执行：`executor.execute(...)` 在工具调用线程内直接跑完返回 | `subagent_tool.py:265` |
| 每 turn 上限 = `MAX_SUBAGENTS = 5`（计数引用） | `subagent_tool.py:25, 194-208` |
| 图上写的「subagent lane 默认并发 8，不碰 mailbox 防自锁」 | 架构图 ③ 执行红线格 |

**判定**：subagent 当前**不并发**（同步串行 + 每 turn 5 个上限），
不存在「8 路并发打爆 provider」的问题。lane 是为「subagent 真并行」
准备的——而那需要先改 subagent 为并发执行，属于新功能不是重构。

**重开条件**：subagent 改为并行执行时（届时 lane 与 mailbox 的隔离
才成为真问题）。

---

## 2. 本刀范围（最小必要件）

| # | 事项 | 工期 | 理由 |
|---|---|---|---|
| R5.1 | **ModelHub**：provider/model 解析唯一入口 | ~0.5d | 20+ 处散点，真收口 |
| R5.2 | 20+ 处调用点改为经 ModelHub（**保留 `LLMConfig.load()` 作底层实现**） | ~0.5d | 不删旧 API（它仍是装载器），只收口「解析序」 |
| R5.3 | 不支持则拒绝、不降级为别的 provider | 含在 R5.1 | 图上红线 |

**命名**：叫 `ModelHub` 而不是 `RuntimeHub`——因为它只解析模型，
不解析图。图上的 RuntimeHub 格改为「部分落地（模型侧）」，虚线的图绑定
部分保留并注明无用户。

### 2.1 ModelHub 契约

```python
# core/llm/model_hub.py
@dataclass(frozen=True)
class ModelBinding:
    provider: str
    model: str
    config: Any            # LLMConfig（不动现有契约）
    source: str            # "explicit" | "config" | "provider_default"

def resolve(**overrides) -> ModelBinding
    """解析序：显式 overrides > llm.json 的 provider profile > provider 默认。
    配置非法（provider 不认识/model 与 provider 不符）→ ModelHubError，
    不猜、不降级。"""
```

- 底层仍调 `LLMConfig.load()`（它已实现分层装载 + provider 默认填充），
  ModelHub 只**收口解析序 + 拒绝语义 + 单一入口**；
- `get_client(**overrides)` 返回 `OpenAICompatClient(binding.config)`；
- 20+ 处 `OpenAICompatClient(LLMConfig.load())` → `get_client()`。

## 3. 测试计划（`tests/test_r5_model_hub.py`）

| # | 用例 | 断言 |
|---|---|---|
| 1 | 显式 overrides 优先 | provider/model 按参数生效，source=explicit |
| 2 | 回落 llm.json | 无 overrides 时取配置值，source=config |
| 3 | provider 默认填充 | model 缺省时取 provider 默认，source=provider_default |
| 4 | 非法 provider 拒绝 | 抛 ModelHubError（不降级） |
| 5 | 客户端构造 | `get_client()` 返回带正确 config 的客户端 |
| 6 | 等价 | 无 overrides 的 `get_client()` 与 `OpenAICompatClient(LLMConfig.load())` 同参 |

## 4. 验收对照

| 门 | 判定 |
|---|---|
| 行为等价 | 所有调用点语义不变（同一 config 对象路径）；仅解析序集中 |
| 测试不减 | inventory 0 removed |
| 失败集不扩大 | c1_gate 对基线 |

## 5. 对架构图的影响

| 格 | 原标注 | 新标注 |
|---|---|---|
| RuntimeHub | ⏳ 未建（整格） | **部分 ✅（模型侧 ModelHub）/ ⏳ 图绑定无用户** |
| 节点类型 cpu | ⏳ | **不做（等价物已有：回测后台子进程 + fanout-t2）**，标注重开条件 |
| 节点类型 graph | ⏳ | **不做（无用户）**，标注重开条件 |
| subagent lane | ⏳ | **不做（subagent 当前同步串行，无并发问题）**，标注重开条件 |
