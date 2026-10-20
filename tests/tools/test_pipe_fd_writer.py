"""Cygwin 原生 Windows 子进程 stdin 管道（``PipeFdWriter``）测试。

修复背景：Cygwin 的 ``asyncio.create_subprocess_exec(stdin=PIPE)`` 内部用
socketpair，原生 Windows 控制台程序读不了 stdin（进程立即退出）。
``os.pipe()`` 创建的真管道原生程序可以读取。
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from src.tools._bash_support import open_interactive_stdin
from src.tools.bash import _command_exe_hint

_CYGWIN = (sys.platform or "").startswith(("cygwin", "msys"))
_CYGWIN_ONLY = pytest.mark.skipif(not _CYGWIN, reason="需要 Cygwin / MSYS2")


async def test_pipe_fd_writer_roundtrip():
    read_fd, writer = open_interactive_stdin()
    try:
        writer.write(b"hello\n")
        await writer.drain()
        data = await asyncio.get_running_loop().run_in_executor(
            None, os.read, read_fd, 64)
        assert data == b"hello\n"
    finally:
        writer.close()
        try:
            os.close(read_fd)
        except OSError:
            pass


async def test_pipe_fd_writer_close_is_idempotent():
    _read_fd, writer = open_interactive_stdin()
    writer.close()
    writer.close()
    with pytest.raises(ValueError):
        writer.write(b"x")


@_CYGWIN_ONLY
async def test_native_windows_program_reads_pipe_stdin():
    """原生 more.com 能从 os.pipe 读入 stdin（socketpair 则不行）。"""
    exe = "/cygdrive/c/Windows/System32/more.com"
    if not os.path.exists(exe):
        pytest.skip("系统没有 more.com")
    read_fd, writer = open_interactive_stdin()
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/bash", "-c", exe,
        stdin=read_fd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    os.close(read_fd)
    try:
        writer.write(b"hello-pipe\n")
        await writer.drain()
        await asyncio.sleep(0.2)
        writer.close()
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()
    assert b"hello-pipe" in stdout, (stdout, stderr)


def test_command_exe_hint_prefers_last_exe_token():
    assert _command_exe_hint("notepad.exe") == "notepad.exe"
    assert _command_exe_hint("cmd /c notepad.exe") == "notepad.exe"
    assert _command_exe_hint(
        "powershell -Command 'Start-Process mspaint.exe'"
    ) == "mspaint.exe"
    # shell / 解释器名不做同名兜底（Cygwin 下会串到其它任务窗口）
    assert _command_exe_hint("python script.py") is None
    assert _command_exe_hint("bash --login -i") is None
    assert _command_exe_hint("") is None
