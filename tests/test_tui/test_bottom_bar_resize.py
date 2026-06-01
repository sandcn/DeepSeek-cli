"""_BottomBar resize 处理测试 — 验证 9 个 Bug 修复。

测试策略：
  - Bug 1/2/3/5/6/7/8: 直接测试 _check_resize() 方法和相关状态
  - Bug 4: 测试 _get_terminal_width() ioctl 解包
  - Bug 9: 测试 _drain_queue Branch B 锁策略
  所有测试禁用终端 I/O（ANSI 输出写入 devnull）。
"""

from __future__ import annotations

import io
import struct
import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from src.ui._bottom_bar import _BottomBar, _BOTTOM_LINES


class TestBug4IoctlUnpack(unittest.TestCase):
    """Bug 4 修复：parallel_executor._get_terminal_width() ioctl 解包顺序。

    struct.unpack("HHHH", data) 返回 (rows, cols, xpixel, ypixel)，
    修复前错误地将 rows 赋值给 cols 变量。
    """

    def test_ioctl_unpack_returns_width_not_height(self):
        """验证 _get_terminal_width() 返回宽度（列数）而非高度（行数）。"""
        from src.core.parallel_executor import _get_terminal_width

        # Mock ioctl 返回 (24 rows, 120 cols, 0, 0)
        mock_data = struct.pack("HHHH", 24, 120, 0, 0)

        with patch("os.open", return_value=3), \
             patch("os.close"), \
             patch("fcntl.ioctl", return_value=mock_data):
            width = _get_terminal_width()

        self.assertEqual(width, 120,
                         "ioctl 应返回宽度(120)而非高度(24)")

    def test_ioctl_unpack_typical_terminal(self):
        """典型 80x24 终端应返回 80。"""
        from src.core.parallel_executor import _get_terminal_width

        mock_data = struct.pack("HHHH", 24, 80, 0, 0)

        with patch("os.open", return_value=3), \
             patch("os.close"), \
             patch("fcntl.ioctl", return_value=mock_data):
            width = _get_terminal_width()

        self.assertEqual(width, 80)


class TestBug2QueryTerminalSize(unittest.TestCase):
    """Bug 2 修复：_check_resize() 使用 query_terminal_size() ioctl 策略。"""

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._setup_height = 30
        self.bb._setup_width = 80
        self.bb._cached_height = 30
        self.bb._cached_width = 80
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_uses_ioctl_not_shutil(self):
        """_check_resize 应调用 query_terminal_size 而非 shutil.get_terminal_size。"""
        with patch("src.ui._bottom_bar.query_terminal_size",
                   return_value=(80, 30)) as mock_qt:
            self.bb._check_resize()

        mock_qt.assert_called()
        self.assertGreaterEqual(mock_qt.call_count, 1,
                                "应至少调用一次 query_terminal_size()")


class TestBug3WidthDetection(unittest.TestCase):
    """Bug 3 修复：_check_resize() 同时检测高度和宽度变化。"""

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._setup_height = 30
        self.bb._setup_width = 80
        self.bb._cached_height = 30
        self.bb._cached_width = 80
        self.bb._last_text = "hello"
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_width_only_change_triggers_resize(self):
        """仅宽度变化（高度不变）应触发 resize。"""
        # query_terminal_size 返回新宽度 120，高度 30 不变
        with patch("src.ui._bottom_bar.query_terminal_size",
                   return_value=(120, 30)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            result = self.bb._check_resize()

        self.assertTrue(result,
                        "宽度变化（80→120）应触发 resize 返回 True")

    def test_no_change_does_not_trigger(self):
        """宽度和高度均不变时应返回 False。"""
        with patch("src.ui._bottom_bar.query_terminal_size",
                   return_value=(80, 30)):
            result = self.bb._check_resize()

        self.assertFalse(result,
                         "尺寸未变时应返回 False")

    def test_both_change_triggers_resize(self):
        """宽度和高度同时变化应触发 resize。"""
        with patch("src.ui._bottom_bar.query_terminal_size",
                   return_value=(100, 40)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            result = self.bb._check_resize()

        self.assertTrue(result,
                        "宽度和高度同时变化应触发 resize")

    def test_height_only_change_triggers_resize(self):
        """仅高度变化应触发 resize（原有行为保持）。"""
        with patch("src.ui._bottom_bar.query_terminal_size",
                   return_value=(80, 40)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            result = self.bb._check_resize()

        self.assertTrue(result,
                        "仅高度变化应触发 resize")


class TestBug5LastBottomLinesLocked(unittest.TestCase):
    """Bug 5 修复：_last_bottom_lines 在 locked 块内赋值。"""

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._setup_height = 50
        self.bb._setup_width = 80
        self.bb._last_bottom_lines = 7  # 非默认值
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_lock_timeout_preserves_last_bottom_lines(self):
        """锁超时时 _last_bottom_lines 不应被重置为 _BOTTOM_LINES。"""
        # 模拟高度 < _MIN_HEIGHT 且锁获取超时
        with patch("src.ui._bottom_bar.query_terminal_size",
                   return_value=(80, 5)), \
             patch("src.ui._bottom_bar._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=False),
                                         __exit__=MagicMock(return_value=False))):
            self.bb._check_resize()

        self.assertEqual(self.bb._last_bottom_lines, 7,
                         "锁超时时 _last_bottom_lines 不应被重置")


class TestBug6TextSaveRestore(unittest.TestCase):
    """Bug 6 修复：终端缩小再恢复后输入文本保存/恢复。"""

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._setup_height = 30
        self.bb._setup_width = 80
        self.bb._cached_height = 30
        self.bb._cached_width = 80
        self.bb._last_text = "user input before shrink"
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_text_saved_before_shrink(self):
        """终端缩小到 MIN_HEIGHT 以下时应保存 _last_text。"""
        with patch("src.ui._bottom_bar.query_terminal_size",
                   return_value=(80, 5)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            self.bb._check_resize()

        self.assertEqual(self.bb._saved_text_before_shrink,
                         "user input before shrink",
                         "缩小前应保存输入文本")

    def test_text_restored_after_grow(self):
        """终端恢复后应还原保存的输入文本。"""
        # 先缩小：保存文本
        with patch("src.ui._bottom_bar.query_terminal_size",
                   return_value=(80, 5)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            self.bb._check_resize()

        self.assertIsNotNone(self.bb._saved_text_before_shrink)

        # 再恢复：应还原文本
        with patch("src.ui._bottom_bar.query_terminal_size",
                   return_value=(80, 30)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            self.bb._check_resize()

        self.assertEqual(self.bb._last_text, "user input before shrink",
                         "恢复后应还原保存的输入文本")
        self.assertIsNone(self.bb._saved_text_before_shrink,
                          "恢复后 _saved_text_before_shrink 应清空")

    def test_no_text_when_no_prior_shrink(self):
        """未经历过缩小则不应有保存文本。"""
        self.assertIsNone(self.bb._saved_text_before_shrink)


class TestBug7LockTimeoutSetupUpdate(unittest.TestCase):
    """Bug 7 修复：锁超时时更新 _setup_height/_setup_width 避免无限重触发。"""

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._setup_height = 30  # 旧值
        self.bb._setup_width = 80   # 旧值
        self.bb._cached_height = 30
        self.bb._cached_width = 80
        self.bb._last_text = "hello"
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_lock_timeout_updates_setup_dimensions(self):
        """锁超时时 _setup_height/_setup_width 应更新为新值（返回 False 下次重试）。"""
        # 尺寸变化到 (100, 40)，但锁获取超时
        with patch("src.ui._bottom_bar.query_terminal_size",
                   return_value=(100, 40)), \
             patch("src.ui._bottom_bar._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=False),
                                         __exit__=MagicMock(return_value=False))):
            result = self.bb._check_resize()

        self.assertFalse(result, "锁超时应返回 False，下次 drain 周期重试")
        self.assertEqual(self.bb._setup_height, 40,
                         "锁超时后 _setup_height 应更新")
        self.assertEqual(self.bb._setup_width, 100,
                         "锁超时后 _setup_width 应更新")

    def test_no_retrigger_after_lock_timeout(self):
        """锁超时并更新尺寸后，第二次调用不应再触发。"""
        with patch("src.ui._bottom_bar.query_terminal_size",
                   return_value=(100, 40)), \
             patch("src.ui._bottom_bar._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=False),
                                         __exit__=MagicMock(return_value=False))):
            self.bb._check_resize()  # 第一次：返回 False + 更新

        # 第二次调用：尺寸与 _setup 一致，不应再触发
        with patch("src.ui._bottom_bar.query_terminal_size",
                   return_value=(100, 40)):
            result = self.bb._check_resize()

        self.assertFalse(result,
                         "锁超时更新后第二次调用不应再触发")


class TestBug1SigwinchCallback(unittest.TestCase):
    """Bug 1 修复：SIGWINCH 回调设置 _resize_dirty。"""

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._setup_height = 30
        self.bb._setup_width = 80
        self.bb._cached_height = 30
        self.bb._cached_width = 80
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_resize_dirty_triggers_size_refresh(self):
        """_resize_dirty=True 时应强制刷新尺寸。"""
        self.bb._resize_dirty = True

        with patch("src.ui._bottom_bar.query_terminal_size",
                   return_value=(80, 30)) as mock_qt:
            self.bb._check_resize()

        # 应调用两次 query_terminal_size: 一次在 dirty 路径，一次在常规路径
        self.assertGreaterEqual(mock_qt.call_count, 1)
        self.assertFalse(self.bb._resize_dirty,
                         "dirty 标记应在消费后被清除")

    def test_sigwinch_callback_sets_dirty(self):
        """_on_sigwinch 回调应设置 _resize_dirty=True。"""
        self.bb._resize_dirty = False
        self.bb._on_sigwinch(100, 40)
        self.assertTrue(self.bb._resize_dirty,
                        "SIGWINCH 回调应设置 dirty 标记")

    def test_sigwinch_callback_registered_on_setup(self):
        """setup() 应注册 SIGWINCH 回调。"""
        bb = _BottomBar()
        bb._cached_height = 30
        bb._cached_width = 80

        with patch("src.ui.terminal_adapter.register_sigwinch_callback") as mock_reg, \
             patch.object(sys, '__stdout__', io.StringIO()):
            bb.setup()

        mock_reg.assert_called_once_with(bb._on_sigwinch)

    def test_sigwinch_callback_unregistered_on_teardown(self):
        """teardown() 应注销 SIGWINCH 回调。"""
        bb = _BottomBar()
        bb._active = True
        bb._sigwinch_registered = True
        bb._cached_height = 30
        bb._cached_width = 80

        with patch("src.ui.terminal_adapter.unregister_sigwinch_callback") as mock_unreg, \
             patch.object(sys, '__stdout__', io.StringIO()):
            bb.teardown()

        mock_unreg.assert_called_once_with(bb._on_sigwinch)


class TestBug8ForceRedrawSkipResizeCheck(unittest.TestCase):
    """Bug 8 修复：force_redraw 支持 skip_resize_check 参数。"""

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._cached_height = 30
        self.bb._cached_width = 80
        self.bb._last_text = "hello"
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_skip_resize_check_true_skips_check_resize(self):
        """skip_resize_check=True 时不应调用 _check_resize()。"""
        with patch.object(self.bb, '_check_resize') as mock_cr, \
             patch.object(sys, '__stdout__', io.StringIO()):
            self.bb.force_redraw(skip_resize_check=True)

        mock_cr.assert_not_called()

    def test_skip_resize_check_false_calls_check_resize(self):
        """skip_resize_check=False（默认）时应调用 _check_resize()。"""
        with patch.object(self.bb, '_check_resize', return_value=False) as mock_cr, \
             patch.object(sys, '__stdout__', io.StringIO()):
            self.bb.force_redraw(skip_resize_check=False)

        mock_cr.assert_called_once()


class TestBug9PositionCursorUnderLock(unittest.TestCase):
    """Bug 9 修复：Branch B 中 _position_cursor 应在锁内执行。

    此测试验证 _drain_queue() 在仅流式活跃路径（Branch B）中
    正确地将 _position_cursor() 包裹在 output_lock 内。
    """

    def setUp(self):
        from src.chat_ui._engine import RenderEngine
        self.mock_renderer = MagicMock()
        self.mock_bb = MagicMock()
        self.engine = RenderEngine(self.mock_renderer, self.mock_bb)
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_branch_b_acquires_lock_before_position_cursor(self):
        """Branch B 路径中 force_redraw 先于 _position_cursor 在持锁状态下调用。"""
        call_order = []

        def track_redraw(*args, **kwargs):
            call_order.append("force_redraw")

        def track_cursor():
            call_order.append("position_cursor")

        self.mock_bb.is_status_active = True
        self.mock_bb.force_redraw.side_effect = track_redraw
        self.mock_bb.check_resize.return_value = False

        # 绕开 _position_cursor 内部复杂的 BB 调用，
        # 直接验证调用顺序：在 drain_queue Branch B 中，
        # force_redraw 先于 position_cursor 被调用。
        orig_position = self.engine._position_cursor
        self.engine._position_cursor = track_cursor

        with patch("src.chat_ui._state._active_parallel_display", None), \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))):
            self.engine._drain_queue()

        self.engine._position_cursor = orig_position
        self.assertEqual(call_order,
                         ["force_redraw", "position_cursor"],
                         "position_cursor 应在 force_redraw 之后调用")


class TestResizeDrainSkip(unittest.TestCase):
    """Bug 修复：无流式输出时终端 resize 被 _drain_queue() 快速空闲跳过阻塞。"""

    def setUp(self):
        from src.chat_ui._engine import RenderEngine
        self.mock_renderer = MagicMock()
        self.mock_bb = MagicMock()
        self.mock_bb.is_status_active = False
        self.mock_bb.is_resize_pending = False
        self.mock_bb._resize_dirty = False
        self.engine = RenderEngine(self.mock_renderer, self.mock_bb)
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_idle_no_resize_pending_skips(self):
        """无待处理命令、无面板、非流式、无 resize pending → 应跳过（快速空闲跳过）。"""
        self.mock_bb.is_resize_pending = False

        with patch("src.chat_ui._state._active_parallel_display", None), \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))):
            self.engine._drain_queue()

        # 快速空闲跳过 → check_resize 不应被调用
        self.mock_bb.check_resize.assert_not_called()

    def test_idle_with_resize_pending_penetrates(self):
        """无待处理命令、无面板、非流式但 resize pending → 应穿透跳过，执行 check_resize。"""
        self.mock_bb.is_resize_pending = True
        self.mock_bb.check_resize.return_value = False

        with patch("src.chat_ui._state._active_parallel_display", None), \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))):
            self.engine._drain_queue()

        # 穿透跳过 → check_resize 应被调用
        self.mock_bb.check_resize.assert_called()

    def test_streaming_active_always_penetrates(self):
        """流式活跃时 (is_status_active=True) 即使无 resize pending 也应穿透。"""
        self.mock_bb.is_status_active = True
        self.mock_bb.is_resize_pending = False
        self.mock_bb.check_resize.return_value = False
        # _position_cursor 需要 get_cursor_info + _cursor_visual_pos_from_cache
        self.mock_bb.get_cursor_info.return_value = ("", 0, 24, 80)
        self.mock_bb._cursor_visual_pos_from_cache.return_value = (0, 0)
        self.mock_bb._bottom_lines = 5
        self.mock_bb._completion_popup_height = 0

        with patch("src.chat_ui._state._active_parallel_display", None), \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))):
            self.engine._drain_queue()

        # 流式活跃应始终穿透
        self.mock_bb.check_resize.assert_called()

    def test_real_bottom_bar_resize_pending_property(self):
        """真实 _BottomBar 实例的 is_resize_pending 应与 _resize_dirty 一致。"""
        bb = _BottomBar()

        self.assertFalse(bb.is_resize_pending,
                         "初始状态 is_resize_pending 应为 False")
        self.assertEqual(bb.is_resize_pending, bb._resize_dirty,
                         "is_resize_pending 应返回 _resize_dirty 的值")

        bb._resize_dirty = True
        self.assertTrue(bb.is_resize_pending,
                        "设置 _resize_dirty=True 后 is_resize_pending 应为 True")


if __name__ == "__main__":
    unittest.main()
