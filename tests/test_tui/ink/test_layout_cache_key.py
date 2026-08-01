"""测试 ink/layout.py — _wrap_cache 稳定样式指纹（BUG-T1 回归）。

验证缓存键不再依赖 id()（对象 GC 后可能复用导致错误缓存命中/未命中），
改用 _style_fp.style_fingerprint 稳定值指纹（同值同构 Style → 相同指纹）。
"""

from __future__ import annotations

from src.tui.ink.element import h, TEXT
from src.tui.ink.reconciler import Reconciler
from src.tui.core.style import Style


def _text_fiber(style: Style | None = None):
    """渲染单 TEXT 元素（含 style 属性）并返回其 host fiber。"""
    r = Reconciler()
    root = r.create_root()
    el = h(TEXT, {"children": "hello world", "style": style})
    r.render(root, el, 80, 24)
    return root.child


class TestWrapCacheStableKey:
    """BUG-T1 — _wrap_cache 样式指纹稳定（值驱动，非 id()）。

    新缓存结构 ``(ref, (width, text_wrap), style_fp, lines)``：
      - cache[0] = ref（内容/引用）
      - cache[1] = (width, text_wrap)
      - cache[2] = style_fp（稳定指纹，BUG-T1 断言对象）
      - cache[3] = lines
    本测试比较 cache[2]（稳定样式指纹）——值驱动，不依赖对象生命周期。
    """

    def test_wrap_cache_stable_key_regression(self):
        """渲染两次相同 text/style，缓存存在且样式指纹相等（稳定值指纹）。"""
        style = Style(fg=45)
        f1 = _text_fiber(style)
        assert f1._wrap_cache is not None
        key1 = f1._wrap_cache[2]

        f2 = _text_fiber(style)
        key2 = f2._wrap_cache[2]
        assert key1 == key2

    def test_wrap_cache_misses_on_style_change_regression(self):
        """style 变化时样式指纹不等触发重包裹。"""
        f1 = _text_fiber(Style(fg=45))
        f2 = _text_fiber(Style(fg=46))
        assert f1._wrap_cache[2] != f2._wrap_cache[2]

    def test_wrap_cache_survives_gc_regression(self):
        """del 旧 style 对象后同值 style 仍命中——验证不再依赖 id()。"""
        style_a = Style(fg=120)
        f1 = _text_fiber(style_a)
        key1 = f1._wrap_cache[2]
        # del 旧对象（id() 可能在后续分配中被复用，旧实现会产生错误命中/未命中）
        del style_a
        f2 = _text_fiber(Style(fg=120))  # 同值新对象
        key2 = f2._wrap_cache[2]
        assert key1 == key2

    def test_truecolor_style_fingerprint_regression(self):
        """TrueColor 与 256 色 int 同 RGB 值 → 指纹不同（保持样式区分）。"""
        from src.tui.core.color import TrueColor
        f_true = _text_fiber(Style(fg=TrueColor(45, 120, 200)))
        f_int = _text_fiber(Style(fg=45))
        assert f_true._wrap_cache[2] != f_int._wrap_cache[2]
