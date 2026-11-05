# vibe-trading 核心模式分析 → strategy-research 借鉴指南

> 调研版本：vibe-trading-ai v0.1.11 | strategy-research v0.3.0
> 调研日期：2026-07-23
> 目的：提炼 vibe-trading 中最值得借鉴的架构模式，给出具体落地路径

---

## 一、vibe-trading 架构总览（精简版）

```
┌─────────────────────────────────────────────────────────┐
│                    用户接入层                              │
│  CLI (REPL)  │  FastAPI REST  │  MCP Server (54 工具)    │
└───────────────┬─────────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────┐
│                   Agent 核心层                            │
│  ContextBuilder ─→ AgentLoop (ReAct) ─→ ToolRegistry     │
│       │                │                     │           │
│  SkillsLoader    5层Context压缩          ~70个BaseTool    │
│  PersistentMemory  目标续跑             只读/写入 分类     │
└───────────────┬─────────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────┐
│                 执行 & 数据层                              │
│  Runner (子进程沙箱)  │  DataLoaderProtocol (20+ 数据源)   │
│  BaseEngine (8 市场)  │  Alpha Zoo (460+ 因子)            │
│  Optimizer (5 种)     │  Validation (MC/BT/WF)            │
└─────────────────────────────────────────────────────────┘
```

---

## 二、7 个核心模式详解 + strategy-research 落地建议

### 模式 1：BaseTool + ToolRegistry（工具基础设施）

**vibe-trading 实现** (`src/agent/tools.py`, 94 行)

```python
class BaseTool(ABC):
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}  # JSON Schema
    repeatable: bool = False
    is_readonly: bool = True

    @classmethod
    def check_available(cls) -> bool:
        """依赖检查：API key、包是否安装"""
        return True

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """返回 JSON 字符串"""

    def to_openai_schema(self) -> Dict:
        """转 OpenAI function calling 格式"""
```

**设计要点：**
- `is_readonly` 标记允许 AgentLoop 并行执行只读工具
- `check_available()` 让缺失依赖的工具静默退出，不报错
- `to_openai_schema()` 统一转 LLM 可用格式
- `ToolRegistry.execute()` 保证返回 JSON（捕获异常包装为 error）

**strategy-research 当前状态：** `core/agent/tools.py` 已有 BaseTool + ToolRegistry，但：
- 缺少 `is_readonly` / `repeatable` 分类
- 缺少 `check_available()` 依赖检查
- 缺少并行执行能力

**落地建议：** P0 — 在现有 BaseTool 中加 3 个字段，改造 AgentLoop 支持只读并行

---

### 模式 2：5 层 Context 压缩

**vibe-trading 实现** (`src/agent/loop.py`, 1607 行)

| 层级 | 触发条件 | 方法 | API 成本 |
|------|---------|------|---------|
| L1 | token > 50% budget | `_microcompact()` — 丢弃旧 tool result | 零 |
| L2 | token > 70% budget | `_context_collapse()` — 长文本 head+tail 保留 | 零 |
| L3 | token > 80% budget | LLM 结构化摘要 | 1 次 API |
| L4 | token > 90% budget | 迭代式增量摘要 | N 次 API |
| L5 | token > 95% budget | 硬截断 | 零 |

**关键实现细节：**
- `_fix_tool_pairs()` — 压缩后修复孤立的 tool_call/tool_result 对
- `estimate_tokens()` — 约 4 字符/token 的粗估
- 目标续跑：当 LLM 生成 answer 但目标未完成时，注入 continuation prompt

**strategy-research 当前状态：** `core/agent/loop.py` 有 3 层压缩（microcompact/collapse/hard truncate），但：
- 缺少 L3/L4 的 LLM 摘要能力
- 缺少 tool pair 修复
- 缺少目标续跑逻辑

**落地建议：** P1 — 先补 tool pair 修复 + 目标续跑，再加 LLM 摘要层

---

### 模式 3：Skills 渐进式披露

**vibe-trading 实现** (`src/agent/skills.py` + `src/skills/`)

```
系统提示词中只放: "- backtest: Run strategy backtests"
load_skill 工具返回:  完整的 SKILL.md 内容（API 契约、工作流、示例）
```

**设计要点：**
- SKILL.md 使用 YAML frontmatter（name, description, category）
- 用户自定义 skill 覆盖内置同名 skill
- 按 category 分组：data-source, strategy, analysis, asset-class, crypto, flow, tool, other
- SkillsLoader 同时扫描 bundled + user 目录

**strategy-research 当前状态：** `core/skills/` 已有 loader + registry，但：
- 系统提示词中未使用渐进式披露（skill 内容可能全量注入）
- 无 `load_skill` 工具让 LLM 按需加载

**落地建议：** P0 — 确认系统提示词只放摘要，加 `load_skill` 工具

---

### 模式 4：AST 沙箱 + 子进程隔离

**vibe-trading 实现** (`backtest/runner.py` + `src/core/runner.py`)

```
Agent 生成 signal_engine.py
    ↓
AST 验证（禁止装饰器、非字面量默认值、循环导入、可执行顶层语句）
    ↓
子进程执行（restricted env: 只传白名单环境变量）
    ↓
Artifact 收集（equity.csv, metrics.csv, trades.csv + schema 校验）
```

**安全设计：**
- `_copy_runtime_env()` 只复制 OS/Python 基础 + 代理/证书 + 只读市场数据 env
- 显式排除 LLM、API server、broker、live-trading 凭证
- `_validate_signal_engine_source()` AST 级别检查

**strategy-research 当前状态：** `core/agent/sandbox.py` 有 AST guard + path resolution，但：
- 无环境变量白名单
- 子进程隔离较弱（`core/backtest.py` 用 subprocess 但无 env 过滤）
- 无 schema 校验 artifacts

**落地建议：** P0 — 加 env 白名单 + artifact schema 校验

---

### 模式 5：DataLoaderProtocol + Fallback Chain

**vibe-trading 实现** (`backtest/loaders/`)

```python
class DataLoaderProtocol(Protocol):
    name: str
    markets: List[str]
    requires_auth: bool
    def is_available(self) -> bool: ...
    def fetch(self, symbols, start, end, interval, fields) -> Dict[str, pd.DataFrame]: ...
```

**Fallback Chain 示例：**
```python
FALLBACK_CHAINS = {
    "a_share": ["tushare", "akshare", "eastmoney", "baostock", "sina"],
    "us": ["yahoo", "yfinance", "alphavantage", "finnhub", "tiingo"],
    "crypto": ["ccxt", "okx"],
    "forex": ["stooq", "alphavantage"],
}
```

**缓存系统：** SHA-256 content-addressed key → parquet + DuckDB，支持 fetch-through

**strategy-research 当前状态：** `core/data_source/` 有 registry + fallback chains，但：
- 无 DataLoaderProtocol 定义（各 loader 接口不统一）
- 无 content-addressed 缓存
- 无 `validate_ohlc()` 数据质量校验

**落地建议：** P1 — 定义 Protocol、加缓存、加 OHLC 校验

---

### 模式 6：Alpha Zoo 注册机制

**vibe-trading 实现** (`src/factors/`)

```
src/factors/zoo/
├── alpha101/    # 101 个因子
├── gtja191/     # 191 个因子
├── academic/    # 10 个因子（Fama-French 等）
└── fundamental/ # 4 个因子
```

**设计要点：**
- **AST 扫描（不导入）：** `load_alpha_meta_from_py()` 用 `ast.literal_eval` 提取 `__alpha_meta__` 字典
- **Lazy import：** 只在 `compute()` 时才 import 模块
- **输出校验：** 检查 shape 匹配、无 inf、NaN 比例 < 95%
- **宽表契约：** 所有算子接收/返回 `index=日期, columns=代码` 的 DataFrame

**算子性能优化：**
- `ts_rank` / `decay_linear` 用 `numpy.sliding_window_view`（45x 加速）
- `ts_argmax` / `ts_argmin` 用 `bottleneck.move_argmax`（350x 加速）

**strategy-research 当前状态：** `core/alpha_zoo/` 有 460+ 因子，但：
- 元数据提取需要 import 模块（慢且有副作用风险）
- 无输出校验（inf/NaN 传播风险）
- 算子无 numpy 优化

**落地建议：** P1 — 改 AST 扫描、加输出校验、关键算子加 numpy 优化

---

### 模式 7：Run Card Trust Layer

**vibe-trading 实现** (`backtest/run_card.py`)

```json
{
  "run_id": "run_20260722_143052_abc123",
  "strategy_hash": "sha256:...",
  "config_hash": "sha256:...",
  "data_sources": ["tushare", "akshare"],
  "market": "china_a",
  "engine": "ChinaAEngine",
  "start_date": "2024-01-01",
  "end_date": "2026-07-22",
  "metrics": { "sharpe": 1.85, "max_dd": -0.12, ... },
  "artifacts": ["equity.csv", "trades.csv", "metrics.csv"],
  "evidence": { "data_source_provenance": {...} }
}
```

**设计要点：**
- SHA-256 策略/配置哈希确保可复现
- 数据来源溯源（哪个 loader、什么时间获取）
- 所有 artifact 文件引用 + schema 校验

**strategy-research 当前状态：** `core/backtest.py` 有 `run_card.json` + SHA-256，但：
- 无数据来源溯源
- 无 artifact schema 校验
- 无 evidence 字段

**落地建议：** P2 — 加数据溯源 + artifact schema 校验

---

## 三、strategy-research 缺失功能清单（按优先级）

### P0 — 基础设施级（1-2 周）

| 功能 | 来源 | 工作量 | 文件 |
|------|------|--------|------|
| BaseTool 加 `is_readonly` + `check_available()` | `src/agent/tools.py` | 0.5 天 | `core/agent/tools.py` |
| AgentLoop 只读工具并行执行 | `src/agent/loop.py` | 2 天 | `core/agent/loop.py` |
| `load_skill` 工具 | `src/tools/` | 0.5 天 | `core/agent/tools.py` |
| DataLoaderProtocol 统一接口 | `backtest/loaders/base.py` | 1 天 | `core/data_source/base.py` |
| 子进程 env 白名单 | `src/core/runner.py` | 0.5 天 | `core/backtest.py` |
| Artifact schema 校验 | `backtest/runner.py` | 1 天 | `core/backtest.py` |

### P1 — 功能增强级（2-4 周）

| 功能 | 来源 | 工作量 | 文件 |
|------|------|--------|------|
| Context L4 层 LLM 摘要 | `src/agent/loop.py` | 3 天 | `core/agent/loop.py` |
| tool pair 修复 | `src/agent/loop.py` | 1 天 | `core/agent/loop.py` |
| 目标续跑 (goal continuation) | `src/agent/loop.py` | 2 天 | `core/agent/loop.py` |
| Content-addressed 数据缓存 | `backtest/loaders/base.py` | 3 天 | `core/data_source/cache.py` |
| OHLC 数据质量校验 | `backtest/loaders/base.py` | 1 天 | `core/data_source/utils.py` |
| Alpha AST 扫描（不导入） | `src/factors/registry.py` | 2 天 | `core/alpha_zoo/registry.py` |
| 因子输出校验 | `src/factors/registry.py` | 1 天 | `core/alpha_zoo/registry.py` |
| PersistentMemory 语义搜索 | `src/memory/persistent.py` | 3 天 | `core/memory/persistent.py` |

### P2 — 体验优化级（4-8 周）

| 功能 | 来源 | 工作量 | 文件 |
|------|------|--------|------|
| Run Card 数据溯源 | `backtest/run_card.py` | 2 天 | `core/backtest.py` |
| SwarmRuntime 拓扑分层 | `src/swarm/runtime.py` | 5 天 | `core/swarm/runtime.py` |
| Optimizer 框架 | `backtest/optimizers/` | 3 天 | `core/engine/optimizers/` |
| Scheduled Research | `src/scheduled_research/` | 3 天 | `core/scheduled_research/` |
| 验证工具 (MC + Bootstrap + WF) | `backtest/validation.py` | 3 天 | 已有，需增强 |
| CLI 拆分（2441 行 → 多模块） | `cli/main.py` | 5 天 | `cli/` |

---

## 四、不应借鉴的功能

| 功能 | 原因 |
|------|------|
| Channels（14 IM + Email） | Research-only 项目不需要消息分发 |
| Live Trading（broker + mandate + halt） | 明确 out of scope |
| Shadow Account | 前提条件：用户交易日志，不现实 |
| WebUI / SPA 前端 | 已有 HTMX WebUI，够用 |
| LangChain/LangGraph 依赖 | 已验证 httpx 够用，不引入重依赖 |
| 54 个 MCP 工具 | 大部分是 A 股门户专属，不需要 |

---

## 五、关键代码片段（可直接复用）

### 1. BaseTool 最小改动（加 3 个字段）

```python
# 在现有 BaseTool 中添加：
class BaseTool(ABC):
    # ... 现有字段 ...
    is_readonly: bool = True
    repeatable: bool = False

    @classmethod
    def check_available(cls) -> bool:
        return True
```

### 2. ToolRegistry 并行执行

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

class ToolRegistry:
    # ... 现有代码 ...

    def execute_batch(self, calls: List[Dict]) -> List[str]:
        """并行执行只读工具，串行执行写入工具"""
        readonly = [(c, self._tools[c["name"]]) for c in calls
                     if self._tools[c["name"]].is_readonly]
        writable = [(c, self._tools[c["name"]]) for c in calls
                     if not self._tools[c["name"]].is_readonly]

        results = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(self.execute, c["name"], c["params"]): c
                       for c, _ in readonly}
            for f in as_completed(futures):
                results.append(f.result())

        for c, _ in writable:
            results.append(self.execute(c["name"], c["params"]))

        return results
```

### 3. OHLC 数据校验

```python
def validate_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """删除违反 OHLC 不变量的 bar"""
    before = len(df)
    mask = (
        (df["high"] >= df["low"]) &
        (df["high"] >= df["open"]) &
        (df["high"] >= df["close"]) &
        (df["low"] <= df["open"]) &
        (df["low"] <= df["close"]) &
        (df["open"] > 0) &
        (df["close"] > 0)
    )
    df = df[mask]
    if len(df) < before:
        logger.warning("validate_ohlc: dropped %d invalid bars", before - len(df))
    return df
```

### 4. AST 扫描因子元数据（不导入）

```python
import ast

def load_alpha_meta_from_py(path: Path) -> dict | None:
    """从 .py 文件中提取 __alpha_meta__ 字典，不执行代码"""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__alpha_meta__":
                    return ast.literal_eval(node.value)
    return None
```

---

## 六、总结

vibe-trading 最值得借鉴的不是具体功能，而是 **3 个架构原则**：

1. **协议优于继承** — DataLoaderProtocol、AlphaCompute、BaseTool 都是 duck typing，易于扩展
2. **安全纵深防御** — AST 沙箱 → env 白名单 → schema 校验 → 数据溯源，多层防护
3. **渐进式复杂度** — Skills 只放摘要、Alpha lazy import、Context 分层压缩，按需加载

strategy-research 已有 80% 的骨架，差距主要在 **执行层细节**（并行、缓存、校验、溯源），而非架构设计。
