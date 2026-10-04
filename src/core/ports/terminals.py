"""终端端口 — 能力接缝（Definition）。

对应 DeepSeek Harness 的 ``ctx.terminals``：持久化终端执行后端，供
``terminal`` 类工具使用（与一次性 shell 执行区分）。
"""

from __future__ import annotations

from typing import Any, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class TerminalsPort(Protocol):
    """持久化终端接缝协议。"""

    async def open(self, argv: Optional[list] = None, *, cwd: Optional[str] = None) -> str:
        """打开一个持久终端会话，返回会话 id。"""
        ...

    async def write(self, session_id: str, data: str) -> bool:
        """向终端会话写入数据。"""
        ...

    async def read(self, session_id: str) -> str:
        """读取终端会话累积输出。"""
        ...

    async def close(self, session_id: str) -> bool:
        """关闭终端会话。"""
        ...

    def list(self) -> List[dict]:
        """列出活跃终端会话。"""
        ...


__all__ = ["TerminalsPort"]
