# 解循环导入：`phase_engine ↔ runner`（2026-10-30）

> 动机：架构图 ⑤ 研究域的「方法论归位 core/study/method/」以本项为前置
> （`docs/design/seam-contract-platform-research-20261030.md` §7）。
> 三道门沿用。**纯域内重构，不碰对外契约、平台层、数据面。**

---

## 1. 现状：环的精确形状

```
runner.py (54KB)
  ├─ import phase_engine.BranchContext        (line 653, 函数内)
  └─ import phase_engine.run_round_phases     (line 1004, 函数内)

phase_engine.py (36KB)
  ├─ import runner.SR_AGENT_MAX_ITER          (line 18, 模块级)  ★ 真正的环
  ├─ import runner.SR_STUDY_COLLECT_INTERVAL  (line 833, 函数内)
  ├─ import runner.SR_STUDY_MAX_DEVIATION     (line 833, 函数内)
  └─ import runner.ShutdownReason             (line 833, 函数内)

engine_common.py
  └─ import runner.ShutdownReason             (line 163, 函数内)
```

**关键判断**：`phase_engine` 对 `runner` 的所有**运行时**使用都是
`runner._xxx()` —— 鸭子类型，通过参数传入，不是 import 耦合。
需要搬的只有 **纯数据符号**（8 个常量 + 1 个枚举 + 1 个纯函数，
实现时核实，比初估的 4 个多）。

## 2. 方案：符号下沉到 `study/contracts.py`

新建 `core/study/contracts.py`：

```python
"""研究域共享契约：调参常量、停因枚举、纯函数。

下沉原因：phase_engine ↔ runner 循环导入的唯一实质是这些纯数据符号。
它们不依赖任何运行时对象，是「两侧都要用的词汇表」——正确位置是独立的
契约模块，而不是任何一方的实现文件。
"""

# 8 个常量（原样搬，值不变）
SR_STUDY_MAX_DEVIATION = 3
SR_STUDY_COLLECT_INTERVAL = 5
SR_STUDY_MAX_DISCARD = 5
SR_AGENT_MAX_ITER = 50
SR_AGENT_NO_PROGRESS_WINDOW = 5
SR_AGENT_MAX_PARSE_RETRIES = 2
SR_AGENT_PARSE_BACKOFF_BASE = 5.0
SR_AGENT_PARSE_BACKOFF_MAX = 30.0

class ShutdownReason:   # 13 个停因，原样搬
    ...

def parse_failed_agents(exec_result) -> list[str]:   # 纯函数
    ...
```

改动：

| 文件 | 改动 |
|---|---|
| `contracts.py` | **新建**，接收上述符号（原样搬，不改值） |
| `runner.py` | 删定义，改为 `from .contracts import ...` + 模块级 re-export（保持 `runner.SR_AGENT_MAX_ITER` / `_parse_failed_agents` 等旧路径可用） |
| `phase_engine.py` | `from .runner import ...` → `from .contracts import ...`（模块级 1 处 + 函数内 2 处） |
| `engine_common.py` | `from .runner import ShutdownReason` → `from .contracts import ShutdownReason` |

**保留 runner 的 re-export**：外部有大量 `from .runner import ShutdownReason`
之类的引用（scheduler/runner_aegis/monitor 等），一次性全改是扩大 diff。
re-export 不产生循环（contracts 不 import runner）。

## 3. 解环验证

```python
# tests/test_study_no_cycles.py
def test_no_circular_import_between_engine_modules():
    """runner ↔ phase_engine 不得互相 import（模块级或函数级都算）。"""
    import ast
    # 断言 phase_engine 的 AST 中无任何 `runner` 的 import
```

比「import 能跑通」更强：AST 级检查，连函数内 import 也抓。

同时加一条通用门禁：**域内模块不得互相 import 形成环**（用已有的
`importlib` 拓扑检查，只检查 core/study 内部）。

## 4. 收益（不只是整洁）

| 收益 | 说明 |
|---|---|
| 方法论可剥离 | `phase_engine` 不再依赖 runner 实现，才能搬进 `method/` |
| 可独立测试 | 目前测 phase_engine 必须先建 runner 实例；解环后可用 stub |
| 依赖方向单一 | runner → phase_engine（执行循环调用阶段函数），符合「执行调用方法」的语义 |
| 词汇表归位 | 常量/枚举集中一处，避免两侧各拷一份后漂移 |

## 5. 明确不做

- 不拆 `phase_engine` 的 16 个函数（那是方法论归位的工作，本项只解环）；
- 不改任何运行时行为（纯搬符号）；
- 不动 `phase_engine` 通过参数使用 `runner` 的鸭子类型调用（那是设计，
  不是缺陷——engine_common 也是同一模式）。

## 6. 测试计划

| # | 用例 | 断言 |
|---|---|---|
| 1 | AST：phase_engine 无 runner import | 通过 |
| 2 | AST：engine_common 无 runner import | 通过 |
| 3 | 旧路径仍可用 | `from strategy_research.core.study.runner import ShutdownReason` / `SR_AGENT_MAX_ITER` |
| 4 | 值不变 | 4 个符号的值与搬前逐字相同 |
| 5 | 域内无环（通用检查） | core/study 内 import 图无环 |

## 7. 验收对照

| 门 | 判定 |
|---|---|
| 行为等价 | 纯搬符号 + re-export，运行时零变化 |
| 测试不减 | inventory 0 removed |
| 失败集不扩大 | c1_gate 对基线 |
