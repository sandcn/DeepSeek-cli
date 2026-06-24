"""Transform 组件单元测试。

覆盖 Transform 的逐行变换、ANSI 感知辅助函数、
空子组件和多行变换。
"""

from __future__ import annotations

import pytest

from src.chat_ui.react_ink._transform import (
    Transform,
    _strip_ansi_prefix,
    _preserve_ansi_prefix,
)
from src.chat_ui._components import TuiComponent


# ── 测试辅助 ────────────────────────────────────────────

class _TextComp(TuiComponent):
    """简单文本子组件。"""

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    def render(self) -> str:
        return self.text


# ═══════════════════════════════════════════════════════════
# TestTransform
# ═══════════════════════════════════════════════════════════

class TestTransform:
    """Transform 组件测试。"""

    def test_basic_transform(self):
        """对每行应用 uppercase 变换。"""
        comp = Transform(
            transform=lambda line, i: line.upper(),
            children=_TextComp("hello\nworld"),
        )
        output = comp.render()
        assert output == "HELLO\nWORLD"

    def test_line_index_passed(self):
        """transform 接收正确的行号。"""
        indices = []

        def _collect(line: str, index: int) -> str:
            indices.append(index)
            return line

        comp = Transform(
            transform=_collect,
            children=_TextComp("a\nb\nc"),
        )
        comp.render()
        assert indices == [0, 1, 2]

    def test_empty_children(self):
        """空子组件返回空字符串。"""
        comp = Transform(
            transform=lambda line, i: line.upper(),
            children=None,
        )
        output = comp.render()
        assert output == ""

    def test_empty_child_text(self):
        """子组件返回空字符串时 Transform 返回空字符串。"""
        comp = Transform(
            transform=lambda line, i: f"[{line}]",
            children=_TextComp(""),
        )
        output = comp.render()
        # Transform.render() 对空文本返回 ""
        assert output == ""

    def test_multiline_transform(self):
        """多行文本逐行变换。"""
        comp = Transform(
            transform=lambda line, i: f"{i}:{line}",
            children=_TextComp("foo\nbar\nbaz"),
        )
        output = comp.render()
        assert output == "0:foo\n1:bar\n2:baz"

    def test_indentation_transform(self):
        """悬挂缩进变换。"""
        comp = Transform(
            transform=lambda line, i: line if i == 0 else "    " + line,
            children=_TextComp("title\nline1\nline2"),
        )
        output = comp.render()
        lines = output.split("\n")
        assert lines[0] == "title"
        assert lines[1] == "    line1"
        assert lines[2] == "    line2"

    def test_multiple_children(self):
        """多个子组件时合并渲染。"""
        comp = Transform(
            transform=lambda line, i: f"#{line}",
            children=[_TextComp("a"), _TextComp("b")],
        )
        output = comp.render()
        # 两个子组件渲染输出用换行连接后再逐行变换
        # TuiComponent.render_children 用 "\n".join 连接
        assert "#a" in output
        assert "#b" in output


# ═══════════════════════════════════════════════════════════
# TestAnsiHelpers
# ═══════════════════════════════════════════════════════════

class TestAnsiHelpers:
    """ANSI 辅助函数测试。"""

    def test_strip_ansi_prefix_plain(self):
        """纯文本不变。"""
        assert _strip_ansi_prefix("hello") == "hello"

    def test_strip_ansi_prefix_single(self):
        """去除单个 ANSI 前缀。"""
        assert _strip_ansi_prefix("\033[31mhello") == "hello"

    def test_strip_ansi_prefix_multiple(self):
        """去除多个连续 ANSI 前缀。"""
        assert _strip_ansi_prefix("\033[1m\033[31mworld") == "world"

    def test_strip_ansi_prefix_trailing_ansi_kept(self):
        """行中/行尾 ANSI 保留。"""
        result = _strip_ansi_prefix("\033[31mred\033[0m text")
        assert result == "red\033[0m text"

    def test_preserve_ansi_prefix_plain(self):
        """纯文本返回空前缀。"""
        prefix, content = _preserve_ansi_prefix("hello")
        assert prefix == ""
        assert content == "hello"

    def test_preserve_ansi_prefix_with_ansi(self):
        """分离 ANSI 前缀和内容。"""
        prefix, content = _preserve_ansi_prefix("\033[1m\033[31mhello")
        assert prefix == "\033[1m\033[31m"
        assert content == "hello"

    def test_preserve_ansi_prefix_trailing_kept(self):
        """行中 ANSI 保留在 content 中。"""
        prefix, content = _preserve_ansi_prefix("\033[31mred\033[0m text")
        assert prefix == "\033[31m"
        assert content == "red\033[0m text"
