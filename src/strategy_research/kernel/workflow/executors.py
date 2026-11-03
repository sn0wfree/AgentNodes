from __future__ import annotations


class AgentLoopExecutor:
    def __init__(
        self,
        name: str,
        loop_cls: type | None = None,
        tools: list | None = None,
    ) -> None:
        self._name = name
        self._loop_cls = loop_cls
        self._tools = tools or []

    @property
    def name(self) -> str:
        return self._name

    def run(self, prompt: str, context: dict) -> dict:
        if self._loop_cls is None:
            raise NotImplementedError("AgentLoop class not configured")
        loop = self._loop_cls(tools=self._tools)
        result = loop.run(prompt)
        return {"status": "ok", "output": result}


class PythonExecutor:
    def __init__(self, name: str, func: callable) -> None:
        self._name = name
        self._func = func

    @property
    def name(self) -> str:
        return self._name

    def run(self, prompt: str, context: dict) -> dict:
        result = self._func(prompt, context)
        return {"status": "ok", "output": result}


class CLIExecutor:
    def __init__(self, name: str, command: str | None = None) -> None:
        self._name = name
        self._command = command

    @property
    def name(self) -> str:
        return self._name

    def run(self, prompt: str, context: dict) -> dict:
        import shlex
        import subprocess

        cmd = self._command or f"echo 'Agent {self._name} executed'"
        try:
            # Use shell=False with shlex.split to avoid shell injection
            result = subprocess.run(
                shlex.split(cmd),
                shell=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return {
                "status": "ok" if result.returncode == 0 else "error",
                "output": result.stdout,
                "error": result.stderr if result.returncode != 0 else "",
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": "Command timed out"}
        except FileNotFoundError:
            return {"status": "error", "error": f"Command not found: {cmd}"}


class StubExecutor:
    """Stub executor that returns a fixed result.

    ⚠️  This class is intended primarily for testing and development
    scaffolding. It does not execute real logic. For production workflows,
    use PythonExecutor, CLIExecutor, or AgentLoopExecutor.
    """

    def __init__(self, name: str, result: dict | None = None) -> None:
        self._name = name
        self._result = result or {"status": "ok"}

    @property
    def name(self) -> str:
        return self._name

    def run(self, prompt: str, context: dict) -> dict:
        return self._result
