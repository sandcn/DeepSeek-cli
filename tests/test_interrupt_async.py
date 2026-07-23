"""测试 src/api/interrupt_async.py 模块

覆盖内容：
  1. request_interrupt_async → is_interrupted_async 返回 True
  2. reset_interrupt_async（先 set 再 reset）→ is_interrupted_async 返回 False
  3. reset_interrupt_async 调用 _flush_stdin（mock 验证）
  4. is_interrupted() 同步桥接函数行为与 is_interrupted_async() 一致
  5. 多次 request_interrupt_async 幂等（多次 set 后 is_set 仍为 True）
"""

from unittest.mock import patch

import pytest


class TestInterruptAsync:
    """Async 全局中断信号测试"""

    @pytest.fixture(autouse=True)
    def _reset_before_each(self):
        """每个测试前复位全局中断信号，保证测试隔离"""
        from src.api.interrupt_async import _interrupted

        _interrupted.clear()
        yield
        _interrupted.clear()

    # ── 1. request → is_set ─────────────────────────────────

    async def test_request_then_is_set(self):
        """调用 request_interrupt_async 后 is_interrupted_async 返回 True"""
        from src.api.interrupt_async import (
            is_interrupted_async,
            request_interrupt_async,
        )

        request_interrupt_async()
        result = await is_interrupted_async()

        assert result is True

    # ── 2. set → reset → is_set ─────────────────────────────

    async def test_reset_after_set_clears(self):
        """先 set 再 reset，is_interrupted_async 返回 False"""
        from src.api.interrupt_async import (
            is_interrupted_async,
            request_interrupt_async,
            reset_interrupt_async,
        )

        request_interrupt_async()
        assert await is_interrupted_async() is True  # 确认已置位

        with patch("src.api.interrupt_async.flush_stdin"):
            reset_interrupt_async()

        assert await is_interrupted_async() is False

    # ── 3. reset 调用 flush_stdin ──────────────────────────

    async def test_reset_calls_flush_stdin(self):
        """reset_interrupt_async 内部调用 flush_stdin"""
        from src.api.interrupt_async import reset_interrupt_async

        with patch("src.api.interrupt_async.flush_stdin") as mock_flush:
            reset_interrupt_async()

        mock_flush.assert_called_once()

    # ── 4. 同步桥接 is_interrupted() ────────────────────────

    async def test_sync_bridge_matches_async(self):
        """is_interrupted() 同步桥接与 is_interrupted_async() 行为一致"""
        from src.api.interrupt_async import (
            is_interrupted,
            is_interrupted_async,
            request_interrupt_async,
            reset_interrupt_async,
        )

        # 初始状态：两者都返回 False
        assert is_interrupted() is False
        assert await is_interrupted_async() is False

        # 请求中断后：两者都返回 True
        request_interrupt_async()
        assert is_interrupted() is True
        assert await is_interrupted_async() is True

        # 复位后：两者都返回 False
        with patch("src.api.interrupt_async.flush_stdin"):
            reset_interrupt_async()
        assert is_interrupted() is False
        assert await is_interrupted_async() is False

    # ── 5. 多次 request 幂等 ────────────────────────────────

    async def test_request_idempotent(self):
        """多次 request_interrupt_async 后 is_set 仍为 True（幂等）"""
        from src.api.interrupt_async import (
            is_interrupted_async,
            request_interrupt_async,
        )

        # 连续多次 set
        request_interrupt_async()
        request_interrupt_async()
        request_interrupt_async()

        assert await is_interrupted_async() is True

    # ── 6. wait_for_interrupt_async 触发 ─────────────────────

    async def test_wait_for_interrupt_async_triggers(self):
        """wait_for_interrupt_async 在中断信号置位后返回 True"""
        from src.api.interrupt_async import (
            request_interrupt_async,
            wait_for_interrupt_async,
        )

        request_interrupt_async()
        result = await wait_for_interrupt_async(timeout=5.0)

        assert result is True

    # ── 7. wait_for_interrupt_async 超时 ─────────────────────

    async def test_wait_for_interrupt_async_timeout(self):
        """wait_for_interrupt_async 在超时后返回 False"""
        from src.api.interrupt_async import wait_for_interrupt_async

        result = await wait_for_interrupt_async(timeout=0.1)

        assert result is False


class TestFlushStdinTcflush:
    """flush_stdin() tcflush 参数正确性测试。

    验证 Bug C 修复：tcflush 第一个参数应为 sys.stdin.fileno() (int fd)
    而非 sys.stdin (file object)。

    使用 patch.object 而非 patch.dict 替换 sys.modules：
    patch.dict 会替换整个 termios 模块对象，导致依赖 termios 的
    其他模块（如 tty）无法导入真实的 TCSAFLUSH 等常量。
    patch.object 仅替换 tcflush 函数，不影响 termios 模块其余部分。
    """

    @pytest.fixture(autouse=True)
    def _ensure_termios(self):
        """确保 termios 已导入 sys.modules，供 patch.object 使用。"""
        try:
            import termios as _termios  # noqa: F401
        except ImportError:
            pytest.skip("termios 在当前平台不可用")

    def test_tcflush_uses_fileno(self):
        """调用 flush_stdin() 时 tcflush 收到 int fd 参数而非 file object。

        Mock 策略：
        - patch.object(termios, 'tcflush') 仅替换 tcflush 函数
        - patch select.select 返回空列表 → while 循环快速退出
        - 调用 flush_stdin() 后验证 tcflush 被调用且第一个参数为 int
        """
        import termios as _real_termios
        from unittest.mock import MagicMock, patch

        mock_select = MagicMock()
        mock_select.select = MagicMock(return_value=([], [], []))

        with patch.object(_real_termios, 'tcflush') as mock_tcflush, \
             patch('src.api.interrupt_async.select', mock_select):
            from src.api.interrupt_async import flush_stdin
            flush_stdin()

        # 验证 tcflush 被调用
        mock_tcflush.assert_called_once()
        # 验证第一个参数为 int（sys.stdin.fileno() 的返回值）
        call_args = mock_tcflush.call_args[0]
        assert isinstance(call_args[0], int), \
            f"tcflush 第一个参数应为 int fd，实际类型: {type(call_args[0])}"
        # 验证第二个参数为 TCIFLUSH
        assert call_args[1] == _real_termios.TCIFLUSH, \
            f"tcflush 第二个参数应为 TCIFLUSH，实际: {call_args[1]}"

    def test_tcflush_exception_is_swallowed(self):
        """tcflush 抛异常时 flush_stdin() 不崩溃。

        验证 try/except Exception 兜底：tcflush 失败时函数正常返回，
        不会将异常传播到调用方。
        """
        import termios as _real_termios
        from unittest.mock import MagicMock, patch

        mock_select = MagicMock()
        mock_select.select = MagicMock(return_value=([], [], []))

        with patch.object(_real_termios, 'tcflush',
                          side_effect=OSError("Bad fd")) as mock_tcflush, \
             patch('src.api.interrupt_async.select', mock_select):
            from src.api.interrupt_async import flush_stdin
            # 不应抛出异常
            flush_stdin()

        # tcflush 仍被调用（只是抛了异常被吞掉）
        mock_tcflush.assert_called_once()
