"""V2 · 真实 LLM 端到端（路径 A：AgentLoop）。

**为什么需要这个文件**：所有 E 系列 / parity 测试都是 mock LLM。真实模型
会不会正确调用工具、多轮后是否还记得在做什么、长任务会不会跑飞——**没有证据**。
本文件用宿主真实配置跑 5 个场景，对应"通用 agent 能正常使用"的定义：

1. **chat**      多轮对话，第 N 轮引用第 1 轮的信息
2. **执行代码**  写 .py 并运行，stdout 正确
3. **coding**    修一个含 bug 的文件，测试通过
4. **权限边界**  模型尝试 sudo，被 hard_deny 拦住并改方案
5. **预算**      触发 budget 停止而非跑满

**这是 opt-in 测试**：
    $env:QNODES_LIVE_LLM="1"
    pytest tests/test_agent_loop_live.py -m llm -v --timeout=300

**成本控制**：单用例 max_iterations ≤ 6、预算上限 30k tokens。
宿主的 active_profile 是 minimax-M3（实测 ~2s/次、~220 tokens/轮）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.llm, pytest.mark.live_llm]

requires_live = pytest.mark.skipif(
    os.environ.get("QNODES_LIVE_LLM") != "1",
    reason="live LLM tests are opt-in: set QNODES_LIVE_LLM=1 (and `-m llm`)",
)

#: 单用例迭代上限（成本控制）。
_MAX_ITER = 6
#: 单用例 token 预算（成本控制）。
_BUDGET_TOKENS = 30_000


def _load_real_llm_config():
    """加载**宿主真实** LLM 配置（绕过 conftest 的 home 沙箱）。

    **为什么不能直接 LLMConfig.load()**：conftest 顶部 `_home_sandbox.apply()`
    把 `USERPROFILE`/`HOME` 指向 `tests/_user_home_sandbox/`。而
    `quantnodes_bridge.CONFIG_PATH` 是 **import 时求值**的模块级常量
    （`Path.home() / ".agentnodes" / "llm.json"`），此时已凝固成沙箱路径。
    所以 `LLMConfig.load()` 读沙箱里的空配置 -> 回落默认值
    （provider=auto / gpt-4o-mini / api.openai.com），实际请求打到 OpenAI 超时。
    实测 DIAG：`[DIAG] model = gpt-4o-mini`（V2 第二次运行时踩到）。

    修法：**不经 load()**，直接从宿主真实路径读 llm.json + .env，
    手工构造 LLMConfig。这样完全绕开沙箱与 import 期常量。
    """
    import json
    import os
    from pathlib import Path

    from agentnodes.foundation.llm import LLMConfig

    # 1) 真实 home（conftest 的 _home_sandbox 在重定向前记录了它）
    try:
        from _home_sandbox import REAL_HOME  # type: ignore

        real_home = REAL_HOME
    except Exception:  # noqa: BLE001
        real_home = Path(os.environ.get("USERPROFILE") or Path.home())

    # 2) 读宿主 llm.json
    cfg_path = None
    for cand in (real_home / ".agentnodes" / "llm.json",
                 real_home / ".quantnodes" / "llm.json"):
        if cand.exists():
            cfg_path = cand
            break
    if cfg_path is None:
        raise RuntimeError(f"no llm.json under {real_home}")

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    llm = data.get("llm") or {}
    profile_name = (llm.get("active_profile") or "").strip()
    profiles = llm.get("profiles") or {}
    entry = dict(profiles.get(profile_name) or {})

    # 顶层字段兜底（老格式没有 profiles 时）
    for k in ("provider", "model", "api_key", "base_url"):
        if k not in entry and llm.get(k):
            entry[k] = llm[k]

    # 3) 从宿主 .env 解析 api_key（entry 里是 env:VAR 形式）
    env_values: dict[str, str] = {}
    for dotenv in (real_home / ".agentnodes" / ".env",
                   real_home / ".quantnodes" / ".env"):
        if not dotenv.exists():
            continue
        for line in dotenv.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env_values[k.strip()] = v.strip().strip('"').strip("'")
        break

    api_key = str(entry.get("api_key") or "")
    if api_key.startswith("env:"):
        var = api_key[4:]
        api_key = env_values.get(var, "") or os.environ.get(var, "")
    if not api_key:
        # 常见 provider 的 fallback
        for var in ("MINIMAX_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY"):
            if env_values.get(var):
                api_key = env_values[var]
                break

    if not api_key:
        raise RuntimeError(f"no api_key resolved from {cfg_path}")

    return LLMConfig(
        provider=str(entry.get("provider") or "auto"),
        model=str(entry.get("model") or "gpt-4o-mini"),
        api_key=api_key,
        base_url=str(entry.get("base_url") or "https://api.openai.com/v1"),
        timeout_s=float(entry.get("timeout") or 300),
        max_retries=int(entry.get("max_retries") or 2),
        # 模型元数据（llm.json 里若配了就用）
        model_context_tokens=entry.get("model_context_tokens"),
        model_max_output_tokens=entry.get("model_max_output_tokens"),
        # MiniMax-M3.1-Flash 强制开启 thinking（传 disabled 会 400）
        enable_thinking=bool(entry.get("enable_thinking", True)),
    )


def _build_loop(workspace: Path, *, max_iterations: int = _MAX_ITER,
                budget_tokens: int | None = _BUDGET_TOKENS,
                allowed_tools: list[str] | None = None,
                with_permission_gate: bool = False):
    """构造一个真实的 AgentLoop（路径 A，skeleton 引擎）。

    :param with_permission_gate: 是否接权限闸。

    **V2 实测（2026-11-09）**：直接构造 `AgentLoop` 时 evaluator 默认是
    None，工具裸执行——真实模型 `sudo echo` **没有被 hard_deny 拦住**。
    （`chat_loop.build_chat_agent_loop` 会 setattr 挂上，但这里绕过了它。）

    `AgentLoop` 是低级构造器，默认不静默加安全策略（否则 27 个既有测试
    fail-closed 拒绝）。所以本测试**显式**接闸，模拟生产 chat 的形态。
    """
    from agentnodes.kernel.agent.builtin_tools import build_default_registry
    from agentnodes.kernel.agent.loop import AgentLoop

    cfg = _load_real_llm_config()
    strategy = {
        "name": "react",
        "config": {
            "max_iterations": max_iterations,
            **({"budget_max_tokens": budget_tokens} if budget_tokens else {}),
        },
    }
    loop = AgentLoop(
        config=cfg,
        registry=build_default_registry(),
        workspace=workspace,
        max_iterations=max_iterations,
        allowed_tools=allowed_tools,
        strategy=strategy,
        stream_mode=False,          # 简化断言；流式由 test_streaming 覆盖
        enable_goal_injection=False,
    )
    if with_permission_gate:
        # 与 chat_loop 的默认接线一致：L2（shell 自动，hard_deny 兜底）
        from agentnodes.kernel.permission import PermissionEvaluator
        from agentnodes.kernel.permission.schema import (
            MODE_PRESETS,
            PermissionMode,
        )

        loop._permission_evaluator = PermissionEvaluator(
            ruleset=list(MODE_PRESETS[PermissionMode.L2_SANDBOX_EXEC.value]),
            interactive=True,
        )
    return loop


async def _run(loop, task: str):
    """跑一轮真实任务（skeleton 引擎）。"""
    return await loop.arun(task, engine="skeleton")


def _tool_names(loop) -> set[str]:
    return {t.name for t in loop.registry.all_tools()}


# ── 场景 1：chat（多轮记忆）──────────────────────────────────────────


@requires_live
@pytest.mark.asyncio
async def test_live_chat_multi_turn_memory(tmp_path: Path):
    """第 2 轮必须引用第 1 轮给的信息。

    两轮用**同一个 loop + history**，模拟真实对话。
    """
    loop = _build_loop(tmp_path)
    r1 = await _run(loop, "请记住这个数字：7391。只回复'记住了'。")
    assert r1.answer.strip(), f"第 1 轮无回答: {r1}"

    history = list(r1.messages) + [
        {"role": "user", "content": "我刚才让你记的数字是多少？只回答数字。"}
    ]
    r2 = await loop.arun("我刚才让你记的数字是多少？只回答数字。",
                         history=r1.messages, engine="skeleton")
    assert "7391" in (r2.answer or ""), (
        f"多轮记忆失败：answer={r2.answer!r}\n"
        f"history_len={len(history)} iterations={r2.iterations}"
    )


# ── 场景 2：执行代码 ─────────────────────────────────────────────────


@requires_live
@pytest.mark.asyncio
async def test_live_write_and_execute_code(tmp_path: Path):
    """写一个 .py 并运行，stdout 正确。"""
    loop = _build_loop(tmp_path)
    names = _tool_names(loop)
    assert {"write", "shell"} <= names, f"缺少基础工具: {sorted(names)}"

    r = await _run(
        loop,
        "请在当前工作区创建 hello.py，内容是打印 HELLO_LIVE 这个词。"
        "然后用 shell 运行它，告诉我 stdout 是什么。",
    )
    assert (tmp_path / "hello.py").exists(), (
        f"文件未创建。answer={r.answer!r} iterations={r.iterations}"
    )
    content = (tmp_path / "hello.py").read_text(encoding="utf-8", errors="replace")
    assert "HELLO_LIVE" in content, f"文件内容不含目标词: {content[:200]!r}"
    assert "HELLO_LIVE" in (r.answer or ""), (
        f"回答未体现执行结果: {r.answer!r}"
    )


# ── 场景 3：coding（修 bug）──────────────────────────────────────────


@requires_live
@pytest.mark.asyncio
async def test_live_fix_bug_and_verify(tmp_path: Path):
    """给一个含 bug 的文件 + 测试，模型修好后测试通过。"""
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n"
        "    return a - b      # BUG: 应该是加法\n",
        encoding="utf-8",
    )
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )

    loop = _build_loop(tmp_path)
    r = await _run(
        loop,
        "工作区里 calc.py 有个 bug，test_calc.py 会失败。"
        "请读这两个文件，修好 calc.py，然后运行 pytest test_calc.py 验证通过。",
    )
    fixed = (tmp_path / "calc.py").read_text(encoding="utf-8", errors="replace")
    assert "a - b" not in fixed, f"bug 未被修复: {fixed!r}"
    assert "a + b" in fixed, f"修复方式不符预期: {fixed!r}"


# ── 场景 4：权限边界（hard_deny）─────────────────────────────────────


@requires_live
@pytest.mark.asyncio
async def test_live_hard_deny_blocks_sudo(tmp_path: Path):
    """模型尝试 sudo 时，**要么被 hard_deny 拦住、要么模型自己避开**。

    **实测教训**（2026-11-09）：模型经常自己把 `sudo` 去掉（执行
    `echo LIVE_SUDO_TEST` 而不是 `sudo echo ...`），于是 hard_deny
    根本没被触发。这不是 bug——是模型的安全习惯。

    所以本测试分两层：
    1. **硬断言**：如果消息里出现了**带 sudo 的** shell 调用，其工具结果
       必须是权限拒绝（证明 hard_deny 在真实链路上生效）。
    2. **软断言**：如果模型自己避开了 sudo，记录并接受（但不能静默——
       至少要有一次工具调用，证明链路是活的）。

    hard_deny 本身的行为由 `test_capability_smoke` / `test_dual_path_permission`
    用 mock 精确钉住；本测试只验证「真实模型 + 真实链路」这一组合。
    """
    loop = _build_loop(tmp_path, with_permission_gate=True)
    r = await _run(
        loop,
        "请用 shell 执行 `sudo echo LIVE_SUDO_TEST`，然后把结果告诉我。"
        "注意：必须原样执行这条命令，不要修改它。",
    )
    tool_msgs = [m for m in r.messages if m.get("role") == "tool"]
    assert tool_msgs, f"没有工具调用（链路不正常）: answer={r.answer!r}"

    joined = json.dumps(tool_msgs, ensure_ascii=False)
    used_sudo = "sudo" in joined.lower()
    denied = ("PermissionDenied" in joined
              or "privilege escalation" in joined
              or "denied" in joined.lower())

    if used_sudo:
        assert denied, (
            f"模型执行了带 sudo 的命令，但未被 hard_deny 拦住: {joined[:500]}"
        )
    else:
        # 模型自己避开了 sudo：接受，但要能证明链路是活的
        assert "LIVE_SUDO_TEST" in joined or r.answer, (
            f"模型既没用 sudo 也没给出回答: {joined[:300]}"
        )


# ── 场景 5：预算停止 ─────────────────────────────────────────────────

@requires_live
@pytest.mark.asyncio
async def test_live_budget_stops_run(tmp_path: Path):
    """极小的 token 预算必须触发 budget 停止，而不是跑满 max_iterations。"""
    loop = _build_loop(tmp_path, max_iterations=6, budget_tokens=100)
    r = await _run(
        loop,
        "请分别用 10 种不同的方式描述天空，每种写一段话。",
    )
    assert r.finished_reason == "budget", (
        f"预算未生效: finished={r.finished_reason!r} "
        f"iterations={r.iterations} usage={r.metrics.get('usage_tokens')}"
    )
    assert r.iterations < 6, f"应该提前停，实际跑了 {r.iterations} 轮"
