# Model Catalog 设计文档

## 目标

为 chat 路径提供模型元数据（context 窗口、最大输出、支持能力、价格），支持**离线 + 在线**两种场景。**三层回退**保证任何情况下都有可用数据。

## 现状

- `ProviderAdapter` 当前只暴露 `default_max_tokens`（8192 兜底），无 `default_context_tokens`
- 模型元数据（context 窗口、functions、vision、cost）需要联网获取
- 110 个 provider × 数十个 model 的数据源是 [models.dev](https://models.dev)，OpenAI 兼容
- 国内访问 `models.dev/api.json` 困难，但 `cdn.jsdelivr.net` 与 `raw.githubusercontent.com` 可达

## 设计

### 三层数据流

```
┌─────────────────────────────────────────────────────────────┐
│ L1 源码内置 (Bundled Fallback)                              │
│   src/strategy_research/core/llm/data/                      │
│   ├── _index.json                          # 5 个 provider 索引│
│   ├── _default_fallback.json               # 静态兜底          │
│   ├── providers/minimax/minimax-M3.toml    # 575B             │
│   ├── providers/openai/gpt-4o-mini.toml    # 491B             │
│   ├── providers/deepseek/deepseek-chat.toml# 447B             │
│   ├── providers/alibaba/qwen-plus.toml     # 518B             │
│   └── providers/moonshotai/kimi-k2.5.toml  # 568B             │
│   总计: ~2.6KB PACKAGED, 永远可用                            │
└─────────────────────────────────────────────────────────────┘
                          ↓ 启动 5s 后异步
┌─────────────────────────────────────────────────────────────┐
│ L2 启动时下载 (Async Refresh)                                │
│   优先级:                                                    │
│     1. cdn.jsdelivr.net/gh/anomalyco/models.dev@dev   (国内) │
│     2. raw.githubusercontent.com/anomalyco/models.dev/dev   │
│   写入磁盘缓存:                                              │
│     ~/.quantnodes/model_catalog.json  TTL=7d                 │
│   失败: 保留 bundled 数据, source="fallback"                │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ L3 未知 provider 按需拉 (On-Demand)                          │
│   用户切换到未内置的 provider                                │
│   路径: providers/{id}/models/{model}.toml                  │
│   失败: 用 _default_fallback.json 兜底                      │
└─────────────────────────────────────────────────────────────┘
```

### 数据源：models.dev

理由：

1. **opencode 也在用**（参考其内部使用），口径一致
2. **TOML 格式简单**（单文件 < 1KB），按需下载
3. **数据丰富**：`[limit]`、`[cost]`、`[modalities]`、`reasoning_options`、`benchmarks`
4. **不依赖 litellm**（避免 30MB+ 运行时负担）

### 路径映射

| 内部 provider name | models.dev provider_id | 备注 |
|-------------------|------------------------|------|
| `minimax` | `minimax-cn-coding-plan` | 默认走中国区 token plan |
| `minimax-cn` | `minimax-cn` | |
| `minimax-cn-coding-plan` | `minimax-cn-coding-plan` | |
| `minimax-coding-plan` | `minimax-coding-plan` | |
| `openai` | `openai` | |
| `deepseek` | `deepseek` | |
| `qwen` | `alibaba` | 厂商在阿里 |
| `kimi` | `moonshotai` | 厂商在 Moonshot AI |
| 其他 | n/a | FallbackAdapter 内部硬编码 |

定义在 `core/llm/provider/__init__.py::MODELS_DEV_ID`，由 `models_dev_id()` 函数解析。

### ModelInfo 数据结构

```python
@dataclass(frozen=True)
class ModelInfo:
    context_tokens: int
    max_output_tokens: int
    supports_vision: bool
    supports_audio: bool
    supports_pdf: bool
    supports_tools: bool
    supports_reasoning: bool
    supports_structured_output: bool
    cost_input: float | None        # USD / 1M tokens
    cost_output: float | None
    cost_cache_read: float | None
    cost_cache_write: float | None
    description: str
    release_date: str | None
    source: Literal["bundled", "cached", "fetched", "fallback"]
    fetched_at: str | None
```

### 核心 API

```python
# core/llm/model_catalog.py
class ModelCatalog:
    BASE_URLS = [
        "https://cdn.jsdelivr.net/gh/anomalyco/models.dev@dev",
        "https://raw.githubusercontent.com/anomalyco/models.dev/dev",
    ]
    TIMEOUT = 5.0
    CACHE_TTL = 7 * 24 * 3600  # 7 days

    def get_info(self, provider: str, model: str) -> ModelInfo:
        """同步返回：30s 内返回 cache / bundled / fallback
        不阻塞主流程。"""

    async def refresh_async(self, provider: str, model: str) -> None:
        """后台下载最新数据，更新磁盘缓存。失败保留 bundled。"""

    async def refresh_all_async(self) -> int:
        """可选：批量刷新 - 使用 GitHub API 遍历 110 个 provider。
        应用启动时跳过，由用户手动触发。"""
```

### 启动流程

```python
# api/app_startup.py
async def startup_background_tasks():
    await asyncio.sleep(5)  # 等首屏先加载
    catalog = ModelCatalog()
    config = load_llm_config()
    try:
        await catalog.refresh_async(config.provider, config.model)
    except Exception:
        logger.warning("Initial model catalog refresh failed; using bundled")
```

### API 端点

#### `GET /api/system/info`（扩展）

```json
{
  "workspace_path": "...",
  "user_count": 1,
  "llm": {"provider": "minimax", "model": "minimax-M3", ...},
  "model_info": {
    "provider": "minimax",
    "model": "minimax-M3",
    "context_tokens": 1000000,
    "max_output_tokens": 128000,
    "supports_vision": true,
    "supports_tools": true,
    "supports_reasoning": true,
    "cost_input": 0.30,
    "cost_output": 1.20,
    "cache_read": 0.06,
    "source": "bundled",
    "fetched_at": null
  }
}
```

#### `POST /api/system/model-info/refresh`

- Body: `{}` 或 `{provider, model}`
- 无 body: 刷新当前 LLM 配置
- 带 body: 刷新特定项
- 强制忽略 TTL

### Token 用量追踪（关联上下文窗口）

LLM 完成时后端 emit `llm_usage` 事件（已有，参见 `core/agent/loop.py:284, 370`）。SessionService 累加到 `attempt.metrics`，emit `session_total_tokens` 给前端。

```python
# api/session/service.py:event_callback
if event_type == "llm_usage":
    data = chunk.usage
    metrics["total_input_tokens"] += data.get("input_tokens", 0)
    metrics["total_output_tokens"] += data.get("output_tokens", 0)
    # Forward to frontend
    self.event_bus.emit(session_id, "session_total_tokens", {
        "total_tokens": metrics["total_input_tokens"] + metrics["total_output_tokens"],
        "input_tokens": metrics["total_input_tokens"],
        "output_tokens": metrics["total_output_tokens"],
    })
```

### 前端 UI

#### `ContextUsageBar` 组件

- 位置：`MessageList` 顶部（与 QueuePauseBanner 同一栏）
- 样式：扁平进度条，3 档颜色
  - `< 50%`: emerald-500
  - `50-80%`: amber-500
  - `> 80%`: red-500
- 文字：`8.2K / 128K (6%)`
- source 提示：`fallback` 时显示 ⚠️ 图标

#### Store 改动

- `useSystemStore.modelInfo: ModelInfo | null`
- `useChatStore.tokensUsed: Map<sessionId, number>` + `setTokensUsed`

#### useSSE 监听

```typescript
case 'llm_usage': {
  const usage = data as {input_tokens?: number, output_tokens?: number, total_tokens?: number}
  const total = usage.total_tokens ?? (usage.input_tokens + usage.output_tokens) ?? 0
  if (sessionId && total > 0) {
    setTokensUsed(sessionId, total)
  }
}
case 'session_total_tokens': {
  const { total_tokens } = data as { total_tokens: number }
  if (sessionId && total_tokens !== undefined) {
    setTokensUsed(sessionId, total_tokens)
  }
}
```

### 数据流时序

```
用户启动 server
  → 5s 后 background task:
    → catalog.refresh_async("minimax", "minimax-M3")
    → 尝试 jsdelivr (1s)
    → 成功 → 写入磁盘缓存
    → 失败 → 尝试 raw github
    → 失败 → 保留 bundled
  → 用户打开 chat
    → GET /api/system/info
    → 来源: model_info (从磁盘缓存或 bundled)
    → 返回给前端
  → 用户发消息
    → 后端 emit llm_usage 事件
    → SessionService 累加到 metrics
    → emit session_total_tokens
    → 前端 useSSE 监听 → 更新 tokensUsed
  → MessageList 顶部渲染 <ContextUsageBar tokensUsed={...} limit={...} />
```

### 错误处理

| 场景 | 行为 |
|------|------|
| 启动时 fetch 失败 | 保留 bundled，log warning |
| 运行时 fetch 失败 | 保留旧缓存，log debug |
| 缓存文件损坏 | catch JSON parse → 重置 |
| 已知 provider 但未知 model | 失败 → 用 provider 级 default |
| 未知 provider | 失败 → 用 `_default_fallback.json` |
| Bundled 文件缺失 | 启动时报错（视为打包 bug） |

### 风险评估

| 风险 | 缓解 |
|------|------|
| jsdelivr 国内偶发不通 | 仍有 raw github 兜底 |
| GitHub 60 req/h 限流 | TTL 7 天 + bundled 兜底 |
| minimax user 实际走 minimax.cn-coding-plan | `MODELS_DEV_ID["minimax"] = "minimax-cn-coding-plan"` |
| Refresh 启动阻塞首屏 | 5s delay + async task |
| 缓存文件损坏 | try/except JSON parse → 重置 |
| models.dev 改名/迁移 | 失败后走 bundled，前端显示 stale hint |

### 文件清单

#### Bundled 数据（5 个）
- `src/strategy_research/core/llm/data/_index.json`
- `src/strategy_research/core/llm/data/_default_fallback.json`
- `src/strategy_research/core/llm/data/providers/minimax/minimax-M3.toml`
- `src/strategy_research/core/llm/data/providers/openai/gpt-4o-mini.toml`
- `src/strategy_research/core/llm/data/providers/deepseek/deepseek-chat.toml`
- `src/strategy_research/core/llm/data/providers/alibaba/qwen-plus.toml`
- `src/strategy_research/core/llm/data/providers/moonshotai/kimi-k2.5.toml`

#### 后端代码
- `src/strategy_research/core/llm/model_catalog.py`（新）
- `src/strategy_research/core/llm/provider/base.py`（加 `default_context_tokens`）
- `src/strategy_research/core/llm/provider/{minimax,openai,deepseek,qwen,kimi}.py`（实现）
- `src/strategy_research/core/llm/provider/__init__.py`（加 `MODELS_DEV_ID`）
- `src/strategy_research/api/routers/system.py`（加端点）
- `src/strategy_research/api/app_startup.py`（启动 task）
- `src/strategy_research/api/session/service.py`（`llm_usage` 累加）

#### 前端代码
- `webui/frontend/src/stores/system.ts`（加 `modelInfo`）
- `webui/frontend/src/stores/chat.ts`（加 `tokensUsed: Map`）
- `webui/frontend/src/hooks/useSSE.ts`（监听 `llm_usage`）
- `webui/frontend/src/components/chat/ContextUsageBar.tsx`（新）
- `webui/frontend/src/components/chat/MessageList.tsx`（渲染）

#### 测试
- `tests/test_model_catalog.py`（8 测试）
- `tests/test_provider_adapter.py`（+1 测试）
- `tests/test_system_api.py`（+1 测试）
- `webui/frontend/src/test/ContextUsageBar.test.tsx`（新）

### 验证

- `pytest tests/test_model_catalog.py tests/test_provider_adapter.py` 全通过
- `pytest tests/test_system_api.py` 全通过
- `cd webui/frontend && vitest run` 全通过
- 类型检查 + lint 通过
- 手动验证：重启 server → /api/system/info 返回 model_info → ContextUsageBar 显示
