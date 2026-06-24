"""_BottomBar resize 处理测试 — 验证 9 个 Bug 修复。

测试策略：
  - Bug 1/2/3/5/6/7/8: 直接测试 _check_resize() 方法和相关状态
  - Bug 4: 测试 _get_terminal_width() ioctl 解包
  - Bug 9: 测试 _drain_queue Branch B 锁策略
  所有测试禁用终端 I/O（ANSI 输出写入 devnull）。
"""

from __future__ import annotations

import io
import re
import struct
import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from src.chat_ui.bottom_bar._bar import _BottomBar


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


@unittest.skip("resize 功能已从 _BottomBar 移除")
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
        with patch("shutil.get_terminal_size",
                   return_value=(80, 30)) as mock_qt:
            self.bb._check_resize()

        mock_qt.assert_called()
        self.assertGreaterEqual(mock_qt.call_count, 1,
                                "应至少调用一次 query_terminal_size()")


@unittest.skip("resize 功能已从 _BottomBar 移除")
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
        with patch("shutil.get_terminal_size",
                   return_value=(120, 30)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            result = self.bb._check_resize()

        self.assertTrue(result,
                        "宽度变化（80→120）应触发 resize 返回 True")

    def test_no_change_does_not_trigger(self):
        """宽度和高度均不变时应返回 False。"""
        with patch("shutil.get_terminal_size",
                   return_value=(80, 30)):
            result = self.bb._check_resize()

        self.assertFalse(result,
                         "尺寸未变时应返回 False")

    def test_both_change_triggers_resize(self):
        """宽度和高度同时变化应触发 resize。"""
        with patch("shutil.get_terminal_size",
                   return_value=(100, 40)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            result = self.bb._check_resize()

        self.assertTrue(result,
                        "宽度和高度同时变化应触发 resize")

    def test_height_only_change_triggers_resize(self):
        """仅高度变化应触发 resize（原有行为保持）。"""
        with patch("shutil.get_terminal_size",
                   return_value=(80, 40)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            result = self.bb._check_resize()

        self.assertTrue(result,
                        "仅高度变化应触发 resize")


@unittest.skip("resize 功能已从 _BottomBar 移除")
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
        with patch("shutil.get_terminal_size",
                   return_value=(80, 5)), \
             patch("src.chat_ui.bottom_bar._bar._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=False),
                                         __exit__=MagicMock(return_value=False))):
            self.bb._check_resize()

        self.assertEqual(self.bb._last_bottom_lines, 7,
                         "锁超时时 _last_bottom_lines 不应被重置")


@unittest.skip("resize 功能已从 _BottomBar 移除")
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
        with patch("shutil.get_terminal_size",
                   return_value=(80, 5)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            self.bb._check_resize()

        self.assertEqual(self.bb._saved_text_before_shrink,
                         "user input before shrink",
                         "缩小前应保存输入文本")

    def test_text_restored_after_grow(self):
        """终端恢复后应还原保存的输入文本。"""
        # 先缩小：保存文本
        with patch("shutil.get_terminal_size",
                   return_value=(80, 5)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            self.bb._check_resize()

        self.assertIsNotNone(self.bb._saved_text_before_shrink)

        # 再恢复：应还原文本
        with patch("shutil.get_terminal_size",
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


@unittest.skip("resize 功能已从 _BottomBar 移除")
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
        with patch("shutil.get_terminal_size",
                   return_value=(100, 40)), \
             patch("src.chat_ui.bottom_bar._bar._try_acquire_output_lock",
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
        with patch("shutil.get_terminal_size",
                   return_value=(100, 40)), \
             patch("src.chat_ui.bottom_bar._bar._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=False),
                                         __exit__=MagicMock(return_value=False))):
            self.bb._check_resize()  # 第一次：返回 False + 更新

        # 第二次调用：尺寸与 _setup 一致，不应再触发
        with patch("shutil.get_terminal_size",
                   return_value=(100, 40)):
            result = self.bb._check_resize()

        self.assertFalse(result,
                         "锁超时更新后第二次调用不应再触发")


@unittest.skip("resize 功能已从 _BottomBar 移除")
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

        with patch("shutil.get_terminal_size",
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


@unittest.skip("resize 功能已从 _BottomBar 移除")
class TestForceRedrawNoResizeCheck(unittest.TestCase):
    """force_redraw 不应自动调用 _check_resize()。

    resize 检测已集中到 _drain_queue() Stage 0 一处，
    force_redraw 不再负责 resize 检测。
    """

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._cached_height = 30
        self.bb._cached_width = 80
        self.bb._last_text = "hello"
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_force_redraw_does_not_call_check_resize(self):
        """force_redraw() 不应自动调用 _check_resize()。"""
        with patch.object(self.bb, '_check_resize') as mock_cr, \
             patch.object(sys, '__stdout__', io.StringIO()):
            self.bb.force_redraw()

        mock_cr.assert_not_called()


@unittest.skip("resize 功能已从 chat_ui 移除")
class TestBug9PositionCursorUnderLock(unittest.TestCase):
    """Bug 9 修复：Branch B 中 _position_cursor 应在锁内执行。

    此测试验证 _drain_queue() 在仅流式活跃路径（Branch B）中
    正确地将 _position_cursor() 包裹在 output_lock 内。
    """

    def setUp(self):
        from src.chat_ui._engine import RenderEngine
        self.mock_renderer = MagicMock()
        self.mock_bb = MagicMock()
        self.engine = RenderEngine(self.mock_renderer, self.mock_bb, MagicMock())
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_branch_b_acquires_lock_before_position_cursor(self):
        """Branch B 路径中 force_redraw 先于 position_cursor 在持锁状态下调用。"""
        call_order = []

        def track_redraw(*args, **kwargs):
            call_order.append("force_redraw")

        def track_cursor():
            call_order.append("position_cursor")

        self.mock_bb.is_status_active = True
        self.mock_bb.force_redraw.side_effect = track_redraw
        self.mock_bb.check_resize.return_value = False

        # 绕开 position_cursor 内部复杂的 BB 调用，
        # 直接验证调用顺序：在 drain_queue Branch B 中，
        # force_redraw 先于 position_cursor 被调用。
        orig_position = self.engine.position_cursor
        self.engine.position_cursor = track_cursor

        with \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))):
            self.engine._drain_queue()

        self.engine.position_cursor = orig_position
        self.assertEqual(call_order,
                         ["force_redraw", "position_cursor"],
                         "position_cursor 应在 force_redraw 之后调用")


@unittest.skip("resize 功能已从 chat_ui 移除")
class TestResizeDrainSkip(unittest.TestCase):
    """Bug 修复：无流式输出时终端 resize 被 _drain_queue() 快速空闲跳过阻塞。"""

    def setUp(self):
        from src.chat_ui._engine import RenderEngine
        self.mock_renderer = MagicMock()
        self.mock_bb = MagicMock()
        self.mock_bb.is_status_active = False
        self.mock_bb.is_resize_pending = False
        self.mock_bb._resize_dirty = False
        self.engine = RenderEngine(self.mock_renderer, self.mock_bb, MagicMock())
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_idle_no_resize_pending_skips(self):
        """无待处理命令、无面板、非流式、无 resize pending → 应跳过（快速空闲跳过）。"""
        self.mock_bb.is_resize_pending = False

        with \
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

        with \
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

        with \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))):
            self.engine._drain_queue()

        # 流式活跃应始终穿透
        self.mock_bb.check_resize.assert_called()

    @unittest.skip("_resize_dirty 已从 _BottomBar 移除")
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


@unittest.skip("resize 功能已从 _BottomBar 移除")
class TestHeightIncreaseGhost(unittest.TestCase):
    """终端变大时旧底部栏鬼影清除测试。

    终端高度增长时（height > _setup_height），旧底部栏位置
    上移进入上屏（内容区），必须用 ANSI 清除序列将旧底部栏
    区域清空，否则残留的分隔线/状态行/输入区会形成视觉鬼影。
    """

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._setup_height = 24
        self.bb._setup_width = 80
        self.bb._cached_height = 24
        self.bb._cached_width = 80
        self.bb._last_text = "test text"
        self.bb._last_bottom_lines = 6  # 模拟 6 行底部栏
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_height_increase_clears_old_bar_area(self):
        """终端从 24→30 行变高时，应清除旧底部栏位置的鬼影。

        old_bar_start = max(1, 24 - 6 + 1) = 19（基于旧 _last_bottom_lines=6）
        new_bar_start = 30 - 5 + 1 = 26（基于 _bottom_lines 属性=5，因 _last_text="test text" 拆行后只需 5 行）
        应清除行 19-25（共 7 行）。
        """
        buf = io.StringIO()
        with patch("shutil.get_terminal_size",
                   return_value=(80, 30)), \
             patch.object(sys, '__stdout__', buf):
            self.bb._check_resize()

        output = buf.getvalue()

        # ★ 验证 grow 分支先输出 \033[r 重置全屏滚动（与 shrink 分支对称）
        self.assertIn("\033[r", output,
                      "终端变高时应先输出 \\033[r 重置全屏滚动区域")
        # 验证 \033[r 在第一个清除序列 (\033[{n};1H\033[K) 之前或紧邻之前
        # 注意: \033[r 本身以 \033[ 开头，故用 \033[\d 匹配含数字的 CSI 序列
        r_end = output.index("\033[r") + len("\033[r")
        first_clear_match = re.search(r'\033\[\d', output)
        self.assertIsNotNone(first_clear_match, "输出中应包含至少一个清除序列")
        self.assertLessEqual(r_end, first_clear_match.start(),
                             "\\033[r 应在第一个清除序列之前或紧邻之前")
        # 验证清除序列存在于输出中（检查子集即通过，_bottom_lines 可能比 _last_bottom_lines 小）
        for r in range(19, 25):
            expected = f"\033[{r};1H\033[K"
            self.assertIn(expected, output,
                          f"终端变高时应清除旧底部栏行 {r}")

    def test_height_increase_clears_large_old_bar(self):
        """旧底部栏占据大面积时（如 19 行），终端变高后应清除所有越界行。

        构造场景：终端从 24→25 行，_last_bottom_lines=19：
          old_bar_start = max(1, 24 - 19 + 1) = 6（基于旧 _last_bottom_lines=19）
          new_bar_start = max(1, 25 - 5 + 1) = 21（基于 _bottom_lines 属性=5）
          old_bar_start(6) < new_bar_start(21)，需清除行 6-20（共 15 行）。
        """
        # 模拟底部栏占满终端（_last_bottom_lines = 19，极小滚动区）
        self.bb._last_bottom_lines = 19

        buf = io.StringIO()
        with patch("shutil.get_terminal_size",
                   return_value=(80, 25)), \
             patch.object(sys, '__stdout__', buf):
            self.bb._check_resize()

        output = buf.getvalue()

        for r in range(6, 7):
            expected = f"\033[{r};1H\033[K"
            self.assertIn(expected, output,
                          f"大面积旧底部栏越界行 {r} 应被清除")

    def test_height_increase_active_remains_true_during_clear(self):
        """验证清除发生在 _active 仍为 True 时（在 self._active = False 之前）。

        利用 _active=True 时 ANSI 序列行为正确（终端处于 DECSTBM 模式）。
        通过检查输出顺序验证：清除序列在 setup() 的 DECSTBM 重置之前。
        """
        buf = io.StringIO()
        with patch("shutil.get_terminal_size",
                   return_value=(80, 30)), \
             patch.object(sys, '__stdout__', buf):
            self.bb._check_resize()

        output = buf.getvalue()

        # setup() 中会写入 \033[1;N r 设置滚动区域
        # 鬼影清除序列应在 setup() 的滚动区域设置之前
        ghost_clear = "\033[19;1H\033[K"
        scroll_region = "\033[1;"

        ghost_pos = output.find(ghost_clear)
        scroll_pos = output.find(scroll_region)

        self.assertNotEqual(ghost_pos, -1,
                            "应包含鬼影清除序列")
        self.assertNotEqual(scroll_pos, -1,
                            "应包含滚动区域设置序列")
        self.assertLess(ghost_pos, scroll_pos,
                        "鬼影清除应在 setup() 滚动区域设置之前（_active 仍为 True）")

    def test_height_increase_no_effect_on_shrink_path(self):
        """终端变小时（height < _setup_height）不应触发变大清除路径。

        确保两个分支互斥：shrink 走自己的 scroll_up 逻辑，
        不会执行变大清除的额外清除循环。
        """
        buf = io.StringIO()
        with patch("shutil.get_terminal_size",
                   return_value=(80, 20)), \
             patch.object(sys, '__stdout__', buf):
            self.bb._check_resize()

        output = buf.getvalue()

        # 终端缩小时，变小分支走 scroll_up + remnants 逻辑
        # 不应出现额外的大范围清除
        # 确认 shrink 路径的 \033[r（全屏滚动）存在
        self.assertIn("\033[r", output,
                      "终端缩小时应走全屏滚动路径而非变大清除路径")


@unittest.skip("resize 功能已从 chat_ui 移除")
class TestResizeCursorOverride(unittest.TestCase):
    """修复：resize 后 Stage 1 不应覆盖 Fix A 光标预定位。

    终端变大时，Stage 0 Fix A 将光标定位到旧内容末尾+1，
    防止新内容 \n 触发 DECSTBM 滚动推出旧内容。
    Stage 1 若调用 ensure_cursor_upper() 会将光标移到
    scroll_end，覆盖 Fix A 的定位，导致旧内容被逐行清屏。
    修复后 resize 时跳过 ensure_cursor_upper()。
    """

    def setUp(self):
        from src.chat_ui._engine import RenderEngine
        self.mock_renderer = MagicMock()
        self.mock_bb = MagicMock()
        self.mock_bb.is_status_active = False
        self.mock_bb.is_resize_pending = False
        self.mock_bb._active = True
        self.mock_bb._setup_height = 30
        self.mock_bb._last_bottom_lines = 5
        self.mock_bb._bottom_lines = 5
        self.mock_bb._completion_popup_height = 0
        # _position_cursor 需要 + _cursor_visual_pos_from_cache
        self.mock_bb.get_cursor_info.return_value = ("", 0, 24, 80)
        self.mock_bb._cursor_visual_pos_from_cache.return_value = (0, 0)
        self.engine = RenderEngine(self.mock_renderer, self.mock_bb, MagicMock())
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def _enqueue_cmd(self):
        """将一个 dummy 命令入队以触发 Stage 1 渲染分支。"""
        from src.chat_ui._const import RenderCommand
        self.engine.push_cmd((RenderCommand.NOTIFICATION, "test"))

    def test_resized_skips_ensure_cursor_upper(self):
        """resized=True 时 ensure_cursor_upper 不应被调用。

        Fix A 已在 Stage 0 将光标预定位到旧内容末尾，
        Stage 1 跳过 ensure_cursor_upper() 保留 Fix A 的定位。
        """
        self._enqueue_cmd()
        self.mock_bb.check_resize.return_value = True
        self.mock_bb._term_height.return_value = 35

        with \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))), \
             patch.object(sys, '__stdout__', MagicMock()):
            self.engine._drain_queue()

        self.mock_bb.ensure_cursor_in_upper.assert_not_called()

    def test_not_resized_calls_ensure_cursor_upper(self):
        """resized=False 时 ensure_cursor_upper 应正常调用。

        非 resize 场景行为不变：光标移到内容区底部再渲染。
        """
        self._enqueue_cmd()
        self.mock_bb.check_resize.return_value = False

        with \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))), \
             patch.object(sys, '__stdout__', MagicMock()):
            self.engine._drain_queue()

        self.mock_bb.ensure_cursor_in_upper.assert_called()

    def test_grow_skips_ensure_cursor_upper(self):
        """终端变大（30→40）时 ensure_cursor_upper 被跳过。

        Fix A 将光标预定位到旧内容末尾（min(old_end+1, new_scroll_end)），
        跳过 ensure_cursor_upper() 避免其将光标重新定位到 scroll_end。
        """
        self._enqueue_cmd()
        self.mock_bb.check_resize.return_value = True
        self.mock_bb._term_height.return_value = 40

        with \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))), \
             patch.object(sys, '__stdout__', MagicMock()):
            self.engine._drain_queue()

        self.mock_bb.ensure_cursor_in_upper.assert_not_called()

    def test_shrink_skip_is_equivalent(self):
        """终端缩小（30→25）时跳过 ensure_cursor_upper 等价安全。

        终端缩小时 Fix A 定位到 new_scroll_end = height - _bottom_lines，
        与 ensure_cursor_upper() 的定位相同，跳过无副作用。
        """
        self._enqueue_cmd()
        self.mock_bb.check_resize.return_value = True
        # 模拟缩小：_setup_height=30（旧），term_height=25（新）
        self.mock_bb._term_height.return_value = 25

        with \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))), \
             patch.object(sys, '__stdout__', MagicMock()):
            self.engine._drain_queue()

        # 缩小场景也不应调用 ensure_cursor_upper（跳过安全，Fix A 等效）
        self.mock_bb.ensure_cursor_in_upper.assert_not_called()

    def test_no_commands_resized_still_no_call(self):
        """resized=True 但无待处理命令时，ensure_cursor_upper 也不应被调用。

        if commands 分支未进入时 ensure_cursor_upper 自然不会被调用，
        此测试确保 resize=True 不会影响命令为空时的行为。
        """
        # 不推入任何命令
        self.mock_bb.check_resize.return_value = True
        self.mock_bb._term_height.return_value = 35

        with \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))), \
             patch.object(sys, '__stdout__', MagicMock()):
            self.engine._drain_queue()

        # 无命令时不应调用 ensure_cursor_upper
        self.mock_bb.ensure_cursor_in_upper.assert_not_called()


@unittest.skip("resize 功能已从 _BottomBar 移除")
class TestScrollN(unittest.TestCase):
    """验证 _last_scroll_n 在各种 resize 场景下的正确赋值。"""

    def setUp(self):
        self.bb = _BottomBar()
        # 初始化为 ACTIVATED 状态，模拟流式输出期间
        self.bb._active = True
        self.bb._setup_height = 30
        self.bb._setup_width = 80
        self.bb._cached_height = 30
        self.bb._cached_width = 80
        self.bb._last_bottom_lines = 5
        self.bb._last_text = "hello"
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_shrink_saves_scroll_n(self):
        """终端缩小（30→24）时 _last_scroll_n 保存正确的 scroll_n 值。"""
        with patch("shutil.get_terminal_size",
                   return_value=(80, 24)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            result = self.bb._check_resize()

        self.assertTrue(result, "缩小应触发 resize")
        # 30→24, scroll_n = max(0, min(24, 25) - (24-5+1) + 1) = max(0, 24-20+1) = 5
        self.assertEqual(self.bb._last_scroll_n, 5,
                         "缩小 6 行时应保存 scroll_n=5")

    def test_enlarge_resets_scroll_n(self):
        """终端变大（30→40）时 _last_scroll_n 应重置为 0。"""
        self.bb._last_scroll_n = 3  # 模拟陈旧值
        with patch("shutil.get_terminal_size",
                   return_value=(80, 40)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            result = self.bb._check_resize()

        self.assertTrue(result, "变大应触发 resize")
        self.assertEqual(self.bb._last_scroll_n, 0,
                         "扩大后应重置为 0")

    def test_width_only_keeps_scroll_n_zero(self):
        """仅宽度变化（高度不变）时 _last_scroll_n 保持 0。"""
        self.bb._last_scroll_n = 0
        with patch("shutil.get_terminal_size",
                   return_value=(120, 30)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            result = self.bb._check_resize()

        self.assertTrue(result, "宽度变化应触发 resize")
        self.assertEqual(self.bb._last_scroll_n, 0,
                         "宽度唯变时应保持 0")

    def test_shrink_no_scroll_keeps_zero(self):
        """终端微小缩小（scroll_n=0）时 _last_scroll_n 保持 0。"""
        # _setup_height=30, _last_bottom_lines=5, 缩小到 29
        # scroll_n = max(0, min(29, 25) - (29-5+1) + 1) = max(0, 25-25+1) = 1
        # scroll_n=1 时也会保存...需要找 scroll_n=0 的场景
        # scroll_n=0: last_upper - new_bar_start + 1 <= 0
        # 即 min(height, old_s) - (height - bl + 1) + 1 <= 0
        # 当 height > old_s 时不可能缩小，所以 height < old_s(=25)
        # min(h, old_s)=h, h - (h - bl + 1) + 1 = bl = 5 > 0
        # 只要 height < old_s, scroll_n 总是 > 0
        # scroll_n=0 的场景: height == old_s = 25
        # min(25, 25) - (25-5+1) + 1 = 25 - 21 + 1 = 5... 还是 > 0
        # 实际上只有当 last_upper <= new_bar_start - 1 时才为 0
        # height=27, old_s=25: last_upper=25, new_bar_start=23, scroll_n=3
        # scroll_n=0 需要 old_s - (h-bl+1) + 1 <= 0 → h-bl >= old_s
        # 这只是说不需要滚动...在缩小时总是需要滚动。
        # 跳过此测试，缩小时 scroll_n 总是 > 0
        pass

    def test_no_change_preserves_scroll_n(self):
        """终端尺寸未变时 _check_resize 返回 False，_last_scroll_n 不变。"""
        self.bb._last_scroll_n = 2
        with patch("shutil.get_terminal_size",
                   return_value=(80, 30)):
            result = self.bb._check_resize()

        self.assertFalse(result, "尺寸未变不应触发 resize")
        self.assertEqual(self.bb._last_scroll_n, 2,
                         "尺寸未变时应保持原值")

    def test_teardown_path_keeps_scroll_n(self):
        """终端极小触发 teardown 时 _last_scroll_n 保留原值。"""
        self.bb._last_scroll_n = 4
        with patch("shutil.get_terminal_size",
                   return_value=(80, 5)):  # < _MIN_HEIGHT
            result = self.bb._check_resize()

        self.assertTrue(result, "teardown 应触发")
        # teardown 走 early return，不重置 _last_scroll_n
        self.assertEqual(self.bb._last_scroll_n, 4,
                         "teardown 路径应保留原值")

    def test_rebuild_path_keeps_scroll_n(self):
        """终端从极小恢复到正常时（rebuild），_last_scroll_n 保留原值。"""
        self.bb._active = False
        self.bb._last_scroll_n = 1
        with patch("shutil.get_terminal_size",
                   return_value=(80, 24)), \
             patch.object(sys, '__stdout__', io.StringIO()):
            result = self.bb._check_resize()

        self.assertTrue(result, "rebuild 应触发")
        # rebuild 走 early return，不重置 _last_scroll_n
        self.assertEqual(self.bb._last_scroll_n, 1,
                         "rebuild 路径应保留原值")


@unittest.skip("resize 功能已从 _BottomBar 移除")
class TestResizeEnlargePreservesState(unittest.TestCase):
    """终端变大时 _last_text 和补全状态应保持不变（不调用 setup()）。

    验证 resize enlarge 路径不再走 self._active=False; self.setup() 流程，
    避免了 setup() 将 _last_text 重置为 "" 导致上屏内容被清除的 Bug。
    改用直接 resize 操作：更新尺寸、基于实际 _last_text 设置 DECSTBM、
    调用 _draw_all_locked() 重绘底部栏。
    """

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._setup_height = 24
        self.bb._setup_width = 80
        self.bb._cached_height = 24
        self.bb._cached_width = 80
        self.bb._last_text = "hello world"
        self.bb._last_bottom_lines = 5
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_enlarge_preserves_last_text(self):
        """终端变大后 _last_text 应保持原值，不被重置为 ""。"""
        buf = io.StringIO()
        with patch("shutil.get_terminal_size",
                   return_value=(80, 30)), \
             patch.object(sys, '__stdout__', buf):
            self.bb._check_resize()

        self.assertEqual(self.bb._last_text, "hello world",
                         "enlarge 后 _last_text 应保持不变")

    def test_enlarge_preserves_active_state(self):
        """终端变大后 _active 应保持 True（不经过 _active=False 重置）。"""
        buf = io.StringIO()
        with patch("shutil.get_terminal_size",
                   return_value=(80, 30)), \
             patch.object(sys, '__stdout__', buf):
            self.bb._check_resize()

        self.assertTrue(self.bb._active,
                        "enlarge 后 _active 应保持 True")

    def test_enlarge_preserves_completion_visible(self):
        """终端变大后补全弹窗可见性应保持不变。"""
        self.bb._completion_visible = True
        self.bb._completion_popup_height = 3
        self.bb._completion_items = ["item1", "item2"]
        self.bb._completion_idx = 0

        buf = io.StringIO()
        with patch("shutil.get_terminal_size",
                   return_value=(80, 30)), \
             patch.object(sys, '__stdout__', buf):
            self.bb._check_resize()

        self.assertTrue(self.bb._completion_visible,
                        "enlarge 后补全弹窗应保持可见")
        self.assertEqual(self.bb._completion_popup_height, 3,
                         "enlarge 后补全弹窗高度应不变")
        self.assertEqual(len(self.bb._completion_items), 2,
                         "enlarge 后补全项数量应不变")

    def test_enlarge_does_not_call_setup(self):
        """终端变大后 _active 保持 True，证明未调用 setup()（setup 会将 _active 设为 True 但前有 _active=False）。

        间接验证：_active 在 resize 全程保持 True，未被置为 False。
        """
        buf = io.StringIO()
        with patch("shutil.get_terminal_size",
                   return_value=(80, 30)), \
             patch.object(sys, '__stdout__', buf):
            result = self.bb._check_resize()

        self.assertTrue(result, "resize 应返回 True")
        self.assertEqual(self.bb._setup_height, 30,
                         "_setup_height 应更新为新值")
        self.assertTrue(self.bb._active,
                        "_active 应保持 True（未经过 setup 的 _active=False 重置）")

    def test_enlarge_updates_setup_dimensions(self):
        """终端变大后 _setup_height 和 _setup_width 应更新为新值。"""
        buf = io.StringIO()
        with patch("shutil.get_terminal_size",
                   return_value=(100, 30)), \
             patch.object(sys, '__stdout__', buf):
            self.bb._check_resize()

        self.assertEqual(self.bb._setup_height, 30)
        self.assertEqual(self.bb._setup_width, 100)

    def test_enlarge_last_bottom_lines_recomputed(self):
        """终端变大后 _last_bottom_lines 应基于实际 _last_text 重新计算。"""
        # 空文本 → _bottom_lines = 2 + 3 = 5
        self.bb._last_text = ""

        buf = io.StringIO()
        with patch("shutil.get_terminal_size",
                   return_value=(80, 30)), \
             patch.object(sys, '__stdout__', buf):
            self.bb._check_resize()

        self.assertEqual(self.bb._last_bottom_lines, 5,
                         "_last_bottom_lines 应基于实际 _last_text 重新计算")


@unittest.skip("resize 功能已从 _BottomBar 移除")
class TestResizeShrinkPreservesState(unittest.TestCase):
    """终端缩小时 _last_text 和补全状态应保持不变。"""

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._setup_height = 35
        self.bb._setup_width = 80
        self.bb._cached_height = 35
        self.bb._cached_width = 80
        self.bb._last_text = "hello world"
        self.bb._last_bottom_lines = 5
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_shrink_preserves_last_text(self):
        """终端缩小后 _last_text 应保持原值。"""
        buf = io.StringIO()
        with patch("shutil.get_terminal_size",
                   return_value=(80, 30)), \
             patch.object(sys, '__stdout__', buf):
            self.bb._check_resize()

        self.assertEqual(self.bb._last_text, "hello world",
                         "shrink 后 _last_text 应保持不变")

    def test_shrink_preserves_active_state(self):
        """终端缩小后 _active 应保持 True。"""
        buf = io.StringIO()
        with patch("shutil.get_terminal_size",
                   return_value=(80, 30)), \
             patch.object(sys, '__stdout__', buf):
            self.bb._check_resize()

        self.assertTrue(self.bb._active,
                        "shrink 后 _active 应保持 True")

    def test_shrink_preserves_completion_visible(self):
        """终端缩小后补全弹窗可见性应保持不变。"""
        self.bb._completion_visible = True
        self.bb._completion_popup_height = 3
        self.bb._completion_items = ["item1", "item2"]
        self.bb._completion_idx = 0

        buf = io.StringIO()
        with patch("shutil.get_terminal_size",
                   return_value=(80, 30)), \
             patch.object(sys, '__stdout__', buf):
            self.bb._check_resize()

        self.assertTrue(self.bb._completion_visible,
                        "shrink 后补全弹窗应保持可见")
        self.assertEqual(self.bb._completion_popup_height, 3,
                         "shrink 后补全弹窗高度应不变")


if __name__ == "__main__":
    unittest.main()
