"""测试 CodeHandler — 代码块渲染，特别是空行处理回归测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from rich.text import Text

from src.api.renderer.handlers.code import CodeHandler
from src.api.renderer.handlers.base import TokenHandler


def _make_mock_engine(typing_speed: int = 1):
    """构造 mock RenderEngine 实例。"""
    engine = MagicMock()
    engine._typing_speed = typing_speed
    engine._code_theme = "monokai"
    engine._theme = MagicMock()
    engine.get_lexer.return_value = MagicMock()
    return engine


class TestRenderCodeBlockTyping:
    """_render_code_block_typing 空行处理回归测试。"""

    def test_empty_line_triggers_write_line(self):
        """空行应调用 write_line() 而非 write_typing()。"""
        engine = _make_mock_engine(typing_speed=1)
        lines = ["", "hello world", ""]

        handler = CodeHandler()
        handler._highlight_line = MagicMock(return_value="highlighted")

        handler._render_code_block_typing(lines, "python", engine)

        # 验证 write_line 被调用了 2 次（两行空行）
        assert engine.write_line.call_count == 2
        # 验证 write_typing 只被调用了 1 次（非空行）
        assert engine.write_typing.call_count == 1
        # 验证 highlight_line 也只被调用了 1 次（非空行做高亮）
        assert handler._highlight_line.call_count == 1

    def test_non_empty_lines_normal_path(self):
        """非空行应正常走 write_typing。"""
        engine = _make_mock_engine(typing_speed=1)
        lines = ["print('hello')", "print('world')"]

        handler = CodeHandler()
        handler._highlight_line = MagicMock(return_value="highlighted")

        handler._render_code_block_typing(lines, "python", engine)

        # write_line 不应被调用
        engine.write_line.assert_not_called()
        # write_typing 应为 2 次
        assert engine.write_typing.call_count == 2

    def test_mixed_lines_correct_routing(self):
        """混合空行/非空行，正确定位。"""
        engine = _make_mock_engine(typing_speed=1)
        lines = ["", "def foo():", "", "    pass", ""]

        handler = CodeHandler()
        handler._highlight_line = MagicMock(return_value="highlighted")

        handler._render_code_block_typing(lines, "python", engine)

        # write_line 3 次，write_typing 2 次，highlight_line 2 次
        assert engine.write_line.call_count == 3
        assert engine.write_typing.call_count == 2
        assert handler._highlight_line.call_count == 2

    def test_all_empty_lines(self):
        """全空行列表：仅 write_line，无 write_typing。"""
        engine = _make_mock_engine(typing_speed=1)
        lines = ["", "", ""]

        handler = CodeHandler()
        handler._highlight_line = MagicMock(return_value="highlighted")

        handler._render_code_block_typing(lines, "python", engine)

        assert engine.write_line.call_count == 3
        engine.write_typing.assert_not_called()
        handler._highlight_line.assert_not_called()

    def test_empty_lines_list(self):
        """空列表：无任何输出调用。"""
        engine = _make_mock_engine(typing_speed=1)
        lines: list[str] = []

        handler = CodeHandler()
        handler._render_code_block_typing(lines, "python", engine)

        engine.write_line.assert_not_called()
        engine.write_typing.assert_not_called()

    def test_typing_speed_zero_uses_write_typing(self):
        """typing_speed=0 时走 write_typing 分支（InstantStrategy），非空行仍获得换行。"""
        engine = _make_mock_engine(typing_speed=0)
        lines = ["", "normal line"]

        handler = CodeHandler()
        handler._highlight_line = MagicMock(return_value="highlighted")

        handler._render_code_block_typing(lines, "python", engine)

        # 空行仍应走 write_line
        engine.write_line.assert_called_once()
        # 非空行走 write_typing（typing_speed=0 时 InstantStrategy 自动追加换行）
        engine.write_typing.assert_called_once()
        # write 不应被调用（已统一为 write_typing 路径，修复代码行合并 Bug）
        engine.write.assert_not_called()

    def test_lexer_none_uses_text_fallback_regression(self):
        """回归测试：lexer=None 时不会 AttributeError，且输出纯文本 Text(line)。"""
        engine = _make_mock_engine(typing_speed=1)
        engine.get_lexer.return_value = None
        lines = ["print('hello')", "def foo():", ""]

        handler = CodeHandler()
        handler._highlight_line = MagicMock(return_value="highlighted")

        handler._render_code_block_typing(lines, "python", engine)

        # 空行走 write_line
        engine.write_line.assert_called_once()
        # 非空行 2 行走 write_typing
        assert engine.write_typing.call_count == 2
        # 每行应传入 Text 实例（而非高亮后的字符串）
        for call_args in engine.write_typing.call_args_list:
            code_text = call_args[0][0]
            assert isinstance(code_text, Text), (
                f"lexer=None 时 code_text 应为 Text 实例，实际为 {type(code_text)}"
            )
        # _highlight_line 不应被调用——lexer=None 时走纯文本路径
        handler._highlight_line.assert_not_called()
