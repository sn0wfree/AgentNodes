from agentnodes.kernel.workflow.agents import AgentExecutor, AgentRegistry


class DummyExecutor:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def run(self, prompt: str, context: dict) -> dict:
        return {"status": "ok", "agent": self._name}


class NotExecutor:
    pass


class TestAgentExecutor:
    def test_protocol_check_pass(self):
        executor = DummyExecutor("test")
        assert isinstance(executor, AgentExecutor)

    def test_protocol_check_fail(self):
        obj = NotExecutor()
        assert not isinstance(obj, AgentExecutor)

    def test_has_name_property(self):
        assert hasattr(AgentExecutor, "name")

    def test_has_run_method(self):
        assert hasattr(AgentExecutor, "run")


class TestAgentRegistry:
    def test_empty_registry(self):
        reg = AgentRegistry()
        assert len(reg) == 0
        assert reg.list_agents() == []

    def test_register(self):
        reg = AgentRegistry()
        executor = DummyExecutor("researcher")
        reg.register(executor)
        assert len(reg) == 1
        assert "researcher" in reg

    def test_get_executor(self):
        reg = AgentRegistry()
        executor = DummyExecutor("strategist")
        reg.register(executor)
        got = reg.get("strategist")
        assert got is executor

    def test_get_missing(self):
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None

    def test_list_agents(self):
        reg = AgentRegistry()
        reg.register(DummyExecutor("a"))
        reg.register(DummyExecutor("b"))
        reg.register(DummyExecutor("c"))
        agents = reg.list_agents()
        assert sorted(agents) == ["a", "b", "c"]

    def test_register_overwrite(self):
        reg = AgentRegistry()
        reg.register(DummyExecutor("x"))
        reg.register(DummyExecutor("x"))
        assert len(reg) == 1

    def test_contains(self):
        reg = AgentRegistry()
        reg.register(DummyExecutor("y"))
        assert "y" in reg
        assert "z" not in reg
