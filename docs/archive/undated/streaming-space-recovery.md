# Streaming 空格根因修复 + 解析管线标准化

## Date: 2026-08-03

## 背景与问题

DeepSeek-V4-Flash（SiliconFlow / DeepSeek 官方 API）流式输出时，英文单词
之间的空格全部丢失：`Let me explore` 变成 `Letmeexplore`。此现象此前被
误归因于"上游 BPE tokenizer 丢弃前导空格"（见 webui
`utils/mastraSmoothStream.ts` 注释），前端加了 `shouldInsertSpaceBetween`
启发式兜底——但它只作用于前端展示缓冲（`partTextAccumDelta`），**不改
后端持久化**，因此硬刷新后从 DB 读回的空格仍然缺失。

## 根因（已定位）

空格不是上游丢的，而是**我们自己的提取链每 chunk `.strip()` 删掉的**：

| 位置 | 代码 | 机制 |
|---|---|---|
| `provider/base.py:123` | `normalize_thinking`: `re.sub(r"\s+", " ", text).strip()` | `extract_thinking_from_delta` 每 chunk 调用 → 每个 chunk 首尾空格被删 |
| `provider/_dsml_patterns.py:86` | `strip_dsml_text`: `return cleaned.strip()` | SiliconFlow/DeepSeek 的 `strip_dsml_from_delta` 对**每个 chunk** 调用 → 同样删边界空格 |
| `provider/minimax.py:56,65` | `THINK_PATTERN.sub(...).strip()` | 同类问题（content 内嵌 think 标签剥离） |

**机制**：DeepSeek `reasoning_content` 每 SSE chunk 一个 BPE token，空格编码在
token **前导**（`" me"`、`" explore"`）。`extract_thinking_from_delta` →
`normalize_thinking` 的 `.strip()` 把前导空格删掉 → `"me"`、`"explore"` →
拼接 `"Letmeexplore"`。

## 方案：strip 参数化 + 解析管线标准化

### 1. 根因修复（无状态）

`normalize_thinking(text, strip_edges=True)` / `strip_dsml_text(text, strip_edges=True)`
加参数。**流式 delta 路径传 `False`**（保留 chunk 边界空格，BPE 前导空格得以
保留）；**完整 message 路径保持 `True`**（完整文本该 strip 仍 strip）。

### 2. 解析管线标准化（parser 内 4 步）

```
parse_stream_chunk(line, adapter=None) → _chunk_from_dict(payload, adapter)
    Step1: payload = adapter.fix_delta(payload)              # 预留兜底，默认 passthrough、无状态
    Step3: thinking = adapter.extract_thinking_from_delta(payload)
    Step2: payload = adapter.sanitize_delta(payload)         # 合并后的净化 hook
    Step4: ProcessedDelta → StreamChunk

parse_chat_response(raw, adapter=None)                        # 非流式：无 Step1，Step3-2 用 message 变体
```

- **顺序（重要）**：extract 必须**先于** sanitize——sanitize 会删除 MiniMax
  的 `<think>` 标签，extract 依赖原始标签；DeepSeek 的 DSML 清洗搬进
  extract 内部（只清 `reasoning_content`），sanitize 只清最终交付的
  `content` 字段
- 中间结构 `ProcessedDelta` / `ProcessedMessage`：净化提取与组装解耦
- **破坏性改造**：`provider_name` 参数 → `adapter` 注入（`adapter=None` 走
  FallbackAdapter），全库统一（client 3 处 + loop 2 处 + tests ~13 处）
- `strip_dsml_from_delta/message` + `strip_thinking_from_delta/message` 合并为
  `sanitize_delta` / `sanitize_message` 单 hook（无 provider 同时 override 两者，
  合并后顺序由 provider 自行控制）

### 3. client 门面收敛

- **不绑定** `self.adapter`；每请求（chat/achat/stream/astream）开头
  `adapter = get_provider(config.provider)` 局部变量贯穿全请求
  （per-request 实例 → fix_delta 将来启用状态时天然隔离，无需 reset）
- `_build_headers` / `_build_payload` / `_raise_for_status` 接收 `adapter` 参数
- 新增公开门面 `parse_response(raw)`：`parse_chat_response(raw, adapter=...)`
- loop.py 2 处（L371/L502）改 `self.client.parse_response(raw_response)`，不再直接调 parser

### 4. fix_delta：预留兜底

本次**不实现**（根因修复后正常情况拼接正确）。若将来发现上游跨词边界仍丢
空格（如 chunk 以 `word` 结尾 + 下个 chunk 以 `word` 开头），按 per-request
实例方案补兜底：`fix_delta` 内部维护 tail 状态，`\S` 结尾 + `^[A-Za-z]` 开头
补空格（与前端 `shouldInsertSpaceBetween` 同规则）。

## 文件改动清单

| 文件 | 改动 |
|---|---|
| `provider/base.py` | `normalize_thinking` 加 `strip_edges` 参数；删旧 strip 方法，换 `sanitize_delta/message`（默认 passthrough）；新增 `fix_delta`（默认 passthrough） |
| `provider/_reasoning_field.py` | `extract_thinking_from_delta` → `normalize_thinking(content, strip_edges=False)` |
| `provider/_dsml_patterns.py` | `strip_dsml_text(text, strip_edges=True)` |
| `provider/siliconflow.py` / `deepseek.py` | `extract_thinking_from_delta/message` override（内部 strip DSML + normalize）；`sanitize_delta/message` 只清 `content`（strip_edges=False/True） |
| `provider/minimax.py` | `sanitize_delta/message`（`.strip()` 参数化，delta 不 strip）；extract 用 `strip_edges=False` |
| `parser.py` | `ProcessedDelta/ProcessedMessage`；`_process_delta/_process_message`（extract → sanitize 顺序）；`parse_stream_chunk/parse_chat_response` 签名改 `adapter=` |
| `openai_client.py` | per-request adapter；`parse_response` 门面；helpers 收 adapter 参数 |
| `loop.py` | 2 处改 `self.client.parse_response(raw)` |

## 测试计划

- 新增 `tests/test_space_recovery.py`：模拟 BPE chunk 流 `"Let"` + `" me"` +
  `" explore"` → 每 chunk 过 `extract_thinking_from_delta` → 拼接 ==
  `"Let me explore"`；`strip_dsml_text(strip_edges=False)` 边界；MiniMax
  sanitize；message 路径 strip 行为不变；`fix_delta` 默认 no-op
- 更新 `tests/test_provider_adapter.py` / `tests/test_llm_parser_edge_cases.py`：
  `provider_name=` → `adapter=`
- `tests/test_provider_dsml.py` 兼容（默认 `strip_edges=True` 行为不变）

## 验证步骤

1. `pytest tests/test_space_recovery.py` + 相关测试全过
2. `ruff check` 0 新错
3. e2e：`SR_LOG_LEVEL=DEBUG` 重启 → 发消息 → 日志 `delta_thinking` 带前导空格
   → `message_parts` 查 thinking 文本含 `"QuantNodes Research"`（带空格）
   → 硬刷新后 thinking 块无 `Letmeexplore`
4. 切 OpenAI/Qwen/Kimi 验证零影响（passthrough 路径）
5. 前端 `shouldInsertSpaceBetween` 保留（老 DB 兜底；新流 prev 末尾已是空格，
   正则 `[A-Za-z]$` 不匹配 → 自动 no-op，不会双空格）

## 边界与已知项

- 老消息（已写入 DB 的无空格文本）不补 backfill
- `normalize_thinking` 的 markdown 代码块剥离（` ``` ` 跨 chunk）在流式路径
  本来就失效——既有行为，本次不动
- 双空格风险：上游 BPE 为前导空格 token 模式，正常不触发；若 e2e 观察到，
  启用 `fix_delta` 兜底去重
