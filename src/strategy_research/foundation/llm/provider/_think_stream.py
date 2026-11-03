"""MiniMax ``<think>`` 跨 chunk 缓冲器（流式 ``fix_delta`` 钩子）。

问题（实测）：MiniMax-M3 把推理内联在 ``delta.content`` 的
``<think>...</think>`` 里，SSE 按 BPE token 切分。实测 chunk 序列：

    [0] ''
    [1] '<think>We need answe'     ← 开标签开始
    [2] 'r in Chinese?'
    ...
    [16] '</think>'                 ← 闭标签在十几个 chunk 之后

下游管线（``parser._process_delta``）是
``fix_delta → extract_thinking → sanitize_delta``，后两步都用**单 chunk 正则**
``THINK_PATTERN``。所以必须由 ``fix_delta`` 保证：**交出去的每个 chunk 里，
``<think>...</think>`` 块要么完整、要么完全没有**。

做法：块内内容全部缓冲，等 ``</think>`` 到达后，把整个块一次性交给下游
（``extract_thinking`` 提取推理 + ``sanitize_delta`` 从 content 剥离）。

代价与取舍：推理块很长时，这一轮 UI 不会逐 token 显示推理（本来也不该显示），
**正文仍然流式**——块闭合后立即恢复逐 chunk 输出。

状态挂在适配器实例上，客户端每请求新建适配器 → 天然按请求隔离。
"""
from __future__ import annotations

import re

__all__ = ["ThinkBlockBuffer", "THINK_OPEN", "THINK_CLOSE", "strip_think_blocks"]

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)


def strip_think_blocks(text: str) -> str:
    """移除完整的 ``<think>...</think>`` 块（整段文本 / 非流式用）。"""
    return _THINK_BLOCK_RE.sub("", text)


class ThinkBlockBuffer:
    """把跨 chunk 的 ``<think>`` 块缓冲成「完整块」再交出。

    状态：``normal``（正常透传） / ``buffering``（块内，攒着不发）。

    交出的 chunk 保证：不含半个块。下游 ``extract_thinking`` 与
    ``sanitize_delta`` 的单 chunk 正则因此总能匹配到完整块。
    """

    __slots__ = ("_mode", "_buf", "_pending")

    def __init__(self) -> None:
        self._mode = "normal"
        self._buf = ""       # buffering 时攒的块内容（含标记）
        self._pending = ""   # 可能是标记前缀的尾部

    def fix(self, text: str) -> str:
        work = self._pending + text
        self._pending = ""

        if self._mode == "buffering":
            self._buf += work
            idx = self._buf.lower().find(THINK_CLOSE)
            if idx < 0:
                return ""
            # 块闭合：整块一次性交出，块后内容继续处理
            end = idx + len(THINK_CLOSE)
            block = self._buf[:end]
            rest = self._buf[end:]
            self._buf = ""
            self._mode = "normal"
            return block + self.fix(rest)

        if not work:
            return ""

        # normal：找完整开标签
        idx = work.lower().find(THINK_OPEN)
        if idx >= 0:
            before = work[:idx]
            self._mode = "buffering"
            self._buf = work[idx:]
            # 单 chunk 内可能已完整闭合：继续走 buffering 分支判定
            return before + self.fix("")

        # 无完整开标签：尾部可能是其前缀 → 缓存
        keep = _open_suffix_len(work)
        if keep:
            self._pending = work[-keep:]
            return work[:-keep]
        return work


def _open_suffix_len(text: str) -> int:
    """返回 text 尾部「可能是 ``<think>`` 前缀」的最大长度（0 = 无）。"""
    lowered = text.lower()
    max_len = min(len(lowered), len(THINK_OPEN) - 1)
    for n in range(max_len, 0, -1):
        if THINK_OPEN.startswith(lowered[-n:]):
            return n
    return 0
