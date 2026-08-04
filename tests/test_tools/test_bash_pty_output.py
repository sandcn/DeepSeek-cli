"""bash 工具 PTY 多行输出完整性回归测试。

背景（bug 根因）：
PTY 模式下子进程一次性输出多行后立刻退出（echo/seq/printf 等快速命令），
master 端先收到全部数据，随后 read 返回 EIO（slave 关闭）。默认
``StreamReaderProtocol.connection_lost`` 把 EIO 作为异常 ``set_exception``
到 reader，导致后续 ``readline()`` 直接抛 EIO，``_read_loop`` 把 EIO 误当
EOF break，丢弃缓冲中剩余的行 → 用户侧现象「多行输出只返回第一行」。

修复：
``_PtyEioAsEofProtocol`` 把 EIO 归一化为 ``feed_eof()``：缓冲中剩余数据
先被 ``readline()`` 消费完，再返回 EOF（b''），与真实终端「读完缓冲再
遇 EOF」一致。
"""

from __future__ import annotations

import asyncio
import errno

import pytest

from src.tools.bash import BashFunc, _PtyEioAsEofProtocol


@pytest.mark.skipif(not BashFunc._is_pty_available(), reason="PTY 不可用")
class TestPtyMultiLineOutput:
    """PTY 模式下多行输出完整返回（快速退出命令的 EIO 竞态回归）。"""

    @pytest.mark.asyncio
    async def test_multi_line_immediate_output(self) -> None:
        """一次性输出 3 行后立即退出 → 3 行完整返回（旧 bug：只返回第一行）。"""
        result = await BashFunc(command="printf 'line1\\nline2\\nline3\\n'").execute()
        assert result == "line1\nline2\nline3"

    @pytest.mark.asyncio
    async def test_seq_multiple_lines(self) -> None:
        """seq 输出 10 行 → 完整返回。"""
        result = await BashFunc(command="seq 1 10").execute()
        assert result == "\n".join(str(i) for i in range(1, 11))

    @pytest.mark.asyncio
    async def test_chain_echo(self) -> None:
        """&& 串联多命令 → 每行都保留。"""
        result = await BashFunc(command="echo a && echo b && echo c").execute()
        assert result == "a\nb\nc"

    @pytest.mark.asyncio
    async def test_no_trailing_newline_preserved(self) -> None:
        """无尾换行输出（printf 'abc'）不再因 EIO 丢失（旧 bug：返回无输出）。"""
        result = await BashFunc(command="printf 'abc'").execute()
        assert result == "abc"

    @pytest.mark.asyncio
    async def test_stderr_merged_in_order(self) -> None:
        """PTY 模式下 stdout/stderr 合并且保持真实顺序。"""
        result = await BashFunc(
            command="echo out1; echo err1 >&2; echo out2"
        ).execute()
        assert result == "out1\nerr1\nout2"

    @pytest.mark.asyncio
    async def test_slow_output_unchanged(self) -> None:
        """慢速逐行输出（每行在中断轮询间隔内完成）行为不变。"""
        result = await BashFunc(
            command="for i in 1 2 3; do echo line$i; sleep 0.1; done",
            timeout=30,
        ).execute()
        assert result == "line1\nline2\nline3"

    @pytest.mark.asyncio
    async def test_blank_line_preserved(self) -> None:
        """中间空行保留（'a\\n\\nb' → 3 逻辑行）。"""
        result = await BashFunc(command="printf 'a\\n\\nb\\n'").execute()
        assert result == "a\n\nb"

    @pytest.mark.asyncio
    async def test_empty_output(self) -> None:
        """空输出仍返回 '(无输出)'。"""
        result = await BashFunc(command="true").execute()
        assert result == "(无输出)"

    @pytest.mark.asyncio
    async def test_long_line_over_64kb_preserved(self) -> None:
        """单行超过 StreamReader 默认 limit（64KB）→ 完整返回。

        旧 bug 根因：readline() 基于 readuntil，超长行触发 LimitOverrunError，
        readline 捕获后 clear() 整个内部缓冲，再调 reader.read() 只能读到清空
        后新到达的数据 → 200KB 输出只返回前几 KB。
        修复：_read_loop 改为 reader.read() 取块 + 本地 bytearray 按 \\n 切行。
        """
        result = await BashFunc(
            command="python3 -c \"print('X' * 200000)\""
        ).execute()
        assert result == "X" * 200000

    @pytest.mark.asyncio
    async def test_long_line_no_trailing_newline(self) -> None:
        """超长行且无尾换行（sys.stdout.write）→ 完整返回。"""
        result = await BashFunc(
            command="python3 -c \"import sys; sys.stdout.write('Y' * 200000)\""
        ).execute()
        assert result == "Y" * 200000

    @pytest.mark.asyncio
    async def test_mixed_normal_and_long_lines(self) -> None:
        """正常行 + 超长行 + 正常行混合 → 行边界不混淆、全部保留。"""
        result = await BashFunc(
            command="python3 -c \"print('first'); print('M' * 150000); print('last')\""
        ).execute()
        lines = result.split('\n')
        assert lines[0] == "first"
        assert lines[1] == "M" * 150000
        assert lines[2] == "last"


class TestPtyEioAsEofProtocol:
    """_PtyEioAsEofProtocol：EIO 归一化为 EOF（缓冲剩余数据可继续消费）。"""

    def test_subclass_of_stream_reader_protocol(self) -> None:
        assert issubclass(_PtyEioAsEofProtocol, asyncio.StreamReaderProtocol)

    @pytest.mark.asyncio
    async def test_eio_becomes_eof_after_buffer_consumed(self) -> None:
        """EIO 到达后：缓冲中未消费的行仍可读到，随后正常 EOF（不抛异常）。"""
        reader = asyncio.StreamReader()
        proto = _PtyEioAsEofProtocol(reader)
        # 模拟：数据已到达缓冲（line1 已被消费），line2 未消费时 slave 关闭→EIO
        reader.feed_data(b"line2\r\nline3\r\n")
        proto.connection_lost(OSError(errno.EIO, "Input/output error"))
        # 缓冲剩余数据先消费完
        assert await reader.readline() == b"line2\r\n"
        assert await reader.readline() == b"line3\r\n"
        # 然后正常 EOF（不是抛 EIO）
        assert await reader.readline() == b""

    @pytest.mark.asyncio
    async def test_real_error_still_propagated(self) -> None:
        """非 EIO 的真实错误仍 set_exception（不吞掉其他异常）。"""
        reader = asyncio.StreamReader()
        proto = _PtyEioAsEofProtocol(reader)
        proto.connection_lost(OSError(errno.EPIPE, "Broken pipe"))
        with pytest.raises(OSError):
            await reader.readline()
