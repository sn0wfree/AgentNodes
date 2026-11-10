"""Real LLM E2E tests — hits actual minimax API via subprocess server.

Requires: ~/.quantnodes/.env with valid LLM_API_KEY.
Does NOT set AGENTNODES_TEST_CHAT.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent

# **必须打 live_llm 标记**（2026-11-10 修）：conftest 的 ``_purge_llm_env``
# autouse fixture 会清掉所有 *_API_KEY 并禁用 ``_try_load_dotenv``——本模块
# 需要真实 key（api_key 是 ``env:MINIMAX_API_KEY`` 引用），被清后子进程读到
# 空串，报 "API key not configured"。
#
# **xdist_group**：本模块起真实 uvicorn 子进程 + 真实 LLM 调用，并行跑时
# 多个 worker 抢端口/临时 HOME 会互相污染（实测 -n 8 下 2 个失败、单跑
# 5/5 全过）。loadgroup 分布器会把同组测试固定在同一个 worker 上串行执行。
pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.xdist_group("chat_real_llm"),
]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=1)
            if r.status_code < 500:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Server at {url} not ready within {timeout}s")


@pytest.fixture(scope="session")
def real_server():
    """Start backend WITHOUT TEST_MODE — real LLM calls.

    Uses the same pattern as conftest_e2e.py but without TEST_MODE.
    """
    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env.pop("AGENTNODES_TEST_CHAT", None)
    # The conftest _isolate_llm_bridge autouse fixture points
    # AGENTNODES_LLM_CONFIG at a stubbed (disabled) llm.json.
    # Real-LLM tests must use the host's actual llm.json instead, so the
    # subprocess cannot inherit that env var.
    env.pop("AGENTNODES_LLM_CONFIG", None)

    # **用户库隔离（2026-11-10）**：不设隔离时子进程用宿主真实的
    # ``~/.agentnodes/quantnodes_agentnodes_user.db``——与同一机器上正在
    # 运行的服务/其他测试共享 admin 账号，密码随时可能被改写，导致
    # ``Invalid credentials`` 随机失败（实测：单跑 3 passed、五套件合跑
    # 4 个 error）。这里给一个临时 HOME，让 seed_admin_if_empty 自己生成
    # 随机密码并落盘到该目录，测试再从同一个文件读回。
    import tempfile

    home = Path(tempfile.mkdtemp(prefix="real_llm_home_"))
    env["USERPROFILE"] = str(home)
    env["HOME"] = str(home)
    (home / ".agentnodes").mkdir(parents=True, exist_ok=True)

    env.update({
        "STATIC_DIR": str(REPO_ROOT / "webui" / "static"),
        "CORS_ORIGINS": "*",
        "PYTHONUNBUFFERED": "1",
        "AN_WORKSPACE_PATH": str(REPO_ROOT),
        # 密码交给被测进程生成后写到临时 HOME；下面的 _admin_password
        # 会先查 AN_ADMIN_PASSWORD，所以我们直接钉死一个已知值更稳。
        "AN_ADMIN_PASSWORD": "real-llm-test-pw",
    })

    # 把宿主 .env **整份**注入子进程 env。
    #
    # 两个必须点（2026-11-10 实测）：
    # 1. **整份注入**而非挑几个变量名：llm.json 的 api_key 是
    #    ``env:MINIMAX_API_KEY`` 引用，配置层会 ``os.environ.get(...)``；
    #    只注入 LLM_API_KEY 时引用解析成空串 → "API key not configured"。
    # 2. **用 REAL_HOME** 而非 ``Path.home()``：conftest 的 ``_home_sandbox``
    #    已把 HOME/USERPROFILE 指向沙箱，``Path.home()`` 拿不到宿主目录。
    try:
        from dotenv import dotenv_values

        # 取 ``_home_sandbox`` 的 REAL_HOME。
        #
        # **不要 ``from tests.conftest import _home_sandbox``**：pytest 在
        # rootdir 模式下不一定把 tests/ 注册成包，xdist worker 里那条
        # import 可能失败（异常被下面的 except 吞掉 → key 没注入 →
        # "API key not configured"）。
        # 从 sys.modules 里找已加载的 sandbox 模块最稳；找不到再退回
        # ``_home_sandbox`` 直接导入（rootdir 会把 tests/ 放进 sys.path）。
        real_home = None
        for mod_name in ("conftest", "_home_sandbox"):
            mod = sys.modules.get(mod_name)
            sandbox = getattr(mod, "_home_sandbox", None) or (
                mod if getattr(mod, "REAL_HOME", None) else None
            )
            if sandbox is not None:
                real_home = sandbox.REAL_HOME
                break
        if real_home is None:
            import _home_sandbox  # type: ignore[import-not-found]

            real_home = _home_sandbox.REAL_HOME
        _loaded = []
        for cand in (real_home / ".agentnodes" / ".env",
                     real_home / ".quantnodes" / ".env"):
            if cand.exists():
                for k, v in dotenv_values(cand).items():
                    if v:
                        env[k] = v
                _loaded.append(f"env={cand}")
                break
        # 子进程的 HOME 被指到临时目录，找不到宿主 llm.json —— 显式指定。
        for lj in (real_home / ".agentnodes" / "llm.json",
                   real_home / ".quantnodes" / "llm.json"):
            if lj.exists():
                env["AGENTNODES_LLM_CONFIG"] = str(lj)
                _loaded.append(f"llm={lj}")
                break
        # 缺凭据时**直接失败**（fail fast）。此前是静默 pass，导致真实原因
        # 报在远端（"API key not configured"），排查要跨两个进程。
        if not env.get("MINIMAX_API_KEY") and not env.get("LLM_API_KEY"):
            raise RuntimeError(
                f"no API key injected: REAL_HOME={real_home} "
                f"(exists={real_home.is_dir()}), loaded={_loaded}, "
                f"env_keys={sorted(k for k in env if k.endswith('_API_KEY'))}"
            )
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 缺 key 时让测试报"api_key 未配置"
        raise RuntimeError(f"creds setup failed: {exc!r}") from exc

    cmd = [
        sys.executable, "-u", "-m", "uvicorn",
        "agentnodes.interfaces.api.app:create_app",
        "--factory",
        "--host", "127.0.0.1", "--port", str(port),
        "--log-level", "warning",
    ]
    log_file = open(Path(tempfile.gettempdir()) / "real_llm_server.log", "wb")
    proc = subprocess.Popen(
        cmd, cwd=str(REPO_ROOT), env=env,
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_http(f"{base_url}/health")
        yield {"base_url": base_url, "proc": proc}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        log_file.close()


#: real_server fixture 钉死的 admin 密码（同时通过 env 传给子进程，
#: 避免依赖宿主 ``admin_password.txt`` 或被其他测试/服务改写）。
_ADMIN_PW = "real-llm-test-pw"


def _admin_password() -> str:
    """返回本测试钉死的 admin 密码（SEC-4 之后 seed 密码不再是常量）。"""
    return _ADMIN_PW


@pytest.fixture
def auth_info(real_server):
    """Login and return (base_url, headers, session_id, token)."""
    base_url = real_server["base_url"]
    r = requests.post(
        f"{base_url}/api/auth/login",
        json={"username": "admin", "password": _admin_password()},
    )
    assert r.status_code == 200, f"Login failed: {r.text}"
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.post(f"{base_url}/api/chat/session", headers=headers, json={"title": "Real LLM Test"})
    session_id = r.json()["id"]
    return base_url, headers, session_id, token


def _read_sse_until_done(base_url: str, session_id: str, token: str, timeout: float = 90.0) -> tuple[str, str | None]:
    """Read SSE stream, return (full_text, error_or_None).

    Opens SSE connection FIRST, then waits for events. The caller should
    have already sent the message via send_async before calling this.
    """
    url = f"{base_url}/api/chat/events?session_id={session_id}&token={token}"
    full_text = ""
    error_msg = None
    evt_type = ""
    data_lines = []

    def _flush():
        nonlocal evt_type, data_lines, full_text, error_msg
        if data_lines:
            data_str = "\n".join(data_lines)
            try:
                data = json.loads(data_str)
            except Exception:
                data = {}
            if evt_type == "text_delta":
                full_text += data.get("text", "")
            elif evt_type in ("error", "attempt.failed"):
                error_msg = data.get("error") or data.get("status") or "unknown error"
            elif evt_type == "agent_done":
                data_lines = []
                return True
        data_lines = []
        evt_type = ""
        return False

    with requests.get(url, stream=True, timeout=timeout) as resp:
        for line in resp.iter_lines(decode_unicode=True):
            if line is None:
                continue
            if line == "":
                if _flush():
                    break
                continue
            if line.startswith("event: "):
                evt_type = line[7:].strip()
            elif line.startswith("data: "):
                data_lines.append(line[6:])

    _flush()
    return full_text, error_msg


class TestRealLLMChat:
    """Real LLM API tests."""

    def test_send_and_get_real_response(self, auth_info):
        """Send 'say ok' → receive real LLM response."""
        base_url, headers, sid, token = auth_info

        # Open SSE connection FIRST
        sse_url = f"{base_url}/api/chat/events?session_id={sid}&token={token}"
        full_text = ""
        error_msg = None
        done = threading.Event()

        def listen_sse():
            nonlocal full_text, error_msg
            evt_type = ""
            data_lines = []
            try:
                with requests.get(sse_url, stream=True, timeout=120) as resp:
                    for line in resp.iter_lines(decode_unicode=True):
                        if line is None:
                            continue
                        if line == "":
                            if data_lines:
                                data_str = "\n".join(data_lines)
                                try:
                                    data = json.loads(data_str)
                                except Exception:
                                    data = {}
                                if evt_type == "text_delta":
                                    full_text += data.get("text", "")
                                elif evt_type == "error":
                                    error_msg = (
                                        data.get("error")
                                        or data.get("message")
                                        or json.dumps(data, ensure_ascii=False)[:300]
                                        or "unknown"
                                    )
                                elif evt_type == "agent_done":
                                    done.set()
                                    return
                            data_lines = []
                            evt_type = ""
                            continue
                        if line.startswith("event: "):
                            evt_type = line[7:].strip()
                        elif line.startswith("data: "):
                            data_lines.append(line[6:])
            except Exception as e:
                error_msg = f"SSE error: {e}"
                done.set()

        t = threading.Thread(target=listen_sse, daemon=True)
        t.start()
        time.sleep(0.5)  # Let SSE register

        # NOW send the message
        r = requests.post(f"{base_url}/api/chat/send_async", headers=headers, json={
            "session_id": sid, "content": "say ok",
        })
        assert r.status_code == 200

        # Wait for agent_done
        done.wait(timeout=120)

        if error_msg and not full_text:
            # **不用 skip 掩盖**：运行了 live LLM 却拿不到任何输出，就是
            # 真失败（key 无效 / 配额耗尽 / provider 故障）。skip 会让 CI
            # 显示绿色，掩盖这类故障。
            pytest.fail(f"LLM call failed: {error_msg}")

        # 提示词是 "say ok"，真实回答往往就是 "ok"（2 字符）——旧阈值
        # ``> 5`` 会把正常回答判成失败（2026-11-10 实测：后端日志显示
        # 回答正确、只是太短）。这里断言"有非空回答"即可。
        assert full_text.strip(), f"Response empty: {full_text!r}"

    def test_multi_turn_history(self, auth_info):
        """Two messages → second references first."""
        base_url, headers, sid, token = auth_info

        def _send_and_read(content: str) -> tuple[str, str | None]:
            """Open SSE, send message, read until done."""
            sse_url = f"{base_url}/api/chat/events?session_id={sid}&token={token}"
            result_text = ""
            result_err = None
            done = threading.Event()

            def listen():
                nonlocal result_text, result_err
                evt_type = ""
                data_lines = []
                with requests.get(sse_url, stream=True, timeout=120) as resp:
                    for line in resp.iter_lines(decode_unicode=True):
                        if line is None:
                            continue
                        if line == "":
                            if data_lines:
                                data_str = "\n".join(data_lines)
                                try:
                                    data = json.loads(data_str)
                                except Exception:
                                    data = {}
                                if evt_type == "text_delta":
                                    result_text += data.get("text", "")
                                elif evt_type == "error":
                                    result_err = data.get("error", "unknown")
                                elif evt_type == "agent_done":
                                    done.set()
                                    return
                            data_lines = []
                            evt_type = ""
                            continue
                        if line.startswith("event: "):
                            evt_type = line[7:].strip()
                        elif line.startswith("data: "):
                            data_lines.append(line[6:])

            t = threading.Thread(target=listen, daemon=True)
            t.start()
            time.sleep(0.5)

            r = requests.post(f"{base_url}/api/chat/send_async", headers=headers, json={
                "session_id": sid, "content": content,
            })
            assert r.status_code == 200

            done.wait(timeout=120)
            return result_text, result_err

        # Message 1
        text1, err1 = _send_and_read("记住这个数字: 42")
        if err1 and not text1:
            pytest.fail(f"First message failed: {err1}")  # 不用 skip 掩盖
        assert len(text1) > 0, "First message got no response"

        # Message 2
        text2, err2 = _send_and_read("我刚才让你记住的数字是什么？只回答数字")
        if err2 and not text2:
            pytest.fail(f"Second message failed: {err2}")  # 不用 skip 掩盖

        assert "42" in text2, f"LLM didn't remember. Response: {text2[:200]}"

    def test_send_async_returns_message_id(self, auth_info):
        """send_async returns valid response."""
        base_url, headers, sid, _ = auth_info
        r = requests.post(f"{base_url}/api/chat/send_async", headers=headers, json={
            "session_id": sid, "content": "test",
        })
        assert r.status_code == 200
        data = r.json()
        assert "user_message_id" in data
        assert "assistant_message_id" in data
        assert data["status"] == "processing"

    def test_session_persists(self, auth_info):
        """Session persists across messages."""
        base_url, headers, sid, _ = auth_info

        for i in range(2):
            r = requests.post(f"{base_url}/api/chat/send_async", headers=headers, json={
                "session_id": sid, "content": f"msg {i+1}",
            })
            assert r.status_code == 200

        r = requests.get(f"{base_url}/api/chat/session/{sid}", headers=headers)
        assert r.status_code == 200

    def test_error_with_bad_key(self, real_server):
        """Invalid API key → error event.

        The shared real_server subprocess inherits the host's real key
        at startup, so this test launches its own server with an
        invalid LLM_API_KEY under an isolated HOME (the host
        ~/.quantnodes/.env would otherwise be re-injected by
        load_dotenv(override=True) inside _build_llm_config).
        """
        import tempfile as _tf

        port = _find_free_port()
        base_url = f"http://127.0.0.1:{port}"

        fake_home = Path(_tf.mkdtemp(prefix="badkey_home"))
        (fake_home / ".quantnodes").mkdir()
        bridge = fake_home / ".quantnodes" / "llm.json"
        bridge.write_text(json.dumps({
            "llm": {
                "active_profile": "minimax",
                "profiles": {
                    "minimax": {
                        "provider": "minimax",
                        "model": "minimax-M3",
                        "api_key": "env:LLM_API_KEY",
                        "base_url": "https://api.minimaxi.com/v1",
                    },
                },
            },
        }))

        env = os.environ.copy()
        env["LLM_API_KEY"] = "sk-invalid"
        env["HOME"] = str(fake_home)
        # Keep the host's user site-packages importable (uvicorn lives
        # in ~/.local for /usr/bin/python3; a fake HOME would break it).
        env["PYTHONUSERBASE"] = str(Path.home() / ".local")
        env.pop("AGENTNODES_LLM_CONFIG", None)
        env.update({
            "STATIC_DIR": str(REPO_ROOT / "webui" / "static"),
            "CORS_ORIGINS": "*",
            "PYTHONUNBUFFERED": "1",
            "AN_WORKSPACE_PATH": str(REPO_ROOT),
        })
        cmd = [
            sys.executable, "-u", "-m", "uvicorn",
            "agentnodes.interfaces.api.app:create_app",
            "--factory",
            "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "warning",
        ]
        log_file = open(Path(_tf.gettempdir()) / "real_llm_badkey_server.log", "wb")
        proc = subprocess.Popen(
            cmd, cwd=str(REPO_ROOT), env=env,
            stdout=log_file, stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_http(f"{base_url}/health")

            r = requests.post(f"{base_url}/api/auth/login", json={"username": "admin", "password": "admin"})
            token = r.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            r = requests.post(f"{base_url}/api/chat/session", headers=headers, json={"title": "Err"})
            sid = r.json()["id"]

            r = requests.post(f"{base_url}/api/chat/send_async", headers=headers, json={
                "session_id": sid, "content": "test",
            })
            assert r.status_code == 200

            _, error_msg = _read_sse_until_done(base_url, sid, token, timeout=60)
            assert error_msg is not None, "Expected error event for invalid API key"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            log_file.close()
