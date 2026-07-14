"""run_bottom_bar_selection KEY_ENTER 处理测试

验证 run_bottom_bar_selection 在收到 KEY_ENTER 序列键
和普通 Enter 字符时均能正确确认选择。

测试策略：
  - Mock Blessed Terminal.inkey() 返回模拟 Keystroke 对象
  - Mock src.chat_ui.get_active_chat_ui、_BottomBar、sys.stdin、os.isatty
  - 验证 KEY_ENTER(343)、'\\r'、'\\n' 三种 Enter 形式均返回 confirmed
  - 验证 _completion_idx 越界时 Enter 自动 clamp 到 0 后确认
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from src.tui.widgets.bottom_bar.selection import (
    run_bottom_bar_selection,
    _run_selection_raw,
    _is_cygwin,
    _KEY_ENTER,
    _KEY_UP,
    _KEY_DOWN,
    _KEY_ESCAPE,
)

# 统一的 patch 目标
_CHAT_UI_PATCH = "src.tui.consumer.state.get_active_chat_ui"
_TERMINAL_PATCH = "src.tui.widgets.bottom_bar.selection.get_terminal"


class _MockKeystroke:
    """模拟 Blessed Keystroke 对象。"""

    def __init__(self, key=None, is_sequence=False, code=None, name=""):
        self._key = key
        self._is_sequence = is_sequence
        self._code = code
        self.name = name

    def __eq__(self, other):
        if isinstance(other, _MockKeystroke):
            return self._key == other._key
        return self._key == other

    def __hash__(self):
        return hash(self._key)

    def __repr__(self):
        return f"_MockKeystroke({self._key!r})"

    @property
    def is_sequence(self):
        return self._is_sequence

    @property
    def code(self):
        return self._code

    def __str__(self):
        return str(self._key) if self._key else ""


class TestRunBottomBarSelectionEnter(unittest.TestCase):
    """验证 run_bottom_bar_selection 对各类 Enter 按键的处理。"""

    def setUp(self):
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def _make_mock_chat_ui(self):
        """创建模拟的 ChatUI，包含活跃的 _BottomBar。"""
        mock_bb = MagicMock()
        mock_bb._active = True
        mock_bb._completion_idx = 0  # 默认选中第一条
        mock_bb.show_completions.return_value = None

        mock_chat_ui = MagicMock()
        mock_chat_ui._bottom_bar = mock_bb
        return mock_chat_ui

    def _make_mock_terminal(self, keys):
        """创建模拟 Blessed Terminal，按顺序返回 key 列表。"""
        mock_term = MagicMock()
        mock_term.__enter__ = MagicMock(return_value=mock_term)
        mock_term.__exit__ = MagicMock(return_value=False)
        mock_term.inkey.side_effect = keys
        return mock_term

    def _run_with_mocks(self, mock_chat_ui, mock_term, items, display_items,
                        initial_idx=0, title="测试"):
        """在完整 mock 环境下运行 run_bottom_bar_selection。"""
        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0

        with patch(_CHAT_UI_PATCH, return_value=mock_chat_ui), \
             patch(_TERMINAL_PATCH, return_value=mock_term), \
             patch("sys.stdin", mock_stdin), \
             patch("os.isatty", return_value=True), \
             patch.object(sys, '__stdout__', MagicMock()):
            return run_bottom_bar_selection(
                items=items,
                display_items=display_items,
                initial_idx=initial_idx,
                title=title,
            )

    # ── KEY_ENTER 序列键确认 ─────────────────────────

    def test_sequence_key_enter_confirms_selection(self):
        """KEY_ENTER(343) 序列键应确认选择。"""
        mock_chat_ui = self._make_mock_chat_ui()
        enter_key = _MockKeystroke(is_sequence=True, code=_KEY_ENTER)
        mock_term = self._make_mock_terminal([enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["item_a", "item_b", "item_c"],
            display_items=["A", "B", "C"],
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    def test_sequence_key_enter_with_nonzero_index(self):
        """KEY_ENTER 应在 _completion_idx 非 0 时正确返回索引。"""
        mock_chat_ui = self._make_mock_chat_ui()
        mock_chat_ui._bottom_bar._completion_idx = 2
        enter_key = _MockKeystroke(is_sequence=True, code=_KEY_ENTER)
        mock_term = self._make_mock_terminal([enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["item_a", "item_b", "item_c"],
            display_items=["A", "B", "C"],
            initial_idx=2,
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 2)

    # ── 非序列 Enter（'\\r', '\\n'）回归测试 ─────────

    def test_carriage_return_confirms_selection(self):
        """\\r 字符应确认选择（回归测试）。"""
        mock_chat_ui = self._make_mock_chat_ui()
        enter_key = _MockKeystroke(key='\r', is_sequence=False)
        mock_term = self._make_mock_terminal([enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["item_a", "item_b", "item_c"],
            display_items=["A", "B", "C"],
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    def test_newline_confirms_selection(self):
        """\\n 字符应确认选择（回归测试）。"""
        mock_chat_ui = self._make_mock_chat_ui()
        enter_key = _MockKeystroke(key='\n', is_sequence=False)
        mock_term = self._make_mock_terminal([enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["item_a", "item_b", "item_c"],
            display_items=["A", "B", "C"],
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    # ── KEY_ESCAPE 取消回归测试 ──────────────────────

    def test_sequence_escape_cancels(self):
        """KEY_ESCAPE(361) 序列键应取消选择。"""
        mock_chat_ui = self._make_mock_chat_ui()
        esc_key = _MockKeystroke(is_sequence=True, code=_KEY_ESCAPE)
        mock_term = self._make_mock_terminal([esc_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["item_a", "item_b", "item_c"],
            display_items=["A", "B", "C"],
        )

        self.assertEqual(result["action"], "cancel")
        self.assertIsNone(result["index"])

    # ── _completion_idx 验证 ─────────────────────────

    def test_sequence_key_enter_respects_completion_idx(self):
        """KEY_ENTER 在 _completion_idx 为 1 时返回索引 1。"""
        mock_chat_ui = self._make_mock_chat_ui()
        mock_chat_ui._bottom_bar._completion_idx = 1
        enter_key = _MockKeystroke(is_sequence=True, code=_KEY_ENTER)
        mock_term = self._make_mock_terminal([enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["a", "b", "c"],
            display_items=["A", "B", "C"],
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 1)

    # ── ↑↓ 导航回归测试 ─────────────────────────────

    def test_arrow_up_cycles_completion(self):
        """↑ 键应调用 cycle_completion(-1)。"""
        mock_chat_ui = self._make_mock_chat_ui()
        up_key = _MockKeystroke(is_sequence=True, code=_KEY_UP)
        enter_key = _MockKeystroke(is_sequence=True, code=_KEY_ENTER)
        mock_term = self._make_mock_terminal([up_key, enter_key])

        self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["a", "b", "c"],
            display_items=["A", "B", "C"],
        )

        mock_chat_ui._bottom_bar.cycle_completion.assert_called_with(-1)

    def test_arrow_down_cycles_completion(self):
        """↓ 键应调用 cycle_completion(1)。"""
        mock_chat_ui = self._make_mock_chat_ui()
        down_key = _MockKeystroke(is_sequence=True, code=_KEY_DOWN)
        enter_key = _MockKeystroke(is_sequence=True, code=_KEY_ENTER)
        mock_term = self._make_mock_terminal([down_key, enter_key])

        self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["a", "b", "c"],
            display_items=["A", "B", "C"],
        )

        mock_chat_ui._bottom_bar.cycle_completion.assert_called_with(1)

    # ── 其他序列键忽略 ───────────────────────────────

    def test_unknown_sequence_ignored(self):
        """未知序列键（如 F1=265）应被忽略，循环继续直到 Enter。"""
        mock_chat_ui = self._make_mock_chat_ui()
        unknown_key = _MockKeystroke(is_sequence=True, code=265)
        enter_key = _MockKeystroke(key='\r', is_sequence=False)
        mock_term = self._make_mock_terminal([unknown_key, enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["a", "b"],
            display_items=["A", "B"],
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    # ── '\\x1b' 取消 ─────────────────────────────────

    def test_raw_escape_cancels(self):
        """'\\x1b' 字符应取消选择。"""
        mock_chat_ui = self._make_mock_chat_ui()
        esc_key = _MockKeystroke(key='\x1b', is_sequence=False)
        mock_term = self._make_mock_terminal([esc_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["a", "b"],
            display_items=["A", "B"],
        )

        self.assertEqual(result["action"], "cancel")
        self.assertIsNone(result["index"])

    def test_sequence_key_enter_clamps_when_idx_out_of_range(self):
        """KEY_ENTER 在 _completion_idx 越界时 clamp 到 0 后 confirmed。"""
        mock_chat_ui = self._make_mock_chat_ui()
        mock_chat_ui._bottom_bar._completion_idx = 99
        enter_key = _MockKeystroke(is_sequence=True, code=_KEY_ENTER)
        mock_term = self._make_mock_terminal([enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["a", "b"],
            display_items=["A", "B"],
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    def test_carriage_return_clamps_when_idx_out_of_range(self):
        """\\r 在 _completion_idx 越界时 clamp 到 0 后 confirmed。"""
        mock_chat_ui = self._make_mock_chat_ui()
        mock_chat_ui._bottom_bar._completion_idx = 99
        enter_key = _MockKeystroke(key='\r', is_sequence=False)
        mock_term = self._make_mock_terminal([enter_key])

        result = self._run_with_mocks(
            mock_chat_ui, mock_term,
            items=["a", "b"],
            display_items=["A", "B"],
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    def test_tcflush_called_before_cbreak(self):
        """cbreak 进入前应调用 tcflush 清空 stdin（防御性 flush）。"""
        mock_chat_ui = self._make_mock_chat_ui()
        enter_key = _MockKeystroke(is_sequence=True, code=_KEY_ENTER)
        mock_term = self._make_mock_terminal([enter_key])

        call_order = []

        def _record_tcflush(*args, **kwargs):
            call_order.append("tcflush")

        cbreak_ctx = MagicMock()
        cbreak_ctx.__enter__ = MagicMock(return_value=mock_term)
        cbreak_ctx.__exit__ = MagicMock(return_value=False)

        def _record_cbreak(*args, **kwargs):
            call_order.append("cbreak")
            return cbreak_ctx

        mock_term.cbreak = MagicMock(side_effect=_record_cbreak)

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0

        with patch(_CHAT_UI_PATCH, return_value=mock_chat_ui), \
             patch(_TERMINAL_PATCH, return_value=mock_term), \
             patch("sys.stdin", mock_stdin), \
             patch("os.isatty", return_value=True), \
             patch("termios.tcflush", side_effect=_record_tcflush) as mock_tcflush, \
             patch.object(sys, '__stdout__', MagicMock()):
            result = run_bottom_bar_selection(
                items=["item_a", "item_b"],
                display_items=["A", "B"],
            )

        self.assertEqual(result["action"], "confirmed")
        # 验证 tcflush 在 cbreak 之前调用（call_order 中第一个 tcflush 在 cbreak 之前）
        self.assertGreater(call_order.index("cbreak"), call_order.index("tcflush"),
                           msg="tcflush 必须在 cbreak 之前调用")
        mock_tcflush.assert_called()
        # 验证第一次 tcflush 调用参数：第一个参数应为 stdin fileno
        first_call_args = mock_tcflush.call_args_list[0][0]
        self.assertEqual(first_call_args[0], mock_stdin.fileno(),
                         msg="tcflush 第一个参数应为 stdin 文件描述符")


class TestRunSelectionRaw(unittest.TestCase):
    """测试 _run_selection_raw() 原始 I/O 选择循环（Cygwin 降级路径）。

    使用 mock os.read + select.select + tty.setcbreak 模拟按键输入，
    避免实际终端操作。Mock 策略与 EscapeMonitor 测试风格一致。
    """

    def setUp(self):
        self.mock_bb = MagicMock()
        self.mock_bb._active = True
        self.mock_bb._completion_idx = 0
        self.fd = 0  # 模拟 stdin 文件描述符
        self._items = ["item_a", "item_b", "item_c"]
        self._display_items = ["A", "B", "C"]

    def _run_with_raw_mocks(
        self,
        select_ready_flags,
        os_read_bytes,
        mock_bb=None,
        completion_idx=0,
        setcbreak_side_effect=None,
    ):
        """在 mock 环境下运行 _run_selection_raw。

        Args:
            select_ready_flags: [bool, ...] 每次 select.select 调用的就绪状态。
                True → ([fd], [], []), False → ([], [], []).
            os_read_bytes: [bytes, ...] 每次 os.read 返回的字节序列。
            mock_bb: Mock _BottomBar（默认使用 self.mock_bb）。
            completion_idx: bb._completion_idx 初始值。
            setcbreak_side_effect: tty.setcbreak 的 side_effect（默认 None=无操作）。

        Returns:
            _run_selection_raw 的返回值。
        """
        bb = mock_bb or self.mock_bb
        bb._completion_idx = completion_idx

        fd = self.fd
        flags_iter = iter(select_ready_flags)
        bytes_iter = iter(os_read_bytes)

        def _mock_select(rlist, wlist, xlist, timeout=None):
            try:
                is_ready = next(flags_iter)
            except StopIteration:
                return ([], [], [])
            return ([fd], [], []) if is_ready else ([], [], [])

        def _mock_os_read(fd_arg, n):
            try:
                return next(bytes_iter)
            except StopIteration:
                return b""

        setcbreak_mock = MagicMock()
        if setcbreak_side_effect is not None:
            setcbreak_mock.side_effect = setcbreak_side_effect

        with patch("select.select", side_effect=_mock_select), \
             patch("os.read", side_effect=_mock_os_read), \
             patch("tty.setcbreak", setcbreak_mock), \
             patch(
                 "src.tui.widgets.bottom_bar.selection._save_terminal_settings",
                 return_value={},
             ), \
             patch(
                 "src.tui.widgets.bottom_bar.selection._restore_terminal_settings"
             ), \
             patch("sys.stdin") as mock_stdin, \
             patch("termios.tcflush"):
            mock_stdin.fileno.return_value = fd
            return _run_selection_raw(
                items=self._items,
                display_items=self._display_items,
                initial_idx=0,
                title="测试",
                bb=bb,
            )

    # ── 上箭头（\\x1b[A）─

    def test_arrow_up_calls_cycle_completion_minus_one(self):
        """\\x1b[A 序列应调用 bb.cycle_completion(-1)。

        序列: \\x1b → [ → A（CSI 上箭头），之后 \\r 确认退出。
        select 调用顺序: (0.1)就绪 → (0.05)就绪 → (0.01)就绪 → (0.1)就绪
        os.read 返回: \\x1b → [ → A → \\r
        """
        result = self._run_with_raw_mocks(
            select_ready_flags=[True, True, True, True],
            os_read_bytes=[b"\x1b", b"[", b"A", b"\r"],
        )

        self.mock_bb.cycle_completion.assert_called_with(-1)
        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    # ── 下箭头（\\x1b[B）─

    def test_arrow_down_calls_cycle_completion_one(self):
        """\\x1b[B 序列应调用 bb.cycle_completion(1)。

        序列: \\x1b → [ → B（CSI 下箭头），之后 \\r 确认退出。
        """
        result = self._run_with_raw_mocks(
            select_ready_flags=[True, True, True, True],
            os_read_bytes=[b"\x1b", b"[", b"B", b"\r"],
        )

        self.mock_bb.cycle_completion.assert_called_with(1)
        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    # ── Enter（\\r）─

    def test_carriage_return_confirms_selection(self):
        """\\r 应返回 confirmed 及当前 _completion_idx。

        select(0.1) 就绪 → os.read 返回 \\r → 直接确认。
        """
        result = self._run_with_raw_mocks(
            select_ready_flags=[True],
            os_read_bytes=[b"\r"],
            completion_idx=2,
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 2)

    # ── Enter（\\n）─

    def test_newline_confirms_selection(self):
        """\\n 应返回 confirmed 及当前 _completion_idx。

        select(0.1) 就绪 → os.read 返回 \\n → 直接确认。
        """
        result = self._run_with_raw_mocks(
            select_ready_flags=[True],
            os_read_bytes=[b"\n"],
            completion_idx=0,
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    # ── Esc（单独 \\x1b，无后续字节）─

    def test_standalone_escape_cancels(self):
        """单独 \\x1b（后续无字节）应返回 cancel。

        select(0.1) 就绪 → os.read → \\x1b
        → select(0.05) 无数据 → 取消。
        """
        result = self._run_with_raw_mocks(
            select_ready_flags=[True, False],
            os_read_bytes=[b"\x1b"],
        )

        self.assertEqual(result["action"], "cancel")
        self.assertIsNone(result["index"])

    # ── stdin 不可读（tty.setcbreak 失败）─

    def test_setcbreak_failure_returns_error(self):
        """tty.setcbreak 失败时应返回 error。

        模拟 stdin 非 tty 场景：tty.setcbreak 抛出 OSError
        → 被外层 except Exception 捕获 → 返回 error。
        """
        result = self._run_with_raw_mocks(
            select_ready_flags=[],
            os_read_bytes=[],
            setcbreak_side_effect=OSError("inappropriate ioctl for device"),
        )

        self.assertEqual(result["action"], "error")
        self.assertIsNone(result["index"])

    # ── 边界：_completion_idx 越界时 Enter 不确认 ──

    def test_enter_clamps_when_completion_idx_out_of_range(self):
        """_completion_idx 越界时 Enter 应 clamp 到 0 后确认选择。

        idx(99) >= len(items)(3) 时，Enter 触发 clamp 到 0，
        返回 confirmed + index=0，而非进入无限循环。
        """
        result = self._run_with_raw_mocks(
            select_ready_flags=[True],
            os_read_bytes=[b"\r"],
            completion_idx=99,  # 远超 items 长度
        )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)
        self.mock_bb.cycle_completion.assert_not_called()


class TestPostCbreakDrain(unittest.TestCase):
    """测试 run_bottom_bar_selection 的 post-cbreak drain 逻辑。

    post-cbreak drain 在 term.cbreak() 上下文进入后、首次 term.inkey()
    之前执行，使用 select.select（10ms 超时）+ os.read 非阻塞清空
    cbreak 模式切换后残留的 stdin 字节。
    """

    def setUp(self):
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def _make_mock_chat_ui(self):
        """创建模拟 ChatUI，包含活跃的 _BottomBar。"""
        mock_bb = MagicMock()
        mock_bb._active = True
        mock_bb._completion_idx = 0
        mock_bb.show_completions.return_value = None
        mock_chat_ui = MagicMock()
        mock_chat_ui._bottom_bar = mock_bb
        return mock_chat_ui

    def _make_mock_terminal(self, keys):
        """创建模拟 Blessed Terminal，按顺序返回 key 列表。"""
        mock_term = MagicMock()
        mock_term.__enter__ = MagicMock(return_value=mock_term)
        mock_term.__exit__ = MagicMock(return_value=False)
        mock_term.inkey.side_effect = keys
        return mock_term

    # ── 调用顺序验证 ─────────────────────────────

    def test_drain_called_after_cbreak_before_inkey(self):
        """验证 select 在 cbreak 后、inkey 前被调用。"""
        mock_chat_ui = self._make_mock_chat_ui()
        enter_key = _MockKeystroke(key='\r', is_sequence=False)
        mock_term = self._make_mock_terminal([enter_key])

        call_order = []

        def _mock_select(rlist, wlist, xlist, timeout=None):
            call_order.append("select")
            return ([], [], [])

        # 包装 cbreak 以记录 __enter__ 调用时刻
        def _record_cbreak(*args, **kwargs):
            ctx = MagicMock()

            def _enter():
                call_order.append("cbreak")
                return mock_term

            ctx.__enter__ = MagicMock(side_effect=_enter)
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx

        mock_term.cbreak = MagicMock(side_effect=_record_cbreak)

        # 包装 inkey 以记录首次调用时刻
        original_inkey = mock_term.inkey
        def _record_inkey(*args, **kwargs):
            call_order.append("inkey")
            return original_inkey(*args, **kwargs)

        mock_term.inkey = MagicMock(side_effect=_record_inkey)

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0

        with patch(_CHAT_UI_PATCH, return_value=mock_chat_ui), \
             patch(_TERMINAL_PATCH, return_value=mock_term), \
             patch("sys.stdin", mock_stdin), \
             patch("os.isatty", return_value=True), \
             patch("select.select", side_effect=_mock_select), \
             patch("termios.tcflush"), \
             patch.object(sys, '__stdout__', MagicMock()):
            result = run_bottom_bar_selection(
                items=["a", "b"],
                display_items=["A", "B"],
            )

        self.assertEqual(result["action"], "confirmed")
        # 验证调用顺序: cbreak → select → inkey
        cbreak_idx = call_order.index("cbreak")
        select_idx = call_order.index("select")
        inkey_idx = call_order.index("inkey")
        self.assertLess(cbreak_idx, select_idx,
                        "select 必须在 cbreak 之后调用")
        self.assertLess(select_idx, inkey_idx,
                        "select 必须在 inkey 之前调用")

    # ── 无残余字节 ─────────────────────────────────

    def test_drain_no_residual_bytes_enter_confirms(self):
        """无残余字节时 drain 立即退出，Enter 确认正常。"""
        mock_chat_ui = self._make_mock_chat_ui()
        enter_key = _MockKeystroke(key='\r', is_sequence=False)
        mock_term = self._make_mock_terminal([enter_key])

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0

        # select 返回空 → drain 立即退出
        def _mock_select(rlist, wlist, xlist, timeout=None):
            return ([], [], [])

        with patch(_CHAT_UI_PATCH, return_value=mock_chat_ui), \
             patch(_TERMINAL_PATCH, return_value=mock_term), \
             patch("sys.stdin", mock_stdin), \
             patch("os.isatty", return_value=True), \
             patch("select.select", side_effect=_mock_select), \
             patch("termios.tcflush"), \
             patch.object(sys, '__stdout__', MagicMock()):
            result = run_bottom_bar_selection(
                items=["a", "b"],
                display_items=["A", "B"],
            )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)

    # ── 有残余字节 ─────────────────────────────────

    def test_drain_with_residual_bytes_enter_confirms(self):
        """有残余字节时 drain 消费它们，Enter 确认正常。"""
        mock_chat_ui = self._make_mock_chat_ui()
        enter_key = _MockKeystroke(key='\r', is_sequence=False)
        mock_term = self._make_mock_terminal([enter_key])

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0

        # select 第一次返回就绪（残余字节 \n），第二次返回空
        select_calls = [([0], [], []), ([], [], [])]
        select_iter = iter(select_calls)

        def _mock_select(rlist, wlist, xlist, timeout=None):
            try:
                return next(select_iter)
            except StopIteration:
                return ([], [], [])

        # os.read 返回残余 \n 字节
        os_read_bytes = [b"\n"]
        os_read_iter = iter(os_read_bytes)

        def _mock_os_read(fd, n):
            try:
                return next(os_read_iter)
            except StopIteration:
                return b""

        with patch(_CHAT_UI_PATCH, return_value=mock_chat_ui), \
             patch(_TERMINAL_PATCH, return_value=mock_term), \
             patch("sys.stdin", mock_stdin), \
             patch("os.isatty", return_value=True), \
             patch("select.select", side_effect=_mock_select), \
             patch("os.read", side_effect=_mock_os_read), \
             patch("termios.tcflush"), \
             patch.object(sys, '__stdout__', MagicMock()):
            result = run_bottom_bar_selection(
                items=["a", "b"],
                display_items=["A", "B"],
            )

        self.assertEqual(result["action"], "confirmed")
        self.assertEqual(result["index"], 0)


if __name__ == "__main__":
    unittest.main()
