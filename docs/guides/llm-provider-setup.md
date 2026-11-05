# LLM Provider 接入指南（免费/低成本）

Date: 2026-08-02
Related: `core/llm/config.py`, `core/llm/provider/`, `cli/commands/llm.py`,
`api/routers/system.py`, `~/.quantnodes/llm.json`, `~/.quantnodes/.env`

本文记录如何为本项目接入（或切换）各家 LLM 服务。**免费政策经常变动，
表中额度为写稿时的观察，接入前请以官网为准。**

## 配置机制

### 1. `~/.quantnodes/llm.json` — 供应商配置（唯一权威来源）

```json
{
  "llm": {
    "active_profile": "nvidia",
    "timeout": 120,
    "max_retries": 3,
    "profiles": {
      "nvidia": {
        "provider": "nvidia",
        "model": "z-ai/glm-5.2",
        "api_key": "env:NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model_context_tokens": 131072,
        "model_max_output_tokens": 16384
      }
    }
  }
}
```

字段说明：

| 字段 | 含义 |
|---|---|
| `active_profile` | 当前生效的 profile 名 |
| `profiles.<name>.provider` | 适配器名（见下方 registry 表；未知名会回退到 OpenAI 兼容行为） |
| `profiles.<name>.api_key` | **`env:VAR_NAME` 引用形式**（推荐，密钥不入 JSON）或明文（不推荐） |
| `profiles.<name>.base_url` | OpenAI 兼容端点根（`.../v1`） |
| `model_context_tokens` / `model_max_output_tokens` | 压缩/上下文预算用，尽量按模型真实窗口填 |

### 2. `~/.quantnodes/.env` — 密钥

```bash
# 每行一个，0600 权限
ZHIPU_API_KEY=xxxx
GEMINI_API_KEY=xxxx
```

### 3. 切换方式（三选一，优先级从低到高）

```bash
sr llm --use <name>          # 改 llm.json 的 active_profile（registry 内 provider 可用）
LLM_PROFILE=<name> sr ...    # 环境变量覆盖
sr llm --use <name>          # 之后可用 --llm-profile 单次覆盖（等价于 LLM_PROFILE）
```

其它命令：`sr llm --list`（列 provider + 密钥状态）、`sr llm --show`
（当前生效配置，密钥掩码）、`sr llm --add-key <name>`（交互录入
`<NAME>_API_KEY` 到 .env）。

WebUI：SettingsModal 的 LLM 面板（GET/PUT `/api/system/llm`）可切换
provider/模型，密钥输入留空 = 不修改。

### 4. Provider registry

内置适配器（`core/llm/provider/*.py`）决定 `--use` 可自动创建哪些
profile、以及各家的默认 base_url/模型/上下文窗口：

`nvidia` · `minimax` · `openai` · `deepseek` · `qwen` · `kimi` ·
`siliconflow`（硅基流动，默认 `deepseek-ai/DeepSeek-V4-Flash`）

**registry 之外的服务（智谱/硅基流动/Gemini/Groq 等）不能 `--use`
直接创建**，需手动加 profile（见下文「接入步骤」）；运行时对未知
provider 名回退 OpenAI 兼容行为，所以手动 profile 也可用。

## 供应商接入参数

### 国内直连（无需代理）

| 服务 | base_url | 推荐模型 | key 环境变量 | 免费/低价情况（写稿时） |
|---|---|---|---|---|
| **智谱 AI** | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash`（永久免费档） | `ZHIPU_API_KEY` | GLM-4-Flash 官方免费；GLM-4-Plus 等按量计费 |
| **硅基流动**（已内置） | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V4-Flash`（默认）等 | `SILICONFLOW_API_KEY` | 注册送体验额度；大量低价模型；`sr llm --use siliconflow` 一键接入 |
| **阿里云百炼** | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-turbo` / `qwen-flash` | `DASHSCOPE_API_KEY` | 新用户送额度；qwen 轻量档有免费/低价档 |
| **百度千帆** | `https://qianfan.baidubce.com/v2` | `ernie-speed-128k` | `QIANFAN_API_KEY` | 部分轻量模型免费档 + 新用户额度 |
| **火山方舟（豆包）** | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-lite-*` | `ARK_API_KEY` | 新用户送 token，lite 档极低价 |
| **阶跃星辰** | `https://api.stepfun.com/v1` | `step-1-8k` | `STEPFUN_API_KEY` | 注册送额度 |
| **腾讯混元** | `https://api.hunyuan.cloud.tencent.com/v1` | `hunyuan-lite` | `HUNYUAN_API_KEY` | 新用户送额度 |
| **讯飞星火** | `https://spark-api-open.xf-yun.com/v1` | `generalv3.5` | `SPARK_API_KEY` | 有免费体验额度 |
| **MiniMax**（已内置） | `https://api.minimaxi.com/v1` | `minimax-M3` | `MINIMAX_API_KEY` | 新用户送额度 |
| **DeepSeek**（已内置） | `https://api.deepseek.com/v1` | `deepseek-chat` | `DEEPSEEK_API_KEY` | 新用户送少量；按量极便宜 |
| **Kimi / Moonshot**（已内置） | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | `MOONSHOT_API_KEY` | 新用户送额度 |

### 需代理（免费额度通常更大）

| 服务 | base_url | 推荐模型 | key 环境变量 | 免费情况（写稿时） |
|---|---|---|---|---|
| **Google AI Studio** | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-2.5-flash-lite` / `gemini-2.5-flash` | `GEMINI_API_KEY` | 免费 tier 额度最大（~250 请求/天，限 RPM）；官方 OpenAI 兼容端点 |
| **NVIDIA build**（已内置） | `https://integrate.api.nvidia.com/v1` | `z-ai/glm-5.2` 等 | `NVIDIA_API_KEY` | 注册即送 key，额度有限且**高峰常过载（HTTP 529/挂起）** |
| **Groq** | `https://api.groq.com/openai/v1` | `llama-4-*` | `GROQ_API_KEY` | 免费 tier 长期有效，速率限制较高 |
| **Cloudflare Workers AI** | `https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1` | `@cf/meta/llama-4-*` 等 | `CLOUDFLARE_API_TOKEN` | 每日免费额度（约 10k neurons） |
| **Mistral** | `https://api.mistral.ai/v1` | `mistral-small-latest` | `MISTRAL_API_KEY` | 免费 tier 有月额度 |
| **Cerebras** | `https://api.cerebras.ai/v1` | `llama-4-*` | `CEREBRAS_API_KEY` | 免费 tier 有速率限制 |
| **OpenRouter** | `https://openrouter.ai/api/v1` | `deepseek/deepseek-chat:free` | `OPENROUTER_API_KEY` | `:free` 后缀模型免费（可能排队） |

## 接入步骤

### 场景 A：内置 registry 服务（如 DeepSeek / Kimi）

```bash
sr llm --add-key deepseek    # 交互录入 DEEPSEEK_API_KEY 到 ~/.quantnodes/.env
sr llm --use deepseek        # 自动创建 profile + 切换
sr llm --show                # 验证
```

### 场景 B：registry 外服务（如智谱 GLM-4-Flash）

1. 把密钥写入 `.env`：

```bash
echo 'ZHIPU_API_KEY=你的key' >> ~/.quantnodes/.env
chmod 600 ~/.quantnodes/.env
```

2. 手动向 `~/.quantnodes/llm.json` 的 `profiles` 加条目：

```json
"zhipu": {
  "provider": "zhipu",
  "model": "glm-4-flash",
  "api_key": "env:ZHIPU_API_KEY",
  "base_url": "https://open.bigmodel.cn/api/paas/v4",
  "model_context_tokens": 131072,
  "model_max_output_tokens": 8192
}
```

3. 切换 + 验证：

```bash
sr llm --use zhipu   # 注：registry 外的名字这里只是改 active_profile；
                     # 若报"未知 provider"，直接改 llm.json 的
                     # active_profile 字段为 "zhipu" 同样生效
sr llm --show
```

4. 快速冒烟（任一方式）：

```bash
python3 -c "
from strategy_research.core.llm import LLMConfig, OpenAICompatClient
cfg = LLMConfig.load()
c = OpenAICompatClient(cfg)
print(c.chat([{'role':'user','content':'你好，回复 OK'}]).content)
"
```

> 提示：`sr llm --use <name>` 对 registry 外的名字会报「未知 provider」；
> 这是预期行为——手动编辑 `active_profile` 即可，运行时对未知 provider
> 名回退 OpenAI 兼容适配。

### 场景 C：WebUI 可视化配置

SettingsModal → LLM 设置：provider 下拉（后端 `provider/` registry 驱动）
+ 模型 + Base URL + API Key（留空 = 保持现有密钥）。**registry 外的
自定义 provider 仍需先手动在 llm.json 加 profile**，WebUI 下拉才能看到。

## 注意事项

1. **免费额度会变**：表中政策是写稿时观察，接入前上官网确认；
   免费档经常伴随 RPM/TPM 限制，长会话或工具密集任务会撞限流。
2. **密钥只进 `.env`**：`llm.json` 用 `env:VAR` 引用；`config_audit`
   会检查明文 key、占位符、死 key（`sr llm --list` 可预览）。
3. **NVIDIA 高峰过载**：当前默认 nvidia/glm-5.2 免费 key 在高峰返回
   529/挂起；开发期建议备一个国内免费档（智谱 GLM-4-Flash）做 fallback。
4. **代理依赖**：本机若无外网，Google/Groq/Cloudflare 等无法连通
   （实测 TCP SYN 到境外 443 挂起），此类服务仅在有代理环境可用。
5. **上下文窗口填准**：`model_context_tokens` 影响自动压缩阈值；
   填大（如 128k）但模型实际窗口小会导致超窗口报错，反之浪费预算。
6. **模型名以官网为准**：各家 `z-ai/glm-5.2`、`glm-4-flash` 这类名称
   会随版本变化，404 时先查官网模型列表。

## 验证清单

- [ ] `sr llm --list` 显示新 key 已就绪（无 C1/C3 告警）
- [ ] `sr llm --show` 显示目标 profile 生效、密钥掩码
- [ ] 冒烟脚本返回模型回复
- [ ] `python3 -m pytest tests/test_llm_profiles.py tests/test_system_llm_api.py -q` 通过
