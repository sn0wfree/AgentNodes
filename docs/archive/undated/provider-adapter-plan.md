# Provider Adapter Architecture Implementation Plan

## Date: 2026-07-30

## Background

Provider-specific logic is currently scattered across multiple files:
- `config.py`: PROVIDER_DEFAULTS hardcoded
- `openai_client.py`: MiniMax-specific 403/429 handling, error code parsing
- `parser.py`: SSE format assumes OpenAI conventions
- `system.py`: Duplicate provider_env_map
- `onboard.py`: Third copy of provider knowledge

**Problem**: Adding a new provider requires modifying multiple core files.

## Solution: Provider Adapter Pattern

All provider-specific logic centralized in `provider/` directory. Adding a new provider = creating a new file + registering.

## Directory Structure

```
src/strategy_research/core/llm/
├── config.py              # Add enable_thinking, use provider defaults
├── parser.py              # Use provider adapter for thinking extraction
├── openai_client.py       # Use provider adapter for headers/errors
├── provider/
│   ├── __init__.py        # Registry
│   ├── base.py            # ProviderAdapter interface
│   ├── openai.py          # OpenAI adapter
│   ├── deepseek.py        # DeepSeek adapter
│   ├── minimax.py         # MiniMax adapter
│   ├── qwen.py            # Qwen adapter
│   ├── kimi.py            # Kimi adapter
│   └── fallback.py        # Default fallback
```

## ProviderAdapter Interface

```python
class ProviderAdapter(ABC):
    # Metadata (replaces PROVIDER_DEFAULTS)
    @property name -> str
    @property default_base_url -> str
    @property default_model -> str
    @property default_max_tokens -> int
    
    # Thinking Tokens
    def extract_thinking_from_delta(delta) -> str | None
    def extract_thinking_from_message(message) -> str | None
    def normalize_thinking(text) -> str
    
    # HTTP Layer
    def custom_headers(config) -> dict[str, str]
    def custom_payload(payload, config) -> dict
    def custom_stream_options() -> dict | None
    
    # Error Handling
    def extract_error_code(body) -> str
    def handle_error(status, body) -> Exception | None
    def quota_error_message() -> str
    
    # UI/TUI
    def reasoning_tag_patterns() -> list[str]
```

## Provider Field Mapping

| Provider | Thinking Field | Error Codes | Special |
|----------|---------------|-------------|---------|
| openai | `delta.reasoning` (o1/o3) | Standard | stream_options |
| deepseek | `delta.reasoning_content` | Standard | - |
| minimax | `<think>` tags in `delta.content` | 403=quota | 5-hour quota |
| qwen | `delta.reasoning_content` | Standard | - |
| kimi | None | Standard | - |

## Implementation Order

1. Create provider/base.py (interface)
2. Create provider implementations (openai, deepseek, minimax, qwen, kimi, fallback)
3. Create provider/__init__.py (registry)
4. Update parser.py (use provider for thinking)
5. Update openai_client.py (use provider for headers/errors)
6. Update config.py (use provider for defaults)
7. Update loop.py (handle delta_thinking)
8. Remove frontend tag parsing
9. Run tests + build frontend

## Adding New Provider (Future)

1. Create `provider/new_provider.py` with `NewProviderAdapter` class
2. Register in `provider/__init__.py`: `_REGISTRY["new_provider"] = NewProviderAdapter`
3. Done. No core file changes needed.

## Testing

- Unit tests for each provider adapter
- Unit tests for thinking extraction
- Unit tests for error handling
- Integration test for full streaming flow