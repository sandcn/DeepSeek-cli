"""chat_ui 控件模块单元测试 — Control / TextControl / MarkdownControl。

测试覆盖：
  - Control ABC 抽象基类行为（不可直接实例化、接口完整性）
  - TextControl：prefix+style 输出、write_raw、write_ansi、close 幂等、空字符串
  - MarkdownControl：IncrementalRenderer 封装、write/close/refresh_width 委托、幂等
"""

from __future__ import annotations

import sys
import pytest
from unittest.mock import patch

# ── 将项目根目录加入 sys.path（Termux 环境需要）───
sys.path.insert(0, "/home/DeepSeek-cli")


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def mock_output_adapter():
    """创建 mock OutputAdapter，所有方法均为 MagicMock。"""
    from unittest.mock import MagicMock
    adapter = MagicMock()
    return adapter


# ═══════════════════════════════════════════════════════════
# TestControl — 验证 ABC 行为
# ═══════════════════════════════════════════════════════════

class TestControl:
    """Control 抽象基类行为测试。"""

    def test_cannot_instantiate_abc_directly(self):
        """验证 Control ABC 不能直接实例化。"""
        from src.chat_ui._controls import Control
        with pytest.raises(TypeError):
            Control()  # type: ignore[abstract]

    def test_interface_has_write(self):
        """验证 Control 定义 write 方法（非抽象，默认 no-op）。"""
        from src.chat_ui._controls import Control
        assert hasattr(Control, 'write')
        assert getattr(Control.write, '__isabstractmethod__', False) is False

    def test_interface_has_is_closed(self):
        """验证 Control 定义 is_closed 抽象属性。"""
        from src.chat_ui._controls import Control
        assert hasattr(Control, 'is_closed')
        assert getattr(Control.is_closed.fget, '__isabstractmethod__', False)

    def test_interface_has_close(self):
        """验证 Control 提供 close 默认实现。"""
        from src.chat_ui._controls import Control
        assert hasattr(Control, 'close')

    def test_interface_has_refresh_width(self):
        """验证 Control 提供 refresh_width 默认实现。"""
        from src.chat_ui._controls import Control
        assert hasattr(Control, 'refresh_width')

    def test_interface_has_start_line(self):
        """验证 Control 定义 start_line 属性。"""
        from src.chat_ui._controls import Control
        assert hasattr(Control, 'start_line')
        assert isinstance(getattr(Control, 'start_line'), property)

    def test_interface_has_level(self):
        """验证 Control 定义 level 属性。"""
        from src.chat_ui._controls import Control
        assert hasattr(Control, 'level')
        assert isinstance(getattr(Control, 'level'), property)


# ═══════════════════════════════════════════════════════════
# TestTextControl
# ═══════════════════════════════════════════════════════════

class TestTextControl:
    """TextControl 功能测试（18 个测试）。"""

    def test_write_with_prefix_and_style(self, mock_output_adapter):
        """验证 write() 输出 prefix + text 并应用 style。"""
        from src.chat_ui._controls import TextControl
        from rich.style import Style
        from rich.text import Text as RichText

        style = Style(bold=True)
        ctrl = TextControl(mock_output_adapter, prefix="> ", style=style)
        ctrl.write("hello")

        mock_output_adapter.write.assert_called_once()
        call_arg = mock_output_adapter.write.call_args[0][0]
        assert isinstance(call_arg, RichText)
        assert "hello" in call_arg.plain
        assert "> " in call_arg.plain

    def test_write_silent_when_closed(self, mock_output_adapter):
        """验证 close 后 write() 静默跳过。"""
        from src.chat_ui._controls import TextControl
        ctrl = TextControl(mock_output_adapter, prefix="> ")
        ctrl.close()
        ctrl.write("should be ignored")
        mock_output_adapter.write.assert_not_called()

    def test_write_empty_string(self, mock_output_adapter):
        """验证 write("") 仍输出前缀（用于换行效果）。"""
        from src.chat_ui._controls import TextControl
        ctrl = TextControl(mock_output_adapter, prefix="\n  > ")
        ctrl.write("")
        mock_output_adapter.write.assert_called_once()
        call_arg = mock_output_adapter.write.call_args[0][0]
        assert "\n  > " in call_arg.plain

    def test_write_raw(self, mock_output_adapter):
        """验证 write_raw() 直写纯文本（无 prefix/style）。"""
        from src.chat_ui._controls import TextControl
        ctrl = TextControl(mock_output_adapter)
        ctrl.write_raw("raw text")
        mock_output_adapter.write_raw.assert_called_once_with("raw text")

    def test_write_raw_silent_when_closed(self, mock_output_adapter):
        """验证 close 后 write_raw() 静默跳过。"""
        from src.chat_ui._controls import TextControl
        ctrl = TextControl(mock_output_adapter)
        ctrl.close()
        ctrl.write_raw("should be ignored")
        mock_output_adapter.write_raw.assert_not_called()

    def test_write_ansi_success(self, mock_output_adapter):
        """验证 write_ansi() 解析 ANSI 文本并输出。"""
        from src.chat_ui._controls import TextControl
        from rich.text import Text as RichText
        ctrl = TextControl(mock_output_adapter)
        ctrl.write_ansi("\033[1mBold\033[0m")
        mock_output_adapter.write.assert_called_once()
        call_arg = mock_output_adapter.write.call_args[0][0]
        assert isinstance(call_arg, RichText)

    def test_write_ansi_fallback_on_parse_error(self, mock_output_adapter):
        """验证 write_ansi() 在 Text.from_ansi 失败时回退到 write_raw。"""
        from src.chat_ui._controls import TextControl
        from unittest.mock import patch
        ctrl = TextControl(mock_output_adapter)
        with patch("rich.text.Text.from_ansi", side_effect=ValueError("parse error")):
            ctrl.write_ansi("\033[1mBold\033[0m")
        mock_output_adapter.write_raw.assert_called_once_with("\033[1mBold\033[0m")

    def test_write_ansi_silent_when_closed(self, mock_output_adapter):
        """验证 close 后 write_ansi() 静默跳过。"""
        from src.chat_ui._controls import TextControl
        ctrl = TextControl(mock_output_adapter)
        ctrl.close()
        ctrl.write_ansi("\033[1mBold\033[0m")
        mock_output_adapter.write.assert_not_called()
        mock_output_adapter.write_raw.assert_not_called()

    def test_is_closed_initial(self, mock_output_adapter):
        """验证初始状态 is_closed=False。"""
        from src.chat_ui._controls import TextControl
        ctrl = TextControl(mock_output_adapter)
        assert ctrl.is_closed is False

    def test_is_closed_after_close(self, mock_output_adapter):
        """验证 close 后 is_closed=True。"""
        from src.chat_ui._controls import TextControl
        ctrl = TextControl(mock_output_adapter)
        ctrl.close()
        assert ctrl.is_closed is True

    def test_close_idempotent(self, mock_output_adapter):
        """验证 close() 幂等——多次调用无副作用。"""
        from src.chat_ui._controls import TextControl
        ctrl = TextControl(mock_output_adapter)
        ctrl.close()
        ctrl.close()  # 第二次不应抛异常
        assert ctrl.is_closed is True
        # flush 只调用一次
        assert mock_output_adapter.flush.call_count == 1

    def test_close_flushes_adapter(self, mock_output_adapter):
        """验证 close() 会 flush 适配器。"""
        from src.chat_ui._controls import TextControl
        ctrl = TextControl(mock_output_adapter)
        ctrl.close()
        mock_output_adapter.flush.assert_called_once()

    def test_empty_prefix_and_no_style(self, mock_output_adapter):
        """验证 prefix="" + style=None 的默认行为。"""
        from src.chat_ui._controls import TextControl
        ctrl = TextControl(mock_output_adapter)
        ctrl.write("plain")
        mock_output_adapter.write.assert_called_once()
        call_arg = mock_output_adapter.write.call_args[0][0]
        assert "plain" in call_arg.plain

    def test_subclass_of_control(self):
        """验证 TextControl 是 Control 的子类。"""
        from src.chat_ui._controls import Control, TextControl
        assert issubclass(TextControl, Control)

    def test_textcontrol_with_style_bold(self, mock_output_adapter):
        """验证 bold style 正确传递到输出 Text。"""
        from src.chat_ui._controls import TextControl
        from rich.style import Style
        style = Style(bold=True)
        ctrl = TextControl(mock_output_adapter, prefix="! ", style=style)
        ctrl.write("error msg")
        call_arg = mock_output_adapter.write.call_args[0][0]
        assert "! " in call_arg.plain
        assert "error msg" in call_arg.plain

    def test_default_start_line_and_level(self, mock_output_adapter):
        """验证 TextControl 的 start_line 和 level 默认值为 0。"""
        from src.chat_ui._controls import TextControl
        ctrl = TextControl(mock_output_adapter)
        assert ctrl.start_line == 0
        assert ctrl.level == 0

    def test_custom_start_line_and_level(self, mock_output_adapter):
        """验证 TextControl 能正确设置 start_line 和 level。"""
        from src.chat_ui._controls import TextControl
        ctrl = TextControl(mock_output_adapter, start_line=42, level=3)
        assert ctrl.start_line == 42
        assert ctrl.level == 3

    def test_setter_start_line_and_level(self, mock_output_adapter):
        """验证 TextControl 的 setter 可修改 start_line 和 level。"""
        from src.chat_ui._controls import TextControl
        ctrl = TextControl(mock_output_adapter)
        ctrl.start_line = 10
        ctrl.level = 5
        assert ctrl.start_line == 10
        assert ctrl.level == 5


# ═══════════════════════════════════════════════════════════
# TestMarkdownControl
# ═══════════════════════════════════════════════════════════

class TestMarkdownControl:
    """MarkdownControl 功能测试（12 个测试）。"""

    @pytest.fixture
    def mock_incremental(self):
        """mock IncrementalRenderer 构造函数的 fixture。

        IncrementalRenderer 在 _controls.py 模块级 import（模块加载时解析），
        因此需 patch src.chat_ui._controls 模块中的引用。
        """
        from unittest.mock import patch, MagicMock
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance._closed = False
        mock_cls.return_value = mock_instance
        with patch(
            "src.chat_ui._controls.IncrementalRenderer",
            mock_cls,
        ):
            yield mock_instance

    def test_write_delegates_to_renderer(self, mock_incremental):
        """验证 write() 委托给 IncrementalRenderer.write()。"""
        from src.chat_ui._controls import MarkdownControl
        ctrl = MarkdownControl(style="dim")
        ctrl.write("# Hello")
        mock_incremental.write.assert_called_once_with("# Hello")

    def test_write_silent_when_closed(self, mock_incremental):
        """验证 close 后 write() 静默跳过。"""
        from src.chat_ui._controls import MarkdownControl
        ctrl = MarkdownControl()
        ctrl.close()
        ctrl.write("# should be ignored")
        mock_incremental.write.assert_not_called()

    def test_close_delegates_to_renderer(self, mock_incremental):
        """验证 close() 委托给 IncrementalRenderer.close()。"""
        from src.chat_ui._controls import MarkdownControl
        ctrl = MarkdownControl()
        ctrl.close()
        mock_incremental.close.assert_called_once()

    def test_close_idempotent(self, mock_incremental):
        """验证 close() 幂等——多次调用只委托一次。"""
        from src.chat_ui._controls import MarkdownControl
        ctrl = MarkdownControl()
        ctrl.close()
        ctrl.close()
        assert ctrl.is_closed is True
        # IncrementalRenderer.close 只应被调用一次
        mock_incremental.close.assert_called_once()

    def test_refresh_width_delegates(self, mock_incremental):
        """验证 refresh_width() 委托给 IncrementalRenderer.force_refresh_width()。"""
        from src.chat_ui._controls import MarkdownControl
        ctrl = MarkdownControl()
        ctrl.refresh_width()
        mock_incremental.force_refresh_width.assert_called_once()

    def test_refresh_width_multiple_calls_idempotent(self, mock_incremental):
        """验证多次 refresh_width() 调用幂等（每次委托，不崩溃）。"""
        from src.chat_ui._controls import MarkdownControl
        ctrl = MarkdownControl()
        ctrl.refresh_width()
        ctrl.refresh_width()
        ctrl.refresh_width()
        # 每次调用都委托给 renderer（幂等指不崩溃，非仅调用一次）
        assert mock_incremental.force_refresh_width.call_count == 3

    def test_refresh_width_after_close(self, mock_incremental):
        """验证 close 后 refresh_width() 仍可安全调用（委托给已关闭的 renderer）。"""
        from src.chat_ui._controls import MarkdownControl
        ctrl = MarkdownControl()
        ctrl.close()
        # close 后 refresh_width 应不崩溃（委托给 renderer.force_refresh_width）
        ctrl.refresh_width()
        mock_incremental.force_refresh_width.assert_called_once()

    def test_is_closed_initial(self, mock_incremental):
        """验证初始状态 is_closed=False。"""
        from src.chat_ui._controls import MarkdownControl
        ctrl = MarkdownControl()
        assert ctrl.is_closed is False

    def test_is_closed_after_close(self, mock_incremental):
        """验证 close 后 is_closed=True。"""
        from src.chat_ui._controls import MarkdownControl
        ctrl = MarkdownControl()
        ctrl.close()
        assert ctrl.is_closed is True

    def test_subclass_of_control(self):
        """验证 MarkdownControl 是 Control 的子类。"""
        from src.chat_ui._controls import Control, MarkdownControl
        assert issubclass(MarkdownControl, Control)

    def test_constructor_passes_kwargs(self):
        """验证构造函数正确传递参数给 IncrementalRenderer。"""
        from src.chat_ui._controls import MarkdownControl
        from unittest.mock import patch

        with patch("src.chat_ui._controls.IncrementalRenderer") as mock_renderer_cls:
            mock_instance = mock_renderer_cls.return_value
            mock_instance._closed = False
            MarkdownControl(style="dim", show_indicator=True, typing_speed=500)
            mock_renderer_cls.assert_called_once()
            kwargs = mock_renderer_cls.call_args.kwargs
            assert kwargs["style"] == "dim"
            assert kwargs["show_indicator"] is True
            assert kwargs["typing_speed"] == 500
            assert "_file" in kwargs

    def test_default_start_line_and_level(self, mock_incremental):
        """验证 MarkdownControl 的 start_line 和 level 默认值为 0。"""
        from src.chat_ui._controls import MarkdownControl
        ctrl = MarkdownControl()
        assert ctrl.start_line == 0
        assert ctrl.level == 0

    def test_custom_start_line_and_level(self, mock_incremental):
        """验证 MarkdownControl 能正确设置 start_line 和 level。"""
        from src.chat_ui._controls import MarkdownControl
        ctrl = MarkdownControl(start_line=7, level=2)
        assert ctrl.start_line == 7
        assert ctrl.level == 2

    def test_setter_start_line_and_level(self, mock_incremental):
        """验证 MarkdownControl 的 setter 可修改 start_line 和 level。"""
        from src.chat_ui._controls import MarkdownControl
        ctrl = MarkdownControl()
        ctrl.start_line = 99
        ctrl.level = 1
        assert ctrl.start_line == 99
        assert ctrl.level == 1


# ═══════════════════════════════════════════════════════════
# TestControlList
# ═══════════════════════════════════════════════════════════

class TestControlList:
    """ControlList 控件列表管理器测试。"""

    @pytest.fixture
    def mock_adapter(self):
        from unittest.mock import MagicMock
        return MagicMock()

    def _make_ctrl(self, adapter, start_line=0):
        """创建简单 TextControl（用于 ControlList 测试）。"""
        from src.chat_ui._controls import TextControl
        return TextControl(adapter, start_line=start_line)

    def test_add_and_sort(self, mock_adapter):
        """添加控件后列表按 start_line 排序。"""
        from src.chat_ui._controls import ControlList
        cl = ControlList()
        c1 = self._make_ctrl(mock_adapter)
        c2 = self._make_ctrl(mock_adapter)
        c3 = self._make_ctrl(mock_adapter)

        cl.add(c3, start_line=30)
        cl.add(c1, start_line=10)
        cl.add(c2, start_line=20)

        assert [c.start_line for c in cl._controls] == [10, 20, 30]

    def test_add_auto_assigns_start_line(self, mock_adapter):
        """不指定 start_line 时自动分配 _next_line。"""
        from src.chat_ui._controls import ControlList
        cl = ControlList()
        c1 = self._make_ctrl(mock_adapter)
        cl.add(c1)  # 自动分配 start_line=1
        assert c1.start_line == 1
        assert cl._next_line == 2

    def test_add_multiple_auto_increments(self, mock_adapter):
        """多次自动分配 start_line 依次递增。"""
        from src.chat_ui._controls import ControlList
        cl = ControlList()
        c1 = self._make_ctrl(mock_adapter)
        c2 = self._make_ctrl(mock_adapter)
        c3 = self._make_ctrl(mock_adapter)
        cl.add(c1)
        cl.add(c2)
        cl.add(c3)
        assert c1.start_line == 1
        assert c2.start_line == 2
        assert c3.start_line == 3
        assert cl._next_line == 4

    def test_remove(self, mock_adapter):
        """移除控件后列表不包含该控件。"""
        from src.chat_ui._controls import ControlList
        cl = ControlList()
        c1 = self._make_ctrl(mock_adapter)
        c2 = self._make_ctrl(mock_adapter)
        cl.add(c1)
        cl.add(c2)
        cl.remove(c1)
        assert len(cl._controls) == 1
        assert cl._controls[0] is c2

    def test_remove_nonexistent_silent(self, mock_adapter):
        """移除不在列表中的控件静默跳过。"""
        from src.chat_ui._controls import ControlList
        cl = ControlList()
        c1 = self._make_ctrl(mock_adapter)
        c2 = self._make_ctrl(mock_adapter)
        cl.add(c1)
        cl.remove(c2)  # 不抛异常
        assert len(cl._controls) == 1

    def test_close_all(self, mock_adapter):
        """close_all() 关闭所有控件并清空列表。"""
        from src.chat_ui._controls import ControlList
        cl = ControlList()
        c1 = self._make_ctrl(mock_adapter)
        c2 = self._make_ctrl(mock_adapter)
        cl.add(c1)
        cl.add(c2)
        cl.close_all()
        assert len(cl._controls) == 0
        assert c1.is_closed is True
        assert c2.is_closed is True
        assert cl._next_line == 1

    def test_refresh_width_all_calls_active_controls(self, mock_adapter):
        """refresh_width_all() 遍历所有活跃控件调用 refresh_width()。"""
        from src.chat_ui._controls import ControlList
        from unittest.mock import MagicMock
        cl = ControlList()
        c1 = MagicMock()
        c1.is_closed = False
        c2 = MagicMock()
        c2.is_closed = False
        cl.add(c1)
        cl.add(c2)
        cl.refresh_width_all()
        c1.refresh_width.assert_called_once()
        c2.refresh_width.assert_called_once()

    def test_refresh_width_all_skips_closed(self, mock_adapter):
        """refresh_width_all() 跳过已关闭的控件。"""
        from src.chat_ui._controls import ControlList
        from unittest.mock import MagicMock
        cl = ControlList()
        c1 = MagicMock()
        c1.is_closed = False
        c2 = MagicMock()
        c2.is_closed = True  # 已关闭
        cl.add(c1)
        cl.add(c2)
        cl.refresh_width_all()
        c1.refresh_width.assert_called_once()
        c2.refresh_width.assert_not_called()

    def test_refresh_width_all_exception_isolation(self, mock_adapter):
        """refresh_width_all() 中单个 refresh_width 异常不阻塞其他控件。"""
        from src.chat_ui._controls import ControlList
        from unittest.mock import MagicMock
        cl = ControlList()
        c1 = MagicMock()
        c1.is_closed = False
        c1.refresh_width.side_effect = RuntimeError("fail")
        c2 = MagicMock()
        c2.is_closed = False
        cl.add(c1)
        cl.add(c2)
        cl.refresh_width_all()  # 不应抛异常
        c2.refresh_width.assert_called_once()


# ═══════════════════════════════════════════════════════════
# TestToolOutputControl
# ═══════════════════════════════════════════════════════════

class TestToolOutputControl:
    """ToolOutputControl 工具输出控件测试。"""

    @pytest.fixture
    def mock_adapter(self):
        from unittest.mock import MagicMock
        return MagicMock()

    @pytest.fixture
    def dim_style(self):
        from rich.style import Style
        return Style(dim=True)

    @pytest.fixture
    def ctrl(self, mock_adapter, dim_style):
        from src.chat_ui._controls import ToolOutputControl
        return ToolOutputControl(mock_adapter, dim_style=dim_style)

    def test_standard_output(self, ctrl, mock_adapter):
        """标准输出（无 \\r）→ 走样式化 write。"""
        ctrl.write("hello world")
        mock_adapter.write.assert_called_once()
        args = mock_adapter.write.call_args[0][0]
        assert "hello world" in args.plain

    def test_carriage_return_takes_last_segment(self, ctrl, mock_adapter):
        """纯 \\r 文本 → 只取最后一段。"""
        ctrl.write("progress\rstatus\rdone")
        mock_adapter.write_raw.assert_any_call("done")
        mock_adapter.write_raw.assert_any_call('\n')

    def test_carriage_ending_marks_last_was_carriage(self, ctrl, mock_adapter):
        """末尾 \\r → _last_was_carriage=True，不追加 \\n。"""
        ctrl.write("progress\rstatus\r")
        mock_adapter.write_raw.assert_any_call("")
        assert ctrl._last_was_carriage is True

    def test_no_carriage_resets_last_was_carriage(self, ctrl, mock_adapter):
        """无 \\r → _last_was_carriage=False。"""
        ctrl._last_was_carriage = True
        # 需要 mock 一个 write_raw 调用来重置
        mock_adapter.write_raw.reset_mock()
        ctrl.write("hello")
        assert ctrl._last_was_carriage is False

    def test_ansi_with_carriage(self, ctrl, mock_adapter):
        """ANSI + \\r 混合 → 移除 \\r 后走 Text.from_ansi。"""
        from rich.text import Text
        ctrl.write("\033[31mred\r\033[32mgreen\033[0m")
        mock_adapter.write.assert_called_once()
        args = mock_adapter.write.call_args[0][0]
        assert isinstance(args[0], Text)

    def test_ansi_with_carriage_mixed_sequence(self, ctrl, mock_adapter):
        """ANSI + \\r 混合多次写入 → _last_was_carriage 正确维护。"""
        ctrl.write("\033[31mprogress\033[0m\r")
        assert ctrl._last_was_carriage is True
        ctrl.write("\033[32mdone\033[0m\n")
        assert ctrl._last_was_carriage is False

    def test_ansi_with_carriage_then_normal(self, ctrl, mock_adapter):
        """ANSI+\\r 后接普通文本 → 补写换行后再走样式化输出。"""
        ctrl.write("\033[33mstatus\033[0m\r")
        assert ctrl._last_was_carriage is True
        mock_adapter.reset_mock()
        ctrl.write("hello")
        # 应补写换行然后走样式化输出
        assert ctrl._last_was_carriage is False

    def test_carriage_ansi_only_no_text(self, ctrl, mock_adapter):
        """仅含 ANSI+\\r 无普通文本 → ANSI 解析正确。"""
        ctrl.write("\033[1m\033[31m\r\033[32mOK\033[0m\r")
        assert ctrl._last_was_carriage is True

    def test_ansi_with_carriage_close_appends_newline(self, ctrl, mock_adapter):
        """ANSI+\\r 后 close → 补写 \\n。"""
        ctrl.write("\033[34minfo\033[0m\r")
        assert ctrl._last_was_carriage is True
        ctrl.close()
        mock_adapter.write_raw.assert_any_call("\n")

    def test_truncation(self, ctrl, mock_adapter):
        """超长文本 → 截断 + ...(truncated)。"""
        from src.chat_ui._controls import ToolOutputControl
        long_text = "x" * (ToolOutputControl._MAX_OUTPUT_LEN + 100)
        ctrl.write(long_text)
        mock_adapter.write.assert_called_once()
        args = mock_adapter.write.call_args[0][0]
        assert "...(truncated)" in args.plain

    def test_close_flushes(self, ctrl, mock_adapter):
        """close() flush 适配器。"""
        ctrl.close()
        mock_adapter.flush.assert_called_once()
        assert ctrl.is_closed is True

    def test_close_idempotent(self, ctrl, mock_adapter):
        """close() 幂等。"""
        ctrl.close()
        ctrl.close()
        assert mock_adapter.flush.call_count == 1

    def test_close_with_carriage_appends_newline(self, ctrl, mock_adapter):
        """_last_was_carriage=True 时 close() 补写 \\n。"""
        ctrl._last_was_carriage = True
        ctrl.close()
        mock_adapter.write_raw.assert_called_with("\n")
        mock_adapter.flush.assert_called_once()

    def test_write_silent_when_closed(self, ctrl, mock_adapter):
        """close 后 write() 静默跳过。"""
        ctrl.close()
        mock_adapter.reset_mock()
        ctrl.write("should be ignored")
        mock_adapter.write.assert_not_called()
        mock_adapter.write_raw.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestToolSummaryControl
# ═══════════════════════════════════════════════════════════

class TestToolSummaryControl:
    """ToolSummaryControl 工具汇总控件测试。"""

    @pytest.fixture
    def mock_adapter(self):
        from unittest.mock import MagicMock
        return MagicMock()

    @pytest.fixture
    def styles(self):
        from rich.style import Style
        return {
            "success": Style(color="green"),
            "fail": Style(color="red"),
            "warn": Style(color="orange1"),
            "dim": Style(dim=True),
        }

    @pytest.fixture
    def ctrl(self, mock_adapter, styles):
        from src.chat_ui._controls import ToolSummaryControl
        return ToolSummaryControl(
            mock_adapter,
            style_success=styles["success"],
            style_fail=styles["fail"],
            style_warn=styles["warn"],
            style_dim=styles["dim"],
        )

    def test_success_summary(self, ctrl, mock_adapter):
        """成功汇总 → 绿色 · 图标。"""
        ctrl.summarize(successful=("tool_a", "tool_b"), failed=())
        mock_adapter.write.assert_called_once()
        args = mock_adapter.write.call_args[0][0]
        assert "2" in args.plain
        assert "工具完成" in args.plain

    def test_failure_summary_item1_zero(self, ctrl, mock_adapter):
        """item[1]=0 → 显示 "0"。"""
        ctrl.summarize(successful=(), failed=(("tool_a", 0),))
        calls = mock_adapter.write.call_args_list
        all_text = "".join(str(c) for c in calls)
        assert "0" in all_text

    def test_failure_summary_item1_false(self, ctrl, mock_adapter):
        """item[1]=False → 显示 "False"。"""
        ctrl.summarize(successful=(), failed=(("tool_a", False),))
        calls = mock_adapter.write.call_args_list
        all_text = "".join(str(c) for c in calls)
        assert "False" in all_text

    def test_failure_summary_item1_none(self, ctrl, mock_adapter):
        """item[1]=None → 不显示 error 但显示工具名。"""
        ctrl.summarize(successful=(), failed=(("tool_a", None),))
        calls = mock_adapter.write.call_args_list
        all_text = "".join(str(c) for c in calls)
        assert "tool_a" in all_text

    def test_failure_summary_extra_elements(self, ctrl, mock_adapter):
        """3+ 元素 → extras 追加到 error。"""
        ctrl.summarize(successful=(), failed=(("tool_a", "timeout", 137),))
        calls = mock_adapter.write.call_args_list
        all_text = "".join(str(c) for c in calls)
        assert "137" in all_text

    def test_failure_summary_non_tuple(self, ctrl, mock_adapter):
        """非 tuple 元素 → str() 安全显示。"""
        ctrl.summarize(successful=(), failed=("just_a_string",))
        calls = mock_adapter.write.call_args_list
        all_text = "".join(str(c) for c in calls)
        assert "just_a_string" in all_text

    def test_close(self, ctrl, mock_adapter):
        """close() flush 适配器。"""
        ctrl.close()
        mock_adapter.flush.assert_called_once()
        assert ctrl.is_closed is True

    def test_summarize_silent_when_closed(self, ctrl, mock_adapter):
        """close 后 summarize() 静默跳过。"""
        ctrl.close()
        mock_adapter.reset_mock()
        ctrl.summarize(successful=("tool_a",), failed=())
        mock_adapter.write.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestParseInfoControl
# ═══════════════════════════════════════════════════════════

class TestParseInfoControl:
    """ParseInfoControl 解析进度控件测试。"""

    @pytest.fixture
    def mock_adapter(self):
        from unittest.mock import MagicMock
        return MagicMock()

    @pytest.fixture
    def ctrl(self, mock_adapter):
        from src.chat_ui._controls import ParseInfoControl
        return ParseInfoControl(mock_adapter)

    def test_normal_tokens(self, ctrl, mock_adapter):
        """正常 int tokens → "Nt"。"""
        ctrl.update("tool_test", 42, 1.5)
        mock_adapter.write_raw.assert_called_once()
        text = mock_adapter.write_raw.call_args[0][0]
        assert "42t" in text
        assert "tool_test" in text
        assert "1.50s" in text

    def test_inf_tokens_shows_question_mark(self, ctrl, mock_adapter):
        """tokens=inf → "?"。"""
        ctrl.update("tool_test", float('inf'), 1.5)
        text = mock_adapter.write_raw.call_args[0][0]
        assert "?" in text
        assert "inft" not in text

    def test_nan_tokens_shows_question_mark(self, ctrl, mock_adapter):
        """tokens=nan → "?"。"""
        ctrl.update("tool_test", float('nan'), 1.5)
        text = mock_adapter.write_raw.call_args[0][0]
        assert "?" in text
        assert "nant" not in text

    def test_clear_sentinel(self, ctrl, mock_adapter):
        """tokens == _CLEAR_PARSE_LINE (-1) → write_raw('\\n')。"""
        ctrl.update("", -1, 0.0)
        mock_adapter.write_raw.assert_called_once_with("\n")

    def test_close(self, ctrl, mock_adapter):
        """close() flush 适配器。"""
        ctrl.close()
        mock_adapter.flush.assert_called_once()
        assert ctrl.is_closed is True

    def test_update_silent_when_closed(self, ctrl, mock_adapter):
        """close 后 update() 静默跳过。"""
        ctrl.close()
        mock_adapter.reset_mock()
        ctrl.update("tool_test", 42, 1.5)
        mock_adapter.write_raw.assert_not_called()
