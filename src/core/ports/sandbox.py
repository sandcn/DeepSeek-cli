"""沙盒端口 — 能力接缝（Definition）。

对应 DeepSeek Harness 的 ``ctx.sandbox``：限制所启动的进程 / 文件操作；
消费方在启动进程前包装 argv、在写入文件前校验路径。
"""

from __future__ import annotations

from typing import Mapping, Optional, Protocol, Sequence, Tuple, runtime_checkable


@runtime_checkable
class SandboxPort(Protocol):
    """沙盒接缝协议。"""

    def check_path(self, path: str, *, operation: str = "read") -> Tuple[bool, Optional[str]]:
        """校验路径是否允许该操作，返回 (允许, 拒绝原因)。"""
        ...

    def check_argv(self, argv: Sequence[str]) -> Tuple[bool, Optional[str]]:
        """校验命令 argv 是否允许执行。"""
        ...

    def wrap_argv(self, argv: Sequence[str]) -> Sequence[str]:
        """在启动进程前包装 argv（如注入沙盒前缀）。"""
        ...

    def wrap_env(self, env: Mapping[str, str]) -> Mapping[str, str]:
        """在启动进程前包装环境变量。"""
        ...


__all__ = ["SandboxPort"]
