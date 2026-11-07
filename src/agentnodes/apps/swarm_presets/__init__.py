"""Swarm presets（L2 业务内容）——多 agent 编排拓扑的 YAML 集合。

**批次 8（1-C）迁出 kernel**：这 37 个 YAML 是量化域的编排拓扑
（committee / trading desk / research team ...），是**业务内容**不是
平台机制。kernel 只保留 loader 机制（``kernel/swarm/preset_loader.py``）
与 schema（``SwarmPreset``），不持有任何具体 preset。

**引用方式**：消费者用本模块的 ``PRESETS_DIR``（apps → 自身，无跨层）；
历史上各处 ``Path(swarm.__file__).parent / "presets"`` 的写法随迁移废止。
"""
from __future__ import annotations

from pathlib import Path

#: 预设目录（本包根）。
PRESETS_DIR = Path(__file__).resolve().parent


def preset_path(name: str) -> Path:
    """按名（含或不含 .yaml 后缀）返回 preset 文件路径。"""
    if name.endswith(".yaml"):
        return PRESETS_DIR / name
    return PRESETS_DIR / f"{name}.yaml"


__all__ = ["PRESETS_DIR", "preset_path"]
