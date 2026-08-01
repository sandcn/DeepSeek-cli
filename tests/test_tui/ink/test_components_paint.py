"""测试 ink/components.py — 内置 host paint（render_frame / _paint 隔离）。

方向2 P7（建议7）：内置 host paint（text/spacer/container）异常隔离——
单节点 paint 抛异常 → 该节点跳过、整帧仍渲染、异常不传播（与自定义 host
一致）。layout 异常仍由 session 层退避兜底（本测试不覆盖 layout）。

★ 命名说明：本文件命名 ``test_components_paint`` 而非 ``test_components``
  —— ``tests/test_tui/test_components.py`` 已存在（app 组件渲染断言），
  与 ``tests/test_tui/ink/test_components.py`` 基名冲突会触发 pytest
  import file mismatch（目录无 __init__.py 时按基名注册模块）。改用
  唯一基名避免整目录收集失败。
"""

from __future__ import annotations

from unittest.mock import patch

from src.tui.ink.fiber import Fiber
from src.tui.ink.layout import LayoutBox
from src.tui.ink.output import Line
from src.tui.ink import components as _components


class TestBuiltinPaintIsolation:
    """方向2 P7（建议7）— 内置 host paint 异常隔离。"""

    def _make_static_tree(self):
        """构造未布局（无 _wrapped_lines）的 static 容器 + 两个 text 子节点。

        手工构造 fiber 树（不经 Reconciler 布局）——使 paint 阶段走
        ``wrap_text_lines`` 回退路径（正常调和流程下 _wrapped_lines 已缓存，
        不会触发该调用；本测试直接验证 paint 隔离本身）。
        """
        root = Fiber("host", "static", {})
        root.layout_box = LayoutBox(x=0, y=0, w=80, h=2)
        child0 = Fiber("host", "text", {"children": "first"})
        child0.layout_box = LayoutBox(x=0, y=0, w=80, h=1)
        child1 = Fiber("host", "text", {"children": "second"})
        child1.layout_box = LayoutBox(x=0, y=1, w=80, h=1)
        child0.sibling = child1
        root.child = child0
        return root

    def test_text_paint_exception_isolated_regression(self):
        """单 text 节点 paint 抛异常 → 该节点跳过、整帧仍渲染、异常不传播。"""
        root = self._make_static_tree()
        calls = {"n": 0}

        def flaky(text, width, style=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("paint boom")
            return [Line.of(text, style)] if text else []

        with patch("src.tui.ink.components.wrap_text_lines", side_effect=flaky):
            frame = _components.render_frame(root, 80)  # 不抛异常
        assert frame.height == 2
        # 第一个节点 paint 失败被跳过（空行）；第二个正常绘制
        assert frame.lines[0].plain == ""
        assert frame.lines[1].plain == "second"

    def test_container_paint_exception_isolated_regression(self):
        """容器（BOX/STATIC/APP）paint（_paint_border）抛异常 → 整帧仍渲染。"""
        root = Fiber("host", "static", {})
        root.layout_box = LayoutBox(x=0, y=0, w=80, h=2)
        child0 = Fiber("host", "static", {"border": 1})
        child0.layout_box = LayoutBox(x=0, y=0, w=80, h=2)
        child1 = Fiber("host", "text", {"children": "ok"})
        child1.layout_box = LayoutBox(x=0, y=1, w=80, h=1)
        child0.sibling = child1
        root.child = child0

        with patch("src.tui.ink.components._paint_border", side_effect=RuntimeError("border boom")):
            frame = _components.render_frame(root, 80)  # 不抛异常
        assert frame.height == 2
        # child0 因 border 失败跳过（空行）；child1 正常绘制
        assert frame.lines[0].plain == ""
        assert frame.lines[1].plain == "ok"
