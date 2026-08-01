"""测试 _style_fp.py — 稳定可哈希样式指纹。

覆盖：int fg / TrueColor fg / None 默认值三场景；
TrueColor 与同 RGB int 指纹不同（不依赖 id()/对象生命周期）。
"""

from __future__ import annotations

from src.tui.core.color import TrueColor
from src.tui.core.style import Style
from src.tui.ink._style_fp import style_fingerprint


class TestStyleFingerprint:
    """样式指纹值稳定性。"""

    def test_int_fg_regression(self) -> None:
        """int fg：相同 Style 指纹相等、不同 Style 指纹不等。"""
        s1 = Style(fg=45)
        s2 = Style(fg=45)
        s3 = Style(fg=46)
        assert style_fingerprint(s1) == style_fingerprint(s2)
        assert style_fingerprint(s1) != style_fingerprint(s3)

    def test_truecolor_fg_regression(self) -> None:
        """TrueColor fg：相同 RGB 指纹相等、不同 RGB 指纹不等。"""
        s1 = Style(fg=TrueColor(45, 67, 89))
        s2 = Style(fg=TrueColor(45, 67, 89))
        s3 = Style(fg=TrueColor(45, 67, 90))
        assert style_fingerprint(s1) == style_fingerprint(s2)
        assert style_fingerprint(s1) != style_fingerprint(s3)

    def test_none_defaults_regression(self) -> None:
        """None 默认：无样式 Style 指纹相等；无样式与有样式不等。"""
        s1 = Style()
        s2 = Style()
        assert style_fingerprint(s1) == style_fingerprint(s2)
        assert style_fingerprint(Style()) != style_fingerprint(Style(fg=45))

    def test_truecolor_differs_from_int_regression(self) -> None:
        """TrueColor 与同 RGB int 指纹不同（int 保持 256 色号语义）。"""
        tc = Style(fg=TrueColor(45, 45, 45))
        idx = Style(fg=45)
        assert style_fingerprint(tc) != style_fingerprint(idx)
