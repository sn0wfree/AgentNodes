# -*- coding: utf-8 -*-
"""第三轮复审候选 S1：会话归属校验 adapter 深模块的直接行为单测。

``enforce_session_owned(request, session_id)`` 独占「user 提取 → 连接获取 →
归属校验」三步。本文件经 fake request + fake conn 直接钉住：

- 所有者取到 row、异主 403、不存在 404（透传 _fetch_session_owned）；
- user_id 统一口径：属性缺失 / 显式 None → ``"anonymous"``；
- helper 内部经 ``kernel.session.db._get_db`` 取连接（lazy import 可被 patch）。

21 个路由调用点与 9 个 E2E 鉴权测试文件保持不动；此文件是 seam 的正确
测试面（interface is the test surface）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agentnodes.interfaces.api import authz


def _request(user_id: object = ...) -> SimpleNamespace:
    """构造只带 state.user_id 的 fake request；... 表示属性不存在。"""
    state = SimpleNamespace()
    if user_id is not ...:
        state.user_id = user_id
    return SimpleNamespace(state=state)


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def execute(self, sql, params):
        return self

    def fetchone(self):
        return self._row


@pytest.fixture
def fake_db(monkeypatch):
    """把 helper lazy import 的 _get_db 换成可观测的假连接工厂。"""
    calls = []

    def _install(row):
        conn = _FakeConn(row)
        monkeypatch.setattr(
            "agentnodes.kernel.session.db._get_db", lambda: conn,
        )
        return conn

    return _install


def test_owner_gets_row(fake_db):
    row = {"id": "s1", "user_id": "alice"}
    fake_db(row)
    got = authz.enforce_session_owned(_request("alice"), "s1")
    assert got is row


def test_other_user_forbidden(fake_db):
    fake_db({"id": "s1", "user_id": "alice"})
    with pytest.raises(HTTPException) as exc:
        authz.enforce_session_owned(_request("bob"), "s1")
    assert exc.value.status_code == 403


def test_missing_session_not_found(fake_db):
    fake_db(None)
    with pytest.raises(HTTPException) as exc:
        authz.enforce_session_owned(_request("alice"), "missing")
    assert exc.value.status_code == 404


@pytest.mark.parametrize("user_id", [None, ...])
def test_missing_or_none_user_falls_back_to_anonymous(fake_db, user_id):
    """Q6 统一口径：属性缺失与显式 None 都回落 anonymous（与 auth 一致）。"""
    fake_db({"id": "s1", "user_id": "anonymous"})
    got = authz.enforce_session_owned(_request(user_id), "s1")
    assert got["user_id"] == "anonymous"
