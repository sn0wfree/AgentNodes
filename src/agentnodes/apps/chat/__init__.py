# -*- coding: utf-8 -*-
"""对话业务策略（L2）—— 可替换的产品决策，不是平台机制。

**为什么需要这一层**：对话能力原先 100% 散在 L1（``routers/chat.py`` 1,016
行把 HTTP 适配、会话编排、业务策略糅在一起）。L1 的判据是「哑适配器：
只构造请求 + 呈现事件流」，但那份文件里混着三类东西：

| 职责 | 例子 | 该在哪 |
|---|---|---|
| HTTP 适配 | ``send_sync`` / ``chat_events`` | L1 ✅ |
| 会话编排 | ``_send_via_gateway`` / ``_install_chat_event_bridge`` | L3 |
| **业务策略** | **persona 清单** / **shell 工具开关** / **导出格式** | **L2 ← 本包** |

业务策略不需要跨进程、不需要状态、不需要协议——但它们**会随产品决策
变化**（今天允许哪些 persona、明天是否默认开 shell 工具、导出加不加
HTML）。这类东西放 L1 会让「渠道适配器」变成产品配置中心；放 L3 会
污染平台机制。L2 是它们的正确位置。

**模块**：

- :mod:`personas` —— 可选角色清单 + 中文展示名（Composer 的角色选择器）
- :mod:`policy` —— shell 工具开关等运行期策略
- :mod:`export` —— 对话导出格式（markdown / json）

**刻意不包含**（这些不是产品策略）：

- **会话编排**（``_send_via_gateway`` / ``_install_chat_event_bridge`` /
  SSE 事件生成）—— 有状态、跨进程，属 L3
- **trace 读取**（``get_session_trace``）—— 读 event_log + 回退 jsonl
  的编排逻辑，属 L1 适配或 L3 机制，不是可替换的产品决策
- **HTTP 响应形态**（``PlainTextResponse`` / media_type）—— 传输细节留在 L1

判据：L1 调用它们时**不需要知道实现**，L3 完全不认识它们。
"""
from __future__ import annotations

__all__ = ["export", "personas", "policy"]
