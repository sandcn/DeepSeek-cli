"""回归测试 — output_strategies.py 的 fill_style 修复。"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from rich.style import Style
from rich.text import Text

from src.api.renderer.output_strategies import CharByCharStrategy


class FakeFile:
    """模拟文件对象，捕获 write 调用。"""
    def __init__(self):
        self.buffer = bytearray()
        self._writes: list[str] = []

    def write(self, s: str) -> int:
        self._writes.append(s)
        return len(s)

    def flush(self) -> None:
        pass

    def getvalue(self) -> str:
        return ''.join(self._writes)

    @property
    def writes(self):
        return self._writes


@pytest.fixture
def fake_console():
    console = MagicMock(spec=Console)
    fake_file = FakeFile()
    console.file = fake_file
    return console, fake_file


def test_newline_fills_remaining_space_with_fill_style(fake_console):
    """Bug 1 回归：显式 \\n 应填充 fill_style 背景色到行尾。"""
    console, fake_file = fake_console

    # 模拟 console.print 为实际调用（因为 MagicMock 默认不执行）
    def print_side_effect(*args, **kwargs):
        text = args[0]
        if isinstance(text, Text):
            fake_file.write(text.plain)
        end = kwargs.get('end', '\n')
        if end:
            fake_file.write(end)

    console.print.side_effect = print_side_effect

    strategy = CharByCharStrategy()
    text = Text("ab\ncd", style="red")
    fill_style = Style(bgcolor="blue")

    strategy.write(text, console, speed=0, end='',
                   fill_style=fill_style,
                   lock=threading.Lock(), width=10)

    # "ab" 输出后，col=2，width=10，应填充 8 个空格带 fill_style
    # 然后写 \n，再输出 "cd"
    # 验证 console.print 被调用时传入了 fill_style 样式的空格
    fill_calls = [
        call for call in console.print.call_args_list
        if isinstance(call[0][0], Text)
        and call[0][0].plain == " " * 8
        and call[0][0].style == fill_style
    ]
    assert len(fill_calls) > 0, (
        "显式 \\n 分支未触发 fill_style 填充。期望 Text(' '*8, style=fill_style) 被 console.print 调用。"
    )


def test_wrapped_indent_uses_fill_style(fake_console):
    """Bug 2 回归：折行缩进空格应带 fill_style 背景色。"""
    console, fake_file = fake_console
    writes_before = []

    def print_side_effect(*args, **kwargs):
        text = args[0]
        if isinstance(text, Text):
            fake_file.write(text.plain)
        end = kwargs.get('end', '\n')
        if end:
            fake_file.write(end)

    console.print.side_effect = print_side_effect

    strategy = CharByCharStrategy()
    # 制造折行：width=5，首行缩进 2 空格，后面文字触发折行
    # "  abcde" → 缩进2 + abcde共7字符，width=5时应折行，line_indent=2
    text = Text("  abcde", style="red")
    fill_style = Style(bgcolor="blue")

    strategy.write(text, console, speed=0, end='',
                   fill_style=fill_style,
                   lock=threading.Lock(), width=5)

    # 验证缩进空格（line_indent=2）使用了 fill_style 样式的 Text
    indent_calls = [
        call for call in console.print.call_args_list
        if isinstance(call[0][0], Text)
        and call[0][0].plain == " " * 2
        and call[0][0].style == fill_style
    ]
    assert len(indent_calls) > 0, (
        "折行缩进空格未使用 fill_style 样式。期望 Text(' '*2, style=fill_style) 被 console.print 调用。"
    )


def test_newline_fill_noop_when_fill_style_none(fake_console):
    """当 fill_style=None 时，\\n 分支不产生额外填充调用。"""
    console, fake_file = fake_console
    fake_file._writes = []

    def print_side_effect(*args, **kwargs):
        text = args[0]
        if isinstance(text, Text):
            fake_file.write(text.plain)
        end = kwargs.get('end', '\n')
        if end:
            fake_file.write(end)

    console.print.side_effect = print_side_effect

    strategy = CharByCharStrategy()
    text = Text("a\nb", style="red")
    # 记录调用前的 console.print 调用次数
    call_count_before = len(console.print.call_args_list)

    strategy.write(text, console, speed=0, end='',
                   fill_style=None,
                   lock=threading.Lock(), width=10)

    # fill_style=None 时不应该有任何填充类的额外调用
    fill_calls = [
        call for call in console.print.call_args_list[call_count_before:]
        if isinstance(call[0][0], Text)
        and " " in call[0][0].plain
    ]
    # 可能有 end='' 导致的尾随，但不应有填充空格
    # 简单验证：没有纯空格超过 1 个的 Text 调用
    space_texts = [
        call[0][0].plain for call in console.print.call_args_list[call_count_before:]
        if isinstance(call[0][0], Text)
    ]
    # 最后一个可能是 end='' 空字符串或其它
    # 只需确保没有 fill_style 样式
    fill_style_calls = [
        call for call in console.print.call_args_list[call_count_before:]
        if isinstance(call[0][0], Text)
        and call[0][0].style is not None
    ]
    # 起码没有 bgcolor 样式
    bg_calls = [
        call for call in fill_style_calls
        if hasattr(call[0][0].style, 'bgcolor') and call[0][0].style.bgcolor is not None
    ]
    assert len(bg_calls) == 0, (
        f"fill_style=None 时不应产生带背景色的填充调用，实际产生了 {len(bg_calls)} 个"
    )


def test_indent_plain_write_when_fill_style_none(fake_console):
    """当 fill_style=None 时，折行缩进应仍使用 console.file.write。"""
    console, fake_file = fake_console

    def print_side_effect(*args, **kwargs):
        text = args[0]
        if isinstance(text, Text):
            fake_file.write(text.plain)
        end = kwargs.get('end', '\n')
        if end:
            fake_file.write(end)

    console.print.side_effect = print_side_effect

    strategy = CharByCharStrategy()
    text = Text("  abcde", style="red")

    # 先清空 writes 记录
    fake_file._writes = []

    strategy.write(text, console, speed=0, end='',
                   fill_style=None,
                   lock=threading.Lock(), width=5)

    # fill_style=None 时，缩进空格应通过 console.file.write 写入
    # 即 fake_file 中应有 "  " 的空格写入记录
    all_writes = ''.join(fake_file.writes)
    # 折行后应有一行缩进空格（不一定是连续"  "，因为可能有换行字符混合）
    # 至少检查缩进空格被写入了
    # 更精确：检查是否有 file.write(" " * line_indent) 的调用
    space_writes = [w for w in fake_file.writes if w == "  "]
    assert len(space_writes) > 0, (
        "fill_style=None 时，缩进空格应通过 console.file.write 写入，但未找到 '  ' 写入记录"
    )
