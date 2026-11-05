"""阶段 1a 演示：一条真实对话走完新架构八站。

用法（需已配置 LLM key，见 core/llm 四层配置）：
    py -3.12 scripts/stage1a_demo.py "什么是动量因子？"
    py -3.12 scripts/stage1a_demo.py                # 交互式多轮（exit 退出）

链路：WorkRequest → EntryPoint → mailbox → SessionController（签发 turn/run）
→ GraphEngine（llm 节点）→ 事件流打印 → 回答。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.core.gateway import SessionController
from agentnodes.core.llm import LLMConfig, OpenAICompatClient
from agentnodes.core.orchestration import EntryPoint, SourceKind, WorkKind, WorkRequest


def main() -> None:
    args = [a for a in sys.argv[1:] if a.strip()]
    interactive = not args

    client = OpenAICompatClient(LLMConfig.load())
    sink = lambda kind, data: print(f"  [event] {kind}: {data}")  # noqa: E731
    ep = EntryPoint(sink=sink)
    controller = SessionController(
        ep, client=client, system="你是量化研究助手，用中文简洁回答。", sink=sink
    )

    def send(text: str) -> None:
        request = WorkRequest(
            kind=WorkKind.CHAT,
            source=SourceKind.API_CHAT,
            session_key="chat:demo",
            payload={"text": text},
        )
        receipt = ep.submit(request)
        if not receipt.accepted:
            print(f"  拒绝: {receipt.reason}")
            return
        results = controller.run_pending()
        for run in results:
            if run.ok:
                print(f"\n助手: {run.answer}\n  (turn={run.turn_id} run={run.run_id})")
            else:
                print(f"\n运行失败: {run.error}")

    print("=== 阶段 1a 演示：WorkRequest → mailbox → 准入 → 引擎 → 事件流 ===")
    if interactive:
        print("输入问题（exit 退出）")
        while True:
            try:
                text = input("\n你: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text or text.lower() == "exit":
                break
            send(text)
    else:
        send(args[0])


if __name__ == "__main__":
    main()
