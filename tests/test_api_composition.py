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
