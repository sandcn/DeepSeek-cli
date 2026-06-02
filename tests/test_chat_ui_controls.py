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
        """验证 Control 定义 write 抽象方法。"""
        from src.chat_ui._controls import Control
        assert hasattr(Control, 'write')
        assert getattr(Control.write, '__isabstractmethod__', False)

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


# ═══════════════════════════════════════════════════════════
# TestTextControl
# ═══════════════════════════════════════════════════════════

class TestTextControl:
    """TextControl 功能测试（14 个测试）。"""

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


# ═══════════════════════════════════════════════════════════
# TestMarkdownControl
# ═══════════════════════════════════════════════════════════

class TestMarkdownControl:
    """MarkdownControl 功能测试（8 个测试）。"""

    @pytest.fixture
    def mock_incremental(self):
        """mock IncrementalRenderer 构造函数的 fixture。

        MarkdownControl 在 __init__ 中惰性 import IncrementalRenderer
        （from ..api.renderer import IncrementalRenderer），
        因此需 patch src.api.renderer 模块中的类定义。
        """
        from unittest.mock import patch, MagicMock
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance._closed = False
        mock_cls.return_value = mock_instance
        with patch(
            "src.api.renderer.IncrementalRenderer",
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

        with patch("src.api.renderer.IncrementalRenderer") as mock_renderer_cls:
            mock_instance = mock_renderer_cls.return_value
            mock_instance._closed = False
            MarkdownControl(style="dim", show_indicator=True, typing_speed=500)
            mock_renderer_cls.assert_called_once()
            kwargs = mock_renderer_cls.call_args.kwargs
            assert kwargs["style"] == "dim"
            assert kwargs["show_indicator"] is True
            assert kwargs["typing_speed"] == 500
            assert "_file" in kwargs
