from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AgentExecutor(Protocol):
    @property
    def name(self) -> str: ...

    def run(self, prompt: str, context: dict) -> dict: ...


class AgentRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, AgentExecutor] = {}

    def register(self, executor: AgentExecutor) -> None:
        self._executors[executor.name] = executor

    def get(self, name: str) -> AgentExecutor | None:
        return self._executors.get(name)

    def list_agents(self) -> list[str]:
        return list(self._executors.keys())

    def __len__(self) -> int:
        return len(self._executors)

    def __contains__(self, name: str) -> bool:
        return name in self._executors
