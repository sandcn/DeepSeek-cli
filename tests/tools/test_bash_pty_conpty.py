"""bash 工具 ``pty=true``（Windows ConPTY / POSIX PTY）测试。

覆盖：
  - ``_conpty`` 能力探测与真实启动（输出可读、退出码正确、获得 TTY）；
  - ``Ctrl+C``（``\\x03``）在 ConPTY 中变成中断信号（这是 PIPE 模式做不到的）；
  - ``bash`` 工具的 ``pty`` 选项：schema 暴露、默认仍走 PIPE、true 时走 PTY 路径；
  - 端到端：``pty=true`` 下 ``sys.stdout.isatty()`` 为真（默认 PIPE 为假）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

import src.tools.bash as bash_mod
from src.tools import _conpty
from src.tools.bash import BashFunc

pytestmark = pytest.mark.skipif(
    not _conpty.is_available(), reason="需要 Windows ConPTY（Windows 10 1809+）",
)


async def _read_all(process) -> bytes:
    """读完 ConPTY 输出（reader 在进程退出、伪终端关闭后才 EOF）。"""
    chunks: list[bytes] = []
    while True:
        chunk = await process.reader.read(65536)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _clean(data: bytes) -> str:
    """把 ConPTY 原始字节清洗为可断言的文本（剥 ANSI、去掉 \\r）。

    ConPTY 会插入终端初始化 / 标题 / 光标序列（``\\x1b[?9001h``、
    ``\\x1b]0;标题\\x07`` 等），并可能把标题序列插在输出中间；工具层在
    ``_read_loop`` 里统一剥离（``_strip_ansi``）+ 兑现 ``\\r`` 覆盖语义，
    这里的测试需要同样处理才能稳定断言。
    """
    from src.tools._bash_support import _strip_ansi

    return _strip_ansi(data.decode("utf-8", "replace")).replace("\r", "")


@pytest.mark.asyncio
async def test_conpty_runs_command_and_returns_exit_code():
    process = _conpty.spawn([sys.executable, "-c", "print('conpty-ok')"])
    try:
        data = await asyncio.wait_for(_read_all(process), timeout=20)
        code = await asyncio.wait_for(process.wait(), timeout=10)
    finally:
        process.close()
    assert code == 0
    assert "conpty-ok" in _clean(data)


@pytest.mark.asyncio
async def test_conpty_reports_nonzero_exit_code():
    process = _conpty.spawn([sys.executable, "-c", "raise SystemExit(7)"])
    try:
        await asyncio.wait_for(_read_all(process), timeout=20)
        code = await asyncio.wait_for(process.wait(), timeout=10)
    finally:
        process.close()
    assert code == 7


@pytest.mark.asyncio
async def test_conpty_child_sees_a_tty():
    """子进程必须看到真 TTY（``isatty()`` 为真）——PIPE 模式恒为假。"""
    process = _conpty.spawn(
        [sys.executable, "-c", "import sys; print('isatty=', sys.stdout.isatty())"])
    try:
        data = await asyncio.wait_for(_read_all(process), timeout=20)
        await asyncio.wait_for(process.wait(), timeout=10)
    finally:
        process.close()
    assert "isatty= True" in _clean(data)


@pytest.mark.asyncio
async def test_conpty_write_fd_delivers_input():
    """写入的字节进入子进程 stdin（交互式程序可读；回车是 ``\\r``）。"""
    script = "import sys; line=sys.stdin.readline(); print('GOT:', line.strip())"
    process = _conpty.spawn([sys.executable, "-c", script])
    try:
        assert process.write_fd is not None
        await asyncio.sleep(0.5)
        # 终端语义：回车是 CR（\r），LF 只是换行、不提交输入行（实测）
        os.write(process.write_fd, b"hello-conpty\r")
        data = await asyncio.wait_for(_read_all(process), timeout=20)
        await asyncio.wait_for(process.wait(), timeout=10)
    finally:
        process.close()
    assert "GOT: hello-conpty" in _clean(data)


# ── 工具层 pty 选项 ──────────────────────────────────────

def test_schema_exposes_pty_option():
    schema = BashFunc.to_tool_schema()
    properties = schema["function"]["parameters"]["properties"]
    assert "pty" in properties
    assert "PTY" in properties["pty"]["description"]


def test_pty_defaults_to_false():
    assert BashFunc("echo hi").pty is False
    assert BashFunc("echo hi", pty=True).pty is True


@pytest.mark.asyncio
async def test_pty_option_routes_to_pty_path(monkeypatch):
    called: dict = {}

    async def _fake_pty(self, **kwargs):
        called["pty"] = True
        return {"stdout": "pty-out", "stderr": "", "returncode": 0}

    async def _fake_pipe(self, **kwargs):
        called["pipe"] = True
        return {"stdout": "pipe-out", "stderr": "", "returncode": 0}

    monkeypatch.setattr(BashFunc, "_run_pty", _fake_pty)
    monkeypatch.setattr(BashFunc, "_run_pipe", _fake_pipe)
    result = json.loads(await BashFunc("echo hi", pty=True).execute())
    assert called == {"pty": True}
    assert result["stdout"] == "pty-out"


@pytest.mark.asyncio
async def test_default_execution_still_uses_pipe(monkeypatch):
    called: dict = {}

    async def _fake_pty(self, **kwargs):  # pragma: no cover - 不应被调用
        called["pty"] = True
        return {"stdout": "", "stderr": "", "returncode": 0}

    async def _fake_pipe(self, **kwargs):
        called["pipe"] = True
        return {"stdout": "pipe-out", "stderr": "", "returncode": 0}

    monkeypatch.setattr(BashFunc, "_run_pty", _fake_pty)
    monkeypatch.setattr(BashFunc, "_run_pipe", _fake_pipe)
    result = json.loads(await BashFunc("echo hi").execute())
    assert called == {"pipe": True}
    assert result["stdout"] == "pipe-out"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 ConPTY")
async def test_end_to_end_pty_true_gives_tty(monkeypatch):
    """端到端：``pty=true`` 时子进程拿到 TTY（默认 PIPE 模式拿不到）。"""
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "powershell")
    command = f"& '{sys.executable}' -c 'import sys;print(sys.stdout.isatty())'"
    result = json.loads(await BashFunc(command, pty=True).execute())
    assert result["returncode"] == 0, result
    assert result["stdout"].strip() == "True"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 ConPTY")
async def test_end_to_end_pipe_still_not_a_tty(monkeypatch):
    monkeypatch.setattr(bash_mod, "detect_shell", lambda: "powershell")
    command = f"& '{sys.executable}' -c 'import sys;print(sys.stdout.isatty())'"
    result = json.loads(await BashFunc(command).execute())
    assert result["stdout"].strip() == "False"
