"""LLM 配置检测：检查 ~/.quantnodes/ 是否存在并已配置。

检测三件事：
1. `~/.quantnodes/llm.json` 是否存在且包含有效 LLM 段
2. `~/.quantnodes/.env` 是否存在且包含 LLM_API_KEY
3. `OPENAI_API_KEY` 环境变量是否设置

返回值：
- bool: 已配置完整（任意一个 layer 有 api_key + provider/model）
- dict: 详细状态信息（供安装脚本展示）
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict

# QuantNodes 标准配置位置（与 onboard.py 一致）
QUANTNODES_DIR = Path.home() / ".quantnodes"
LLM_JSON_PATH = QUANTNODES_DIR / "llm.json"
DOTENV_PATH = QUANTNODES_DIR / ".env"


class LLMConfigStatus(TypedDict):
    """LLM 配置状态。"""
    configured: bool         # 已配置完整（可启动 LLM 调用）
    quantnodes_dir_exists: bool
    llm_json_exists: bool
    llm_json_has_llm_section: bool
    dotenv_exists: bool
    dotenv_has_api_key: bool
    env_var_set: bool        # OPENAI_API_KEY 或 LLM_API_KEY
    api_key_source: str      # "env" | "llm.json" | "dotenv" | "none"
    model: str               # 解析到的模型名（可能为空）
    provider: str            # 解析到的 provider（可能为空）
    model_context_tokens: int | None = None     # 用户覆盖（settings modal 用）
    model_max_output_tokens: int | None = None  # 同上


def _read_llm_json(path: Path) -> dict:
    """读取 llm.json，失败返回空 dict。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _read_dotenv_api_key(path: Path) -> str:
    """从 .env 文件提取 LLM_API_KEY 或 OPENAI_API_KEY。"""
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key in ("LLM_API_KEY", "OPENAI_API_KEY"):
                    return value
    except OSError:
        pass
    return ""


def _profile_effective(
    llm_section: dict,
    provider: str,
    model: str,
    effective_api_key: str,
    api_key_source: str,
) -> tuple[str, str, str, str]:
    """Resolve provider/model through the profile layer.

    When llm.json uses the ``profiles``/``active_profile`` structure the
    top-level provider/model may be absent; the effective values come
    from ``LLMConfig.load()``. Falls back to the given top-level values
    on any error.
    """
    try:
        from strategy_research.foundation.llm.config import LLMConfig

        cfg = LLMConfig.load()
        resolved_provider = cfg.provider or ""
        resolved_model = cfg.model or ""
        if resolved_provider not in ("", "auto") and resolved_model not in ("", "unknown"):
            provider = resolved_provider
            model = resolved_model
            if not effective_api_key:
                effective_api_key = cfg.api_key or ""
                if effective_api_key and api_key_source == "none":
                    api_key_source = "config"
    except Exception:
        pass
    return provider, model, effective_api_key, api_key_source


def check_llm_config() -> LLMConfigStatus:
    """检测 ~/.quantnodes/ 配置状态。

    Returns:
        LLMConfigStatus: 包含所有检测字段。``configured=True`` 表示
        至少有 provider + model + api_key 三件套可用。
    """
    # 1. 文件存在性
    quantnodes_dir_exists = QUANTNODES_DIR.exists()
    llm_json_exists = LLM_JSON_PATH.exists()
    dotenv_exists = DOTENV_PATH.exists()

    # 2. llm.json 解析
    llm_section: dict = {}
    if llm_json_exists:
        data = _read_llm_json(LLM_JSON_PATH)
        section = data.get("llm")
        if isinstance(section, dict):
            llm_section = section

    llm_json_has_llm_section = bool(llm_section)

    # 3. api_key 来源探测（优先级：env var > .env > llm.json）
    env_var_value = (
        os.environ.get("OPENAI_API_KEY", "")
        or os.environ.get("LLM_API_KEY", "")
    )
    env_var_set = bool(env_var_value)

    dotenv_api_key = _read_dotenv_api_key(DOTENV_PATH) if dotenv_exists else ""
    dotenv_has_api_key = bool(dotenv_api_key)

    llm_json_api_key = llm_section.get("api_key", "") if llm_json_has_llm_section else ""
    # llm.json 中的 api_key 可能是 "env:LLM_API_KEY" 引用（wizard 写的形式）
    if isinstance(llm_json_api_key, str) and llm_json_api_key.startswith("env:"):
        llm_json_api_key = ""  # 需要 .env 配合才能用

    # 4. 优先级解析
    if env_var_set:
        api_key_source = "env"
        effective_api_key = env_var_value
    elif dotenv_has_api_key:
        api_key_source = "dotenv"
        effective_api_key = dotenv_api_key
    elif llm_json_api_key:
        api_key_source = "llm.json"
        effective_api_key = llm_json_api_key
    else:
        api_key_source = "none"
        effective_api_key = ""

    # 5. provider / model（profile 感知：生效配置优先于顶层字段）
    provider = llm_section.get("provider", "") if llm_section else ""
    model = llm_section.get("model", "") if llm_section else ""
    provider, model, effective_api_key, api_key_source = _profile_effective(
        llm_section, provider, model, effective_api_key, api_key_source,
    )

    # 6. 综合判断
    configured = bool(provider and model and effective_api_key)

    return LLMConfigStatus(
        configured=configured,
        quantnodes_dir_exists=quantnodes_dir_exists,
        llm_json_exists=llm_json_exists,
        llm_json_has_llm_section=llm_json_has_llm_section,
        dotenv_exists=dotenv_exists,
        dotenv_has_api_key=dotenv_has_api_key,
        env_var_set=env_var_set,
        api_key_source=api_key_source,
        model=model or "",
        provider=provider or "",
    )


def get_install_message(status: LLMConfigStatus) -> str:
    """生成安装时的 LLM 配置状态消息（多行）。"""
    lines = []

    if status["configured"]:
        lines.append(f"✓ 已检测到 LLM 配置：{status['provider']} / {status['model']}")
        lines.append(f"  api_key 来源：{status['api_key_source']}")
        lines.append("  → 跳过 LLM 设置步骤")
        return "\n".join(lines)

    # 未配置：提示原因
    if not status["quantnodes_dir_exists"]:
        lines.append("✗ ~/.quantnodes/ 目录不存在")
        lines.append("  → 需要运行 init 配置 LLM")
    elif not status["llm_json_has_llm_section"]:
        lines.append("✗ ~/.quantnodes/llm.json 缺少 [llm] 段")
        lines.append("  → 需要运行 init 配置 LLM")
    elif not status["env_var_set"] and not status["dotenv_has_api_key"]:
        lines.append("✗ 未找到 API key")
        lines.append("  1. 设置环境变量：export OPENAI_API_KEY=...")
        lines.append("  2. 或运行：quantnodes-research init")
    return "\n".join(lines)


if __name__ == "__main__":
    # CLI 测试入口
    import json as _json
    print(_json.dumps(check_llm_config(), indent=2, ensure_ascii=False))
