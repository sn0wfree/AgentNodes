"""跨会话长期记忆（kernel/memory）——与 session/state 历史不是同一数据域。

## 所有权与依赖方向（顺序④ owner 标注，2026-11-18）

- **本包不 import kernel/agent**：frontmatter 解析器已迁入本包
  （``frontmatter.py``，唯一消费者 = ``persistent.py``），
  ``memory→agent`` 反向边已在顺序④拆除（e8dcf097）。
- **保留边 `kernel/agent → kernel/memory`（owner：kernel/agent）**：
  AgentLoop / AgentContext 使用 ``PersistentMemory`` 做跨会话记忆——
  这是真实运行依赖（agent 是消费方，memory 是被消费的基础设施），
  消费点：``agent/loop/core.py`` / ``agent/context/core.py`` /
  ``agent/context/injector.py``（lazy）。依据：20261118 计划 §11.3
  memory 行（"保留且与 session history 区分"）+ §11.2 agent↔memory 行。
- session/message **历史**在 ``kernel/session/state``（另一数据域）。
"""
from agentnodes.kernel.memory.fts5 import MemoryFTS5
from agentnodes.kernel.memory.persistent import MemoryEntry, PersistentMemory

__all__ = [
    "MemoryEntry",
    "MemoryFTS5",
    "PersistentMemory",
]
