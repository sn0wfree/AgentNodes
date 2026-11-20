# -*- coding: utf-8 -*-
"""M12 契约：组合根的资源图定位与生命周期规则单一化。

钉住：① build_resources 走现有生产 wiring（同 AppContainer 字段）；
② register/unregister 按实例幂等；③ close_resources 幂等且不误伤
同 db_path 的新实例（测试间重建场景）；④ close_all 统一关闭已登记实例。
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from agentnodes.interfaces.api import composition


def test_build_resources_uses_existing_wiring():
    with TemporaryDirectory() as tmpdir:
        resources = composition.build_resources(db_path=Path(tmpdir) / "s.db")
        fields = ("projector", "session_store", "event_store",
                  "memory_manager", "session_service")
        for name in fields:
            assert getattr(resources, name, None) is not None
        composition.close_resources(resources)


def test_register_is_idempotent_and_close_is_per_instance():
    with TemporaryDirectory() as tmpdir:
        db = Path(tmpdir) / "s.db"
        r1 = composition.build_resources(db_path=db)
        composition.register(r1)
        composition.register(r1)  # 幂等
        composition.close_resources(r1)
        composition.close_resources(r1)  # 幂等

        # 同 db_path 重建的新实例：不应被 r1 的关闭状态误伤——
        # 用真实 close（不 monkey-patch），若被误判为已关闭则 TemporaryDirectory
        # 清理时会抛 WinError 32（本测试的设计判据）。
        r2 = composition.build_resources(db_path=db)
        composition.register(r2)
        composition.close_resources(r2)
        composition.unregister(r1)
        composition.unregister(r2)


def test_resources_from_request_prefers_container():
    from types import SimpleNamespace

    class _State:
        _container = "the-container"

    request = SimpleNamespace(app=SimpleNamespace(state=_State()))
    assert composition.resources_from_request(request) == "the-container"


class TestPermissionGatewaySingleton:
    """C3：PermissionGateway 单例归 composition 的契约。"""

    def test_singleton_identity(self, monkeypatch):
        """同一进程重复取用 → 同一实例（cache dict "default"）。"""
        monkeypatch.setitem(composition._permission_gateway_cache, "default", None)
        composition._permission_gateway_cache.pop("default", None)
        try:
            g1 = composition.get_permission_gateway()
            g2 = composition.get_permission_gateway()
            assert g1 is g2
        finally:
            composition._permission_gateway_cache.pop("default", None)

    def test_gateway_wires_sse_hook(self, monkeypatch):
        """on_request 钩子已接线（进程级 SSE 推送），且无 session 时不推。"""
        monkeypatch.setitem(composition._permission_gateway_cache, "default", None)
        composition._permission_gateway_cache.pop("default", None)
        try:
            gw = composition.get_permission_gateway()
            assert gw is not None
            # 无 session_id 的请求被丢弃（原 router 行为逐字保留）
            pushes = []
            monkeypatch.setattr(composition, "_push_event",
                                lambda sid, et, data: pushes.append((sid, et)))
            hook = None
            # 钩子闭包在 gateway 内部——经 on_request 触发验证
            gw_on_request = getattr(gw, "_on_request", None)
            if gw_on_request is None:
                return  # gateway 封装不暴露钩子时跳过（行为由 e2e 覆盖）
            hook = gw_on_request
            hook("tc-1", type("D", (), {"pattern": "p", "target": "t"})(), {})
            assert pushes == []
        finally:
            composition._permission_gateway_cache.pop("default", None)

    def test_container_and_fallback_paths_equivalent(self, monkeypatch, tmp_path):
        """两构造路径同构：container 路径与 fallback SessionService 均无
        permission_gateway（None，ASK fail-closed）——C3 统一后的契约。"""
        from agentnodes.interfaces.api.container import build_container
        from agentnodes.kernel.session.service import SessionService

        with TemporaryDirectory() as tmpdir:
            container = build_container(db_path=Path(tmpdir) / "a.db")
            try:
                assert container.session_service.permission_gateway is None
            finally:
                composition.close_resources(container)

        # fallback（dependencies.get_session_service 的裸构造形态）
        monkeypatch.setenv("AGENTNODES_SESSION_DB", str(Path(tmpdir) / "b.db"))
        from agentnodes.kernel.session.store import SessionStore
        from agentnodes.kernel.session.state.event_store import EventStore

        store = SessionStore(Path(tmpdir) / "b.db")
        bus = EventStore(Path(tmpdir) / "b.db")
        svc = SessionService(store=store, event_bus=bus)
        assert svc.permission_gateway is None
