"""ConfigHub 骨架（E1 段：gateway / mailbox 两节）。

依据决策 17（ConfigHub 配置中心）与 roadmap 1b：
- 分层装载：代码默认 < 配置文件 < 环境变量
- 启动校验 fail-fast：未知键、错误类型直接拒启
- 脱敏指纹：canonical JSON 的 sha256（本骨架两节无敏感项；
  provider/密钥节加入后必须在指纹前剔除，见 SENSITIVE_KEYS）

实现刻意零依赖（不用 pydantic）：两节共 ~10 个字段，手工类型表
比引入依赖更便宜，且 fail-fast 语义可以完全自定义。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, fields, asdict, replace
from typing import Any, Mapping


@dataclass(frozen=True)
class GatewayConfig:
    """Gateway 宿主段。workers 恒为 1（多 worker=多 Gateway，mailbox 分裂）。

    workers 在 `__post_init__` 中强制校验——**结构性钉死**，不是约定：
    任何构造路径（直接构造、文件层、env 层、replace()）传入非 1 值都会
    立即失败。ConfigHub 的文件/env 层另有前置检查给出更友好的错误信息。
    """

    workers: int = 1
    auto_resume: bool = False
    grace_seconds: float = 300.0
    no_http: bool = False

    def __post_init__(self) -> None:
        if self.workers != 1:
            raise ValueError(
                f"gateway.workers 必须为 1（得到 {self.workers}）——"
                "多 worker = 多 Gateway = mailbox/状态机分裂"
            )


@dataclass(frozen=True)
class MailboxConfig:
    """mailbox 段：cap / 溢出策略（契约 §5 不变量 9）/ 优先级老化（R2.3）。"""

    cap: int = 20
    overflow: str = "summarize"  # summarize / old / new（v1 仅 summarize 实装）
    expire_on_poll: bool = True
    aging_s: float = 15.0  # 普通项等待超过此秒数后不再被 interrupt 无限插队；0=禁用


@dataclass(frozen=True)
class PlatformConfig:
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    mailbox: MailboxConfig = field(default_factory=MailboxConfig)


# 指纹前必须剔除的键（本骨架两节暂无；provider 密钥节加入时登记于此）
SENSITIVE_KEYS: frozenset[str] = frozenset()

_SECTIONS: dict[str, type] = {"gateway": GatewayConfig, "mailbox": MailboxConfig}
_ENV_PREFIX = "QNODES_"


class ConfigError(Exception):
    """fail-fast：装载/校验失败拒启（契约：配错直接报错，不猜默认）。"""


def _coerce(value: Any, target_type: type, where: str) -> Any:
    """类型收敛。env 层的值恒为 str，按目标类型转换；转换失败 fail-fast。"""
    if isinstance(value, str):
        text = value.strip()
        if target_type is bool:
            lowered = text.lower()
            if lowered in ("true", "1", "yes", "on"):
                return True
            if lowered in ("false", "0", "no", "off"):
                return False
            raise ConfigError(f"{where}: 无法把 {value!r} 读成 bool")
        if target_type is int:
            try:
                return int(text)
            except ValueError as exc:
                raise ConfigError(f"{where}: 无法把 {value!r} 读成 int") from exc
        if target_type is float:
            try:
                return float(text)
            except ValueError as exc:
                raise ConfigError(f"{where}: 无法把 {value!r} 读成 float") from exc
    if target_type is bool:
        if isinstance(value, bool):
            return value
        raise ConfigError(f"{where}: 需要 bool，得到 {value!r}")
    if target_type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{where}: 需要 int，得到 {value!r}")
        return value
    if target_type is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{where}: 需要 float，得到 {value!r}")
        return float(value)
    if target_type is str:
        if not isinstance(value, str):
            raise ConfigError(f"{where}: 需要 str，得到 {value!r}")
        return value
    raise ConfigError(f"{where}: 不支持的类型 {target_type}")


def load_platform_config(
    *,
    file_path: str | None = None,
    env: Mapping[str, str] | None = None,
) -> PlatformConfig:
    """分层装载：默认 < 文件（yaml/json，按扩展名） < 环境变量（QNODES_<节>__<键>）。"""
    import yaml

    config = PlatformConfig()

    if file_path:
        if not os.path.exists(file_path):
            raise ConfigError(f"配置文件不存在: {file_path}")
        with open(file_path, "r", encoding="utf-8") as fh:
            if file_path.endswith((".yaml", ".yml")):
                raw = yaml.safe_load(fh) or {}
            elif file_path.endswith(".json"):
                raw = json.load(fh)
            else:
                raise ConfigError(f"不支持的配置文件格式: {file_path}")
        if not isinstance(raw, dict):
            raise ConfigError("配置文件顶层必须是映射")
        for section_name, section_cls in _SECTIONS.items():
            if section_name in raw:
                overrides = raw[section_name]
                if not isinstance(overrides, dict):
                    raise ConfigError(f"{section_name}: 节必须是映射")
                current = getattr(config, section_name)
                config = _apply_section(config, section_name, section_cls, current, overrides)

    env_map = env if env is not None else os.environ
    for section_name, section_cls in _SECTIONS.items():
        overrides: dict[str, Any] = {}
        prefix = f"{_ENV_PREFIX}{section_name.upper()}__"
        for env_key, env_val in env_map.items():
            if not env_key.startswith(prefix):
                continue
            field_name = env_key[len(prefix):].lower()
            overrides[field_name] = env_val
        if overrides:
            current = getattr(config, section_name)
            config = _apply_section(config, section_name, section_cls, current, overrides)

    return _finalize(config)


def _apply_section(
    config: PlatformConfig, section_name: str, section_cls: type,
    current: Any, overrides: Mapping[str, Any],
) -> PlatformConfig:
    known = {f.name for f in fields(section_cls)}
    for key in overrides:
        if key not in known:
            raise ConfigError(f"{section_name}: 未知配置键 {key!r}")
        if section_name == "gateway" and key == "workers":
            raise ConfigError("gateway.workers 不可配置（恒为 1）")
    coerced = {
        key: _coerce(val, type(getattr(current, key)), f"{section_name}.{key}")
        for key, val in overrides.items()
    }
    new_section = replace(current, **coerced)
    return replace(config, **{section_name: new_section})


def _finalize(config: PlatformConfig) -> PlatformConfig:
    if config.gateway.workers != 1:
        raise ConfigError("gateway.workers 恒为 1（分库与 mailbox 的单写者前提）")
    if config.mailbox.overflow not in ("summarize", "old", "new"):
        raise ConfigError(f"mailbox.overflow 非法: {config.mailbox.overflow!r}")
    if config.mailbox.cap < 1:
        raise ConfigError("mailbox.cap 必须 >= 1")
    if config.mailbox.aging_s < 0:
        raise ConfigError("mailbox.aging_s 必须 >= 0（0 = 禁用老化）")
    return config


def config_fingerprint(config: PlatformConfig) -> str:
    """脱敏指纹：canonical JSON（排序键）的 sha256。进事件流用于审计与对账。"""
    payload = asdict(config)
    for key in SENSITIVE_KEYS:
        payload.pop(key, None)
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
