"""组合工具 (paradigm v2): 声明式配置 → 运行时实例化 → 注册。

组合工具是"工具调工具"的实现: 配置描述线性 steps + 参数映射符号,
执行时经 registry 调用子工具, 正常路径中间结果不进上下文 (上下文压缩),
报错完整透传 + 失败步骤定位。

边界:
- 只支持线性 steps (配置不滑向 DSL); 复杂逻辑改手写工具
- 嵌套深度 = 1 (组合工具不能被其他组合工具引用)
- 副作用 = 子工具 effects 并集
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .tools import BaseTool, ToolContext, ToolRegistry

logger = logging.getLogger(__name__)


# ── 参数映射符号 ────────────────────────────────────────────────────
# - "input.<path>"      组合工具输入参数 (点路径, 如 input.codes)
# - "step<N>.result.<p>" 前一步 JSON 结果的字段路径 (如 step1.result.code)
# - 其他                 字面量 (str/int/bool/list/dict 原样)

_REF_PREFIXES = ("input.", "step")


def _resolve_param(value: Any, inputs: dict, results: List[dict]) -> Any:
    """把映射符号解析为实际值。"""
    if isinstance(value, str):
        if value.startswith("input."):
            return _dig(inputs, value[len("input."):], value)
        if value.startswith("step"):
            parts = value.split(".", 2)
            if len(parts) == 3 and parts[0].startswith("step"):
                idx = int(parts[0][4:]) - 1  # step1 → 0
                if 0 <= idx < len(results):
                    return _dig(results[idx], parts[2], value)
        return value
    if isinstance(value, list):
        return [_resolve_param(v, inputs, results) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_param(v, inputs, results) for k, v in value.items()}
    return value


def _dig(mapping: dict, path: str, raw: Any) -> Any:
    node: Any = mapping
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"missing reference '{raw}'")
        node = node[part]
    return node


# ── 组合工具 ─────────────────────────────────────────────────────────


class CompositeTool(BaseTool):
    """由声明式配置实例化的组合工具。

    对 LLM 是一个普通原子工具 (一个 name + 一份说明书);
    内部按 steps 依次调用子工具 (registry 工具级调用)。
    """

    repeatable = True

    def __init__(
        self,
        *,
        name: str,
        description: str,
        category: str,
        steps: List[dict],
        registry: ToolRegistry,
        with_summary: bool = False,
    ) -> None:
        super().__init__()
        self.name = name
        self.description = description
        self.category = category
        self._steps = steps
        self._registry = registry
        self.with_summary = with_summary
        # 输入参数: 从 steps 收集 input.* 引用, 类型从子工具 schema 推断
        self._input_params: Dict[str, dict] = {}
        for step in steps:
            tool_name = step.get("tool")
            tool = registry.get(tool_name) if tool_name else None
            schema = None
            if tool is not None:
                try:
                    schema = tool.to_openai_schema()["function"]["parameters"]
                except Exception:
                    schema = None
            for pname, ref in (step.get("params") or {}).items():
                if isinstance(ref, str) and ref.startswith("input."):
                    key = ref[len("input."):]
                    if key not in self._input_params:
                        ptype = self._infer_type(schema, pname) if schema else "string"
                        self._input_params[key] = {"type": ptype}
        # 副作用并集
        effects: set = set()
        for step in steps:
            tool = registry.get(step.get("tool", ""))
            if tool is not None:
                effects |= set(getattr(tool, "effects", frozenset()))
        self.effects = frozenset(effects)
        # parameters (schema): 组合输入
        self.parameters = {
            "type": "object",
            "properties": self._input_params,
            "required": list(self._input_params.keys()),
        }
        # 动态说明书 (tool_help / brief 使用实例 __doc__)
        self.__doc__ = self._build_spec_doc()

    @staticmethod
    def _infer_type(schema: Optional[dict], param_name: str) -> str:
        if schema:
            prop = (schema.get("properties") or {}).get(param_name)
            if isinstance(prop, dict) and prop.get("type"):
                return prop["type"]
        return "string"

    # ── 说明书 (详细版) ─────────────────────────────────────────

    def _build_spec_doc(self) -> str:
        lines = [
            f"{self.description}",
            "",
            "# ── 工具说明书 (组合工具) ────────────────",
            "# 版本: 1.0.0",
            "# 变更: 由组合配置生成 (workspace tools/combo/)",
            "",
            "## 用途",
            f"# {self.description}",
            "# 组合步骤: " + " → ".join(s.get("tool", "?") for s in self._steps),
            "",
            "## 参数",
        ]
        for key in self._input_params:
            lines.append(f"# - {key}: 组合输入 ({self._input_params[key]['type']})")
        lines += [
            "",
            "## 示例",
            "# 按步骤工具的说明书组合调用",
            "",
            "## 边界",
            "# 组合工具 = 子工具调用序列; 正常路径中间结果不进上下文;",
            "# 报错时完整透传子工具错误并标注失败步骤。",
            "",
            "## 错误处理范式",
            "# - 子工具失败 → 完整错误 + combo_step/combo_tool 定位",
            "# - 可安全重试 (子工具各自幂等语义)",
            "",
            "## 相关工具",
            "# " + ", ".join(dict.fromkeys(s.get("tool", "?") for s in self._steps)),
            "# ─────────────────────────────────────────────",
        ]
        return "\n".join(lines)

    # ── 执行 ────────────────────────────────────────────────────

    def execute(self, ctx: ToolContext, **inputs: Any) -> str:
        results: List[dict] = []
        for i, step in enumerate(self._steps):
            tool_name = step.get("tool", "")
            tool = self._registry.get(tool_name)
            if tool is None:
                return json.dumps({
                    "status": "error",
                    "error": f"combo step {i + 1}: tool '{tool_name}' not registered",
                    "combo": self.name,
                    "combo_step": i + 1,
                    "combo_tool": tool_name,
                }, ensure_ascii=False)
            try:
                params = {
                    k: _resolve_param(v, inputs, results)
                    for k, v in (step.get("params") or {}).items()
                }
            except KeyError as exc:
                return json.dumps({
                    "status": "error",
                    "error": f"combo step {i + 1} ({tool_name}): {exc}",
                    "combo": self.name,
                    "combo_step": i + 1,
                    "combo_tool": tool_name,
                    "fix": f"check the mapping for param referencing missing input; combo inputs: {sorted(inputs)}",
                }, ensure_ascii=False)
            out = tool.invoke({**params, "ctx": ctx})
            try:
                payload = json.loads(out)
            except (TypeError, json.JSONDecodeError):
                return json.dumps({
                    "status": "error",
                    "error": f"combo step {i + 1} ({tool_name}): non-JSON tool output",
                    "combo": self.name,
                    "combo_step": i + 1,
                    "combo_tool": tool_name,
                }, ensure_ascii=False)
            if payload.get("status") == "error":
                # 报错完整透传 + 失败定位 (不截断)
                payload["combo"] = self.name
                payload["combo_step"] = i + 1
                payload["combo_tool"] = tool_name
                return json.dumps(payload, ensure_ascii=False, default=str)
            results.append(payload)

        # 正常路径: 中间结果不进上下文; 默认只返回最后一步关键输出
        final = dict(results[-1])
        if self.with_summary:
            final["combo_summary"] = [
                {"step": i + 1, "tool": s.get("tool")}
                for i, s in enumerate(self._steps)
            ]
        return json.dumps(final, ensure_ascii=False, default=str)


# ── 组合库加载器 ─────────────────────────────────────────────────────


class ComboConfigError(ValueError):
    """组合配置非法。"""


def _validate_config(cfg: dict, path: Path) -> None:
    name = cfg.get("name")
    if not isinstance(name, str) or not name:
        raise ComboConfigError(f"{path}: missing 'name'")
    steps = cfg.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ComboConfigError(f"{path}: missing/empty 'steps'")
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get("tool"), str):
            raise ComboConfigError(f"{path}: step {i + 1} missing 'tool'")
        if not isinstance(step.get("params"), dict):
            raise ComboConfigError(f"{path}: step {i + 1} missing 'params'")


def load_combo_tools(workspace: Path, registry: ToolRegistry) -> int:
    """扫描 workspace/tools/combo/*.yml, 实例化组合工具并注册。

    组合深度 = 1: steps 中出现组合工具名会被拒绝。
    """
    combo_dir = workspace / "tools" / "combo"
    if not combo_dir.is_dir():
        return 0
    import yaml

    registered = 0
    for f in sorted(combo_dir.glob("*.yml")):
        try:
            cfg = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            _validate_config(cfg, f)
            # 嵌套深度 = 1: 拒绝引用其他组合工具
            for step in cfg["steps"]:
                sub = registry.get(step["tool"])
                if sub is not None and isinstance(sub, CompositeTool):
                    raise ComboConfigError(
                        f"{f}: step references composite tool '{step['tool']}' "
                        "(nesting depth limited to 1)"
                    )
            tool = CompositeTool(
                name=cfg["name"],
                description=cfg.get("description") or cfg["name"],
                category=cfg.get("category", "组合"),
                steps=cfg["steps"],
                registry=registry,
                with_summary=bool(cfg.get("with_summary", False)),
            )
            registry.register(tool)
            registered += 1
        except (ComboConfigError, OSError, yaml.YAMLError) as exc:
            logger.warning("combo load skipped %s: %s", f, exc)
            continue
    return registered
