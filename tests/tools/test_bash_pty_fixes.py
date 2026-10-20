"""bash 工具 PTY 修复项回归测试（Cygwin / POSIX PTY + 跨平台单元逻辑）。

覆盖本轮修复（每条对应一个测试组）：

1. **不再用 pass_fds 保留 slave fd**：子进程及后代不得多出 ``/dev/pty*`` /
   ``/dev/pts/*`` 副本（修复前实测孙进程出现 fd 6 → /dev/pty6）；
2. **POSIX PTY 关闭终端驱动 ECHO**：``op=stdin`` 写入的内容不再在输出里
   重复一遍（修复前 ``cat`` 返回 ``hello\\nhello``）；
3. **Ctrl+C / 中断提示**：被 SIGINT 终止时返回 ``stderr="(命令已被中断)"``
   （修复前 stdout / stderr 都为空，只有 returncode=130）；
4. **PTY 的 stdin/stdout 都是终端**（``interactive=False`` 也不例外）——
   修复前非交互分支 stdin=DEVNULL，子进程看到 ``isatty(stdin)=False``；
5. **pty_ready_fn 接线**：回调可获得 ``resize(cols, rows)``，会话内可调整
   PTY 尺寸（修复前参数无调用方、ConPTY 的 ResizePseudoConsole 从未调用）；
6. **输入回显去重队列**（``_consume_echo_line`` / ``_remember_echo_lines``）
   与 ``_read_loop`` 的过滤行为；
7. **ConPTY 不继承输出管道写端**（Windows-only）：根进程退出后读线程必须
   及时收到 EOF，不能被后台孙进程拖住；``ConPtyProcess.resize`` 可用。

POSIX / Cygwin 相关用例在非 POSIX PTY 环境自动跳过；ConPTY 用例在
Windows 10 1809+ 之外自动跳过。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

import pytest

from src.tools import bash as bash_module
from src.tools import _conpty
from src.tools.bash import BashFunc

_POSIX_PTY = (
    os.name != "nt"
    and getattr(bash_module, "_HAS_PTY", False)
    and hasattr(os, "openpty")
)
_NEEDS_CONPTY = pytest.mark.skipif(
    not _conpty.is_available(), reason="需要 Windows ConPTY（Windows 10 1809+）",
)
_NEEDS_POSIX_PTY = pytest.mark.skipif(
    not _POSIX_PTY, reason="需要 POSIX PTY（pty.openpty + fcntl/termios）",
)


# ═══════════════════════════════════════════════════════════
# 1. 跨平台单元逻辑
# ═══════════════════════════════════════════════════════════

def test_consume_echo_line_matches_line_tail():
    """回显行按「行尾匹配」消费；同一行只消费一次。"""
    queue = {"lines": ["echo hi"]}
    assert bash_module._consume_echo_line(queue, "C:\\path>echo hi\n") is True
    assert queue["lines"] == []
    # 真实输出再次出现同样文本时保留（队列已空）
    assert bash_module._consume_echo_line(queue, "echo hi\n") is False


def test_consume_echo_line_ignores_blank_and_unrelated():
    queue = {"lines": ["hello"]}
    assert bash_module._consume_echo_line(queue, "\n") is False
    assert bash_module._consume_echo_line(queue, "something else\n") is False
    assert queue["lines"] == ["hello"]


def test_consume_echo_line_handles_multiple_pending():
    """多行输入：任意一条命中都能消费（不阻塞在未命中的队首）。"""
    queue = {"lines": ["first", "second", "third"]}
    assert bash_module._consume_echo_line(queue, ">>> second\n") is True
    assert queue["lines"] == ["first", "third"]


def test_remember_echo_lines_splits_and_caps():
    queue = {"lines": []}
    bash_module._remember_echo_lines(queue, "a\r\nb\rc\n")
    assert queue["lines"] == ["a", "b", "c"]

    cap = bash_module._ECHO_FILTER_MAX_LINES
    bash_module._remember_echo_lines(
        queue, "\n".join(f"line-{i}" for i in range(cap + 20)))
    assert len(queue["lines"]) == cap
    assert queue["lines"][-1] == f"line-{cap + 19}"


def test_remember_echo_lines_tolerates_missing_filter():
    bash_module._remember_echo_lines(None, "x")  # 不抛异常


def test_is_ctrl_c_exit():
    assert bash_module._is_ctrl_c_exit(130) is True
    assert bash_module._is_ctrl_c_exit(0xC000013A) is True
    assert bash_module._is_ctrl_c_exit(0) is False
    assert bash_module._is_ctrl_c_exit(1) is False
    assert bash_module._is_ctrl_c_exit(None) is False


def test_notify_pty_ready_supports_both_signatures():
    """只接受 master_fd 的旧回调与新回调（带 resize 关键字）都可用。"""
    seen = {}
    resize = lambda cols, rows: True  # noqa: E731

    def legacy(fd):
        seen["legacy"] = fd

    def modern(fd, resize=None):
        seen["modern"] = (fd, resize)

    bash_module._notify_pty_ready(legacy, 5, resize)
    bash_module._notify_pty_ready(modern, 6, resize)
    assert seen == {"legacy": 5, "modern": (6, resize)}


async def test_read_loop_drops_registered_echo_line():
    """_read_loop 丢弃已登记的输入回显行，其余行原样保留。"""
    reader = asyncio.StreamReader()
    reader.feed_data(b"hello\r\nhello\r\nworld\r\n")
    reader.feed_eof()
    lines: list[str] = []
    echo_filter = {"lines": ["hello"]}

    await BashFunc._read_loop(reader, None, lines, None, echo_filter=echo_filter)

    assert lines == ["hello\n", "world\n"]
    assert echo_filter["lines"] == []


async def test_promote_without_agent_cancels_task_and_kills_process(monkeypatch):
    """超时转后台但无 Agent 上下文：必须终止命令，不能把任务丢下不管。

    修复前 `_promote_to_background` 直接返回错误，exec_task 与子进程继续
    运行——读 stdin 的命令（cat / ssh）会永久挂起且无法再被管理。
    """
    killed: list = []
    monkeypatch.setattr(
        bash_module, "_kill_process_tree", lambda pid: killed.append(pid))

    class _FakeProcess:
        pid = 424242

    cancelled: list = []

    async def _slow():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    task = asyncio.ensure_future(_slow())
    await asyncio.sleep(0)  # 让协程真正开始运行（否则取消发生在启动前）
    func = BashFunc("sleep 30")  # 不关联 agent
    result = await func._promote_to_background(
        task, {"process": _FakeProcess()})

    payload = json.loads(result)
    assert payload["returncode"] == -1, payload
    assert "已终止该命令" in payload["stderr"]
    assert task.cancelled() is True
    assert cancelled == [True]
    assert killed == [424242]


# ═══════════════════════════════════════════════════════════
# 2. POSIX / Cygwin PTY 实测
# ═══════════════════════════════════════════════════════════

async def _wait_for_fd(holder: dict, timeout: float = 5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if "fd" in holder:
            return holder["fd"]
        await asyncio.sleep(0.01)
    raise AssertionError("PTY fd 未就绪")


async def _run_interactive(command: str, writes: list[tuple[bytes, float]]):
    """启动交互式 PTY 任务，按序写入字节（每次后等待给定秒数），返回结果。"""
    holder: dict = {}

    def _ready(process, mode, master_fd=None, stdin_writer=None, job_handle=None):
        holder["fd"] = master_fd

    task = asyncio.ensure_future(
        BashFunc(command)._run_pty(interactive=True, interactive_ready_fn=_ready))
    try:
        fd = await _wait_for_fd(holder)
        # 等子进程真正跑起来（shell 解析 + exec + 建立前台进程组）再写入：
        # 控制字符 \x03 依赖前台进程组，过早写入会在 shell 设置 pgrp 之前
        # 到达而被丢弃（16 worker 并行跑测试时启动更慢，这里给足余量）。
        await asyncio.sleep(0.8)
        for payload, delay in writes:
            os.write(fd, payload)
            await asyncio.sleep(delay)
        return await asyncio.wait_for(task, timeout=20)
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@_NEEDS_POSIX_PTY
@pytest.mark.skipif(
    not os.path.exists("/proc/self/fd"), reason="需要 /proc/self/fd 枚举 fd")
async def test_pty_child_has_no_extra_slave_fd():
    """子进程（及后代）不得继承额外的 PTY slave fd（pass_fds 已移除）。"""
    result = json.loads(
        await BashFunc("ls -l /proc/self/fd").execute())
    assert result["returncode"] == 0, result
    tty_targets = re.findall(r"-> /dev/(?:pty\d+|pts/\d+)", result["stdout"])
    # 0 / 1 / 2 三个标准 fd 指向 slave；多出来的都是 pass_fds 泄漏
    assert len(tty_targets) == 3, result["stdout"]


@_NEEDS_POSIX_PTY
async def test_pty_terminal_echo_is_disabled():
    """终端驱动的本地回显已关闭：写入的一行只出现在程序输出里一次。"""
    result = await _run_interactive(
        "cat", [(b"hello\r", 0.4), (b"\x04", 0.2)])

    assert result["returncode"] == 0, result
    assert result["stdout"].strip() == "hello", result


@_NEEDS_POSIX_PTY
async def test_pty_ctrl_c_returns_interrupt_hint():
    """Ctrl+C（\\x03）经控制终端变成 SIGINT，返回中断提示而不是空结果。"""
    result = await _run_interactive(
        "sleep 30; echo done", [(b"\x03", 0.2)])

    assert result["returncode"] == 130, result
    assert result["stderr"] == "(命令已被中断)", result


@_NEEDS_POSIX_PTY
async def test_pty_interactive_false_still_connects_stdin_to_tty():
    """PTY 模式下 stdin 也是终端（interactive=False 不再是半吊子分支）。"""
    command = (
        f"'{sys.executable}' -c "
        "\"import sys; print(sys.stdin.isatty(), sys.stdout.isatty())\"")
    result = await BashFunc(command)._run_pty(interactive=False)

    assert result["returncode"] == 0, result
    assert result["stdout"].strip() == "True True", result


@_NEEDS_POSIX_PTY
async def test_pty_ready_fn_exposes_working_resize():
    """pty_ready_fn 提供 resize(cols, rows)，真实改变会话的终端尺寸。"""
    holder: dict = {}

    def _ready(master_fd, resize=None):
        holder["fd"] = master_fd
        holder["resize"] = resize

    task = asyncio.ensure_future(
        BashFunc("sleep 1; stty size")._run_pty(pty_ready_fn=_ready))
    try:
        fd = await _wait_for_fd(holder)
        assert fd is not None
        assert callable(holder.get("resize")), "resize 回调未提供"
        # 等子进程启动后再调整尺寸（spawn 前的设置也生效，这里验证运行期调整）
        await asyncio.sleep(0.3)
        assert holder["resize"](100, 40) is True
        result = await asyncio.wait_for(task, timeout=20)
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert result["returncode"] == 0, result
    # stty size 输出 "rows cols"
    assert result["stdout"].strip() == "40 100", result


# ═══════════════════════════════════════════════════════════
# 3. Windows ConPTY 实测（仅在支持 ConPTY 的环境运行）
# ═══════════════════════════════════════════════════════════

async def _read_all(process, timeout: float = 15) -> bytes:
    chunks: list[bytes] = []
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError("ConPTY 输出未在超时内 EOF")
        chunk = await asyncio.wait_for(process.reader.read(65536), timeout=remaining)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


@_NEEDS_CONPTY
async def test_conpty_child_does_not_hold_output_pipe():
    """根进程退出后读线程必须及时 EOF（后代不得继承输出管道写端）。

    复现修复前的缺陷：``cmd /c start /b ping`` 让 ping 驻留，旧实现把
    ConPTY 的 output_write 继承给子进程 → ping 持有写端 → ReadFile 永远
    等不到 EOF → 任务卡死。修复后 ``bInheritHandles=False``，cmd 退出即
    EOF（即使 ping 还活着）。
    """
    process = _conpty.spawn(
        ["cmd", "/c", "start", "/b", "ping", "-n", "30", "127.0.0.1"])
    try:
        data = await _read_all(process, timeout=10)
        await asyncio.wait_for(process.wait(), timeout=5)
    finally:
        process.close()
    # 只要没超时就算通过（ping 的后续输出不属于 ConPTY 的 EOF 条件）
    assert isinstance(data, bytes)


@_NEEDS_CONPTY
async def test_conpty_resize_reaches_child():
    """ConPtyProcess.resize 调用 ResizePseudoConsole 并影响子进程读到的尺寸。"""
    script = (
        "import time, shutil; time.sleep(0.8); "
        "size = shutil.get_terminal_size(); "
        "print('SIZE', size.columns, size.lines)")
    process = _conpty.spawn(
        [sys.executable, "-c", script], cols=80, rows=24)
    try:
        assert process.resize(100, 40) is True
        data = await _read_all(process, timeout=15)
        await asyncio.wait_for(process.wait(), timeout=5)
    finally:
        process.close()
    from src.tools._bash_support import _strip_ansi

    clean = _strip_ansi(data.decode("utf-8", "replace")).replace("\r", "")
    assert "SIZE" in clean
    assert "100 40" in clean, clean
