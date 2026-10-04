"""Shell 端口 — 能力接缝（Definition）。

对应 DeepSeek Harness 的 ``ctx.shell``：注册 shell 执行后端；本地后端通过
``ctx.subprocess`` spawn 进程。Consumer 是 ``bash`` / ``bash_opt`` 等工具。
"""

from __future__ import annotations

from typing import Mapping, Optional, Protocol, runtime_checkable

from .subprocess import ProcessResult


@runtime_checkable
class ShellPort(Protocol):
    """Shell 接缝协议。"""

    async def run(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        stdin: Optional[str] = None,
    ) -> ProcessResult:
        """同步执行 shell 命令并返回结果。"""
        ...

    async def spawn(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        on_output=None,
    ):
        """后台执行 shell 命令，返回句柄。"""
        ...


__all__ = ["ShellPort"]
