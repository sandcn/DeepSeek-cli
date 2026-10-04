"""默认能力 Provider — 本地文件系统 / 子进程 / Shell / 终端 / 任务 / 沙盒。

这些是各能力接缝（seam）的**默认 Provider**：把 Provider 替换为远程实现，
Consumer（工具）无需改动即可把整个执行世界搬到别处。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..ports.subprocess import ProcessResult

_logger = logging.getLogger(__name__)

_DECODE_ERRORS = "replace"


def _decode(raw: bytes) -> str:
    if not raw:
        return ""
    return raw.decode("utf-8", _DECODE_ERRORS)


# ═══════════════════════════════════════════════════════════════
# 文件系统
# ═══════════════════════════════════════════════════════════════


class LocalFsProvider:
    """本地文件系统 Provider。"""

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def is_file(self, path: str) -> bool:
        return os.path.isfile(path)

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    def read_text(self, path: str, *, encoding: Optional[str] = None, errors: str = "replace") -> str:
        from ...tools.file_ops import _sync_read_local

        content = _sync_read_local(path, encoding or "utf-8", errors)
        if content is None:
            raise FileNotFoundError(path)
        return content

    def read_bytes(self, path: str) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8") -> None:
        from ...tools.file_ops import _atomic_write_local

        _atomic_write_local(path, content, encoding, "replace")

    def write_bytes(self, path: str, data: bytes) -> None:
        directory = os.path.dirname(os.path.abspath(path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)

    def remove(self, path: str, *, recursive: bool = False) -> None:
        if os.path.isdir(path):
            if recursive:
                shutil.rmtree(path)
            else:
                os.rmdir(path)
        else:
            os.remove(path)

    def move(self, src: str, dst: str) -> None:
        shutil.move(src, dst)

    def mkdir(self, path: str, *, parents: bool = True) -> None:
        os.makedirs(path, exist_ok=True) if parents else os.mkdir(path)

    def list_dir(self, path: str) -> List[str]:
        return [name for name in os.listdir(path) if name not in (".", "..")]

    def walk_files(self, path: str) -> List[str]:
        from ...tools.file_ops import _sync_collect_files

        return list(_sync_collect_files(path))

    def stat(self, path: str) -> Dict[str, Any]:
        info = os.stat(path)
        return {
            "size": info.st_size,
            "mtime": info.st_mtime,
            "is_dir": os.path.isdir(path),
            "is_file": os.path.isfile(path),
        }

    def realpath(self, path: str) -> str:
        return os.path.realpath(path)

    def size_mb(self, path: str) -> float:
        return os.path.getsize(path) / (1024 * 1024)


# ═══════════════════════════════════════════════════════════════
# 子进程 / Shell
# ═══════════════════════════════════════════════════════════════


class LocalSubprocessProvider:
    """本地子进程 Provider（asyncio 子进程）。"""

    async def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        stdin: Optional[str] = None,
    ) -> ProcessResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=dict(env) if env else None,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        payload = stdin.encode("utf-8") if stdin is not None else None
        try:
            out, err = await asyncio.wait_for(process.communicate(payload), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            return ProcessResult(-1, "", "", timed_out=True)
        return ProcessResult(process.returncode or 0, _decode(out), _decode(err))

    async def spawn_background(
        self,
        argv: Sequence[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        on_output=None,
    ) -> Any:
        return await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=dict(env) if env else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def create_process(self, argv: Sequence[str], **kwargs) -> Any:
        """创建原始进程（不经管道封装），供需要流式 / pty 控制的 Consumer。"""
        return await asyncio.create_subprocess_exec(*argv, **kwargs)


class LocalShellProvider:
    """本地 Shell Provider（经 ``ctx.subprocess`` 的语义执行命令）。"""

    async def run(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        stdin: Optional[str] = None,
    ) -> ProcessResult:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            env=dict(env) if env else None,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        payload = stdin.encode("utf-8") if stdin is not None else None
        try:
            out, err = await asyncio.wait_for(process.communicate(payload), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            return ProcessResult(-1, "", "", timed_out=True)
        return ProcessResult(process.returncode or 0, _decode(out), _decode(err))

    async def spawn(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        on_output=None,
    ):
        return await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            env=dict(env) if env else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def create_process(self, command, *, shell: bool = True, **kwargs) -> Any:
        """创建原始进程（不经管道封装），供需要流式 / pty 控制的 Consumer。

        ``shell=True`` 走 shell 解析；否则 ``command`` 视为 argv 序列。
        """
        if shell:
            return await asyncio.create_subprocess_shell(command, **kwargs)
        return await asyncio.create_subprocess_exec(*command, **kwargs)


# ═══════════════════════════════════════════════════════════════
# 终端 / 任务 / 沙盒
# ═══════════════════════════════════════════════════════════════


class LocalTerminalsProvider:
    """本地持久终端 Provider（内存态会话）。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._next = 0

    async def open(self, argv: Optional[list] = None, *, cwd: Optional[str] = None) -> str:
        import itertools

        self._next += 1
        session_id = f"term-{self._next}"
        process = await asyncio.create_subprocess_exec(
            *(argv or [os.environ.get("SHELL", "/bin/sh")]),
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._sessions[session_id] = {"process": process, "buffer": ""}
        return session_id

    async def write(self, session_id: str, data: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        process = session["process"]
        if process.stdin is None:
            return False
        process.stdin.write(data.encode("utf-8"))
        await process.stdin.drain()
        return True

    async def read(self, session_id: str) -> str:
        session = self._sessions.get(session_id)
        if session is None:
            return ""
        process = session["process"]
        if process.stdout is not None:
            try:
                chunk = await asyncio.wait_for(process.stdout.read(65536), timeout=0.05)
                session["buffer"] += _decode(chunk)
            except asyncio.TimeoutError:
                pass
        return session["buffer"]

    async def close(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        process = session["process"]
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
        return True

    def list(self) -> List[dict]:
        return [
            {"id": session_id, "alive": session["process"].returncode is None}
            for session_id, session in self._sessions.items()
        ]


class LocalJobsProvider:
    """本地后台任务 Provider。"""

    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def start(self, job_id: str, task: Any, *, kind: str = "generic", meta: Optional[dict] = None) -> Dict[str, Any]:
        if job_id in self._jobs:
            raise ValueError(f"任务已存在: {job_id!r}")
        record = {"id": job_id, "kind": kind, "task": task, "meta": dict(meta or {})}
        self._jobs[job_id] = record
        return {k: v for k, v in record.items() if k != "task"}

    def stop(self, job_id: str) -> bool:
        record = self._jobs.pop(job_id, None)
        if record is None:
            return False
        task = record.get("task")
        cancel = getattr(task, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                _logger.debug("取消任务失败: %s", job_id, exc_info=True)
        return True

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        record = self._jobs.get(job_id)
        if record is None:
            return None
        return {k: v for k, v in record.items() if k != "task"}

    def list(self) -> List[Dict[str, Any]]:
        return [self.get(job_id) for job_id in sorted(self._jobs)]


class DefaultSandboxProvider:
    """默认沙盒 Provider — 允许全部路径与命令（策略由 policy 插件裁决）。"""

    def check_path(self, path: str, *, operation: str = "read"):
        return True, None

    def check_argv(self, argv: Sequence[str]):
        return True, None

    def wrap_argv(self, argv: Sequence[str]) -> Sequence[str]:
        return argv

    def wrap_env(self, env: Mapping[str, str]) -> Mapping[str, str]:
        return env


__all__ = [
    "LocalFsProvider",
    "LocalSubprocessProvider",
    "LocalShellProvider",
    "LocalTerminalsProvider",
    "LocalJobsProvider",
    "DefaultSandboxProvider",
]
