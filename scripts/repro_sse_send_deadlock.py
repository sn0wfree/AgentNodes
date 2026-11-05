import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8979"

env = os.environ.copy()
env.update({
    "STRATEGY_RESEARCH_TEST_CHAT": "1",
    "PYTHONUNBUFFERED": "1",
    "SR_WORKSPACE_PATH": os.getcwd(),
})
p = subprocess.Popen(
    [sys.executable, "-u", "-m", "uvicorn",
     "agentnodes.api.app:create_app", "--factory",
     "--host", "127.0.0.1", "--port", "8979", "--log-level", "info"],
    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="ignore",
)
time.sleep(8)


def req(method, path, payload=None, token=None, timeout=15):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(r, timeout=timeout)
        return resp.status, resp.read().decode()
    except Exception as e:
        return "FAIL", repr(e)[:160]


st, body = req("POST", "/api/auth/login", {"username": "admin", "password": "admin"})
token = json.loads(body)["access_token"]
st, body = req("POST", "/api/chat/session", {"title": "T"}, token=token)
sid = json.loads(body)["id"]
print("sid:", sid)

# ① 开 SSE 流（后台线程，requests stream=True 同测试）
sse_events = []
sse_done = threading.Event()


def listen():
    req2 = urllib.request.Request(
        f"{BASE}/api/chat/events?session_id={sid}&token={token}")
    try:
        resp = urllib.request.urlopen(req2, timeout=20)
        sse_done.set()
        for raw in resp:
            line = raw.decode(errors="ignore").strip()
            if line:
                sse_events.append(line)
    except Exception as e:
        sse_events.append(f"LISTEN-ERR {e!r}")
        sse_done.set()


t = threading.Thread(target=listen, daemon=True)
t.start()
print("SSE connected:", sse_done.wait(10))

# ② SSE 挂着时 send_async（60s 严格超时）
t0 = time.time()
st, body = req("POST", "/api/chat/send_async",
               {"session_id": sid, "content": "test"}, token=token, timeout=60)
print(f"send_async with SSE attached: {st} after {time.time()-t0:.1f}s")
print("  body:", str(body)[:150])
print("  sse lines so far:", len(sse_events))

p.terminate()
try:
    out = p.stdout.read()
except Exception:
    out = ""
tail = out[-1500:]
print("--- server tail ---")
print(tail)
