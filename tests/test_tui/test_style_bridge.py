"""桥接方法测试 — tui.core.Style ↔ rich.style.Style 双向转换。

测试覆盖：
  - to_rich(): int 256 色号 / TrueColor / 全部属性 / 空 Style
  - from_rich(): 256 色号 / TrueColor / 无颜色 / None 布尔值
  - Roundtrip: Style → to_rich → from_rich → 验证所有字段一致

桥梁方法均为纯函数（延迟加载 third-party），无需 mock。
测试使用真实 rich.style.Style 实例，确保与 Rich 实际 API 兼容。
"""

from __future__ import annotations

import pytest
from rich.color import Color as RichColor
from rich.style import Style as RichStyle

from src.tui.core.color import TrueColor
from src.tui.core.style import Style


# ═══════════════════════════════════════════════════════════
# to_rich — tui.core.Style → rich.style.Style
# ═══════════════════════════════════════════════════════════


class TestToRich:
    """tui.core.Style → rich.style.Style 转换测试。"""

    def test_to_rich_int_fg(self):
        """int fg（256 色号）→ rich.color.number 正确。"""
        s = Style(fg=196)
        rich = s.to_rich()
        assert isinstance(rich, RichStyle)
        assert rich.color is not None
        assert rich.color.number == 196

    def test_to_rich_truecolor(self):
        """TrueColor fg → rich.color.triplet 正确。"""
        s = Style(fg=TrueColor(255, 100, 50))
        rich = s.to_rich()
        assert isinstance(rich, RichStyle)
        assert rich.color is not None
        assert rich.color.triplet is not None
        assert rich.color.triplet.red == 255
        assert rich.color.triplet.green == 100
        assert rich.color.triplet.blue == 50

    def test_to_rich_all_attributes(self):
        """全部属性（fg+bg+4 布尔）均正确映射。"""
        s = Style(
            fg=27,
            bg=TrueColor(40, 50, 60),
            bold=True,
            italic=True,
            dim=True,
            underline=True,
        )
        rich = s.to_rich()
        assert rich.color is not None
        assert rich.color.number == 27
        assert rich.bgcolor is not None
        assert rich.bgcolor.triplet is not None
        assert rich.bgcolor.triplet.red == 40
        assert rich.bgcolor.triplet.green == 50
        assert rich.bgcolor.triplet.blue == 60
        assert rich.bold is True
        assert rich.italic is True
        assert rich.dim is True
        assert rich.underline is True

    def test_to_rich_empty_style(self):
        """空 Style（全部默认）→ 返回无样式的 RichStyle。"""
        s = Style()
        rich = s.to_rich()
        assert rich.color is None
        assert rich.bgcolor is None
        # rich 未设置布尔属性时返回 None
        assert rich.bold is None
        assert rich.italic is None

    def test_to_rich_partial_bools(self):
        """仅部分布尔属性（italic=True）→ 仅设置 True 的属性。"""
        s = Style(italic=True)
        rich = s.to_rich()
        assert rich.italic is True
        # rich 未设置的布尔属性返回 None
        assert rich.bold is None
        assert rich.dim is None
        assert rich.underline is None


# ═══════════════════════════════════════════════════════════
# from_rich — rich.style.Style → tui.core.Style
# ═══════════════════════════════════════════════════════════


class TestFromRich:
    """rich.style.Style → tui.core.Style 转换测试。"""

    def test_from_rich_int_fg(self):
        """rich style 的 color.number=196 → fg=196, bold=True。"""
        rich = RichStyle(color=RichColor.from_ansi(196), bold=True)
        s = Style.from_rich(rich)
        assert s.fg == 196
        assert s.bold is True
        assert s.bg is None

    def test_from_rich_truecolor(self):
        """rich style 的 TrueColor → tui.core.TrueColor。"""
        rich = RichStyle(color=RichColor.from_rgb(100, 150, 200))
        s = Style.from_rich(rich)
        assert isinstance(s.fg, TrueColor)
        assert s.fg.r == 100
        assert s.fg.g == 150
        assert s.fg.b == 200
        assert s.bg is None

    def test_from_rich_no_color(self):
        """无颜色的 rich.style.Style → fg=None, bg=None。"""
        rich = RichStyle()
        s = Style.from_rich(rich)
        assert s.fg is None
        assert s.bg is None

    def test_from_rich_none_bools(self):
        """rich style 的 bold=None → from_rich → bold=False。"""
        rich = RichStyle(bold=None, italic=None)
        s = Style.from_rich(rich)
        assert s.bold is False
        assert s.italic is False

    def test_from_rich_fg_and_bg(self):
        """rich style 同时设置 color 和 bgcolor → 均正确映射。"""
        rich = RichStyle(
            color=RichColor.from_ansi(196),
            bgcolor=RichColor.from_ansi(27),
        )
        s = Style.from_rich(rich)
        assert s.fg == 196
        assert s.bg == 27


# ═══════════════════════════════════════════════════════════
# Roundtrip — 双向转换一致性
# ═══════════════════════════════════════════════════════════


class TestRoundtrip:
    """Style → to_rich → from_rich 往返转换一致性。"""

    def test_roundtrip_int_fg(self):
        """int fg + bool 属性往返一致。"""
        s = Style(fg=196, bold=True, italic=True)
        rich = s.to_rich()
        s2 = Style.from_rich(rich)
        assert s2.fg == s.fg
        assert s2.bold == s.bold
        assert s2.italic == s.italic
        assert s2.dim == s.dim
        assert s2.underline == s.underline

    def test_roundtrip_truecolor(self):
        """TrueColor fg 往返一致。"""
        s = Style(fg=TrueColor(200, 100, 50), bold=True)
        rich = s.to_rich()
        s2 = Style.from_rich(rich)
        assert isinstance(s2.fg, TrueColor)
        assert s2.fg.r == 200
        assert s2.fg.g == 100
        assert s2.fg.b == 50
        assert s2.bold is True

    def test_roundtrip_all_attributes(self):
        """全部属性同时设置（fg(int)+bg(int)+bools）往返一致。"""
        s = Style(fg=196, bg=27, bold=True, italic=True, dim=True, underline=True)
        rich = s.to_rich()
        s2 = Style.from_rich(rich)
        assert s2.fg == 196
        assert s2.bg == 27
        assert s2.bold is True
        assert s2.italic is True
        assert s2.dim is True
        assert s2.underline is True

    def test_roundtrip_empty(self):
        """空 Style（全部默认）往返一致。"""
        s = Style()
        rich = s.to_rich()
        s2 = Style.from_rich(rich)
        assert s2.fg is None
        assert s2.bg is None
        assert s2.bold is False
        assert s2.italic is False
        assert s2.dim is False
        assert s2.underline is False

    def test_roundtrip_truecolor_fg_bg(self):
        """TrueColor fg + int bg + bools 往返一致。"""
        s = Style(
            fg=TrueColor(100, 150, 200),
            bg=27,
            bold=True,
            dim=True,
        )
        rich = s.to_rich()
        s2 = Style.from_rich(rich)
        assert isinstance(s2.fg, TrueColor)
        assert s2.fg.r == 100 and s2.fg.g == 150 and s2.fg.b == 200
        assert s2.bg == 27
        assert s2.bold is True
        assert s2.dim is True
        assert s2.italic is False
        assert s2.underline is False
