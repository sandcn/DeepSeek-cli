"""子进程端口 — 能力接缝（Definition）。

对应 DeepSeek Harness 的 ``ctx.subprocess``：本地后端 spawn 真实进程；把
Provider 指向远程沙箱，进程便在沙箱里启动。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, runtime_checkable


@dataclass
class ProcessResult:
    """进程执行结果。"""

    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
        }


@runtime_checkable
class SubprocessPort(Protocol):
    """子进程接缝协议。"""

    async def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        stdin: Optional[str] = None,
    ) -> ProcessResult:
        """启动进程并等待结束（可设超时与 stdin）。"""
        ...

    async def spawn_background(
        self,
        argv: Sequence[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        on_output=None,
    ) -> Any:
        """启动后台进程，返回句柄（供 jobs 接缝管理）。"""
        ...


__all__ = ["ProcessResult", "SubprocessPort"]
