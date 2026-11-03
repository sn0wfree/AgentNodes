"""AgentPlugin — the single source of truth for agent definitions.

One frozen dataclass describes everything needed to execute an agent:
identity, prompt, tool whitelist, DAG dependencies, executor type and
default execution knobs. Both the study system and the workflow/
orchestration system consume plugins from the same registry
(docs/unified-agent-engine-design.md).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentPlugin:
    """Complete definition of one agent (building block)."""

    id: str
    name: str
    category: str            # research | execution | evaluation | tool
    description: str
    prompt_file: str = ""    # e.g. ".prompts/researcher.md"; "" for non-LLM
    tools: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()   # hard upstream deps (ids)
    provides: str = ""               # output key, e.g. "researcher_output"
    executor_type: str = "llm"       # llm | python | evaluator
    python_function: str | None = None
    default_timeout: int = 180
    default_max_iterations: int = 8
    default_max_retries: int = 3
    optional: bool = True
    keywords: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "prompt_file": self.prompt_file,
            "tools": list(self.tools),
            "requires": list(self.requires),
            "provides": self.provides,
            "executor_type": self.executor_type,
            "python_function": self.python_function,
            "default_timeout": self.default_timeout,
            "default_max_iterations": self.default_max_iterations,
            "default_max_retries": self.default_max_retries,
            "optional": self.optional,
            "keywords": list(self.keywords),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentPlugin":
        return cls(
            id=str(d["id"]),
            name=str(d.get("name", d["id"])),
            category=str(d.get("category", "execution")),
            description=str(d.get("description", "")),
            prompt_file=str(d.get("prompt_file", "")),
            tools=tuple(d.get("tools") or ()),
            requires=tuple(d.get("requires") or ()),
            provides=str(d.get("provides", "")),
            executor_type=str(d.get("executor_type", "llm")),
            python_function=d.get("python_function"),
            default_timeout=int(d.get("default_timeout", 180)),
            default_max_iterations=int(d.get("default_max_iterations", 8)),
            default_max_retries=int(d.get("default_max_retries", 3)),
            optional=bool(d.get("optional", True)),
            keywords=tuple(d.get("keywords") or ()),
        )


__all__ = ["AgentPlugin"]
