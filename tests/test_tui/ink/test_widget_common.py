"""test_widget_common — 控件库公共纯辅助收敛架构固化。

架构决策（2026-08-05 架构优化）：``_clamp_index`` / ``_children`` / ``_color``
/ ``_call`` 原在多个控件模块各自定义（行为逐字一致），收敛至
``widgets/_widget_common.py`` **单一真源**。本测试固化：
  - _widget_common 独立可导入、仅依赖 helpers（Layer 0/1）
  - 各控件模块 re-export / 薄委托与 _widget_common 同对象（单一真源防漂移）
  - 收敛后各控件模块无本地重复定义残留
"""

from __future__ import annotations

import ast
from pathlib import Path


def _widgets_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent / "src" / "tui" / "ink" / "widgets"


class TestWidgetCommonModule:
    """_widget_common.py 模块边界。"""

    def test_module_file_exists(self) -> None:
        assert (_widgets_dir() / "_widget_common.py").is_file()

    def test_symbols_available(self) -> None:
        from src.tui.ink.widgets import _widget_common
        for name in ("_clamp_index", "_children", "_color", "_call"):
            assert callable(getattr(_widget_common, name)), name

    def test_dependencies_minimal(self) -> None:
        """_widget_common 仅依赖 helpers（Layer 0/1），不依赖控件模块。"""
        source = (_widgets_dir() / "_widget_common.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                m = node.module or ""
                assert "widgets" not in m, f"_widget_common 不应依赖控件模块 {m}"


class TestSingleSource:
    """收敛后各模块与 _widget_common 同对象（单一真源）。"""

    def test_interactive_common_single_source(self) -> None:
        from src.tui.ink.widgets import _interactive_common, _widget_common
        assert _interactive_common._clamp_index is _widget_common._clamp_index
        assert _interactive_common._color is _widget_common._color
        assert _interactive_common._call is _widget_common._call

    def test_display_common_single_source(self) -> None:
        from src.tui.ink.widgets import _display_common, _widget_common
        assert _display_common._color is _widget_common._color

    def test_menu_single_source(self) -> None:
        from src.tui.ink.widgets import menu, _widget_common
        assert menu._clamp_index is _widget_common._clamp_index

    def test_focus_single_source(self) -> None:
        from src.tui.ink.widgets import focus, _widget_common
        assert focus._children is _widget_common._children
        assert focus._call is _widget_common._call


class TestNoLocalDuplicate:
    """收敛后各控件模块无本地重复定义（防双实现漂移）。"""

    def _has_local_def(self, fname: str, sym: str) -> bool:
        source = (_widgets_dir() / fname).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == sym:
                return True
        return False

    def test_menu_no_local_clamp(self) -> None:
        assert not self._has_local_def("menu.py", "_clamp_index")

    def test_tabs_no_local_clamp(self) -> None:
        assert not self._has_local_def("tabs.py", "_clamp_index")

    def test_tree_no_local_clamp(self) -> None:
        assert not self._has_local_def("tree.py", "_clamp_index")

    def test_listview_no_local_clamp(self) -> None:
        assert not self._has_local_def("listview.py", "_clamp_index")

    def test_search_input_no_local_clamp(self) -> None:
        assert not self._has_local_def("search_input.py", "_clamp_index")

    def test_layout_no_local_children(self) -> None:
        assert not self._has_local_def("layout.py", "_children")

    def test_panel_no_local_children(self) -> None:
        assert not self._has_local_def("_panel.py", "_children")

    def test_focus_no_local_children_call(self) -> None:
        assert not self._has_local_def("focus.py", "_children")
        assert not self._has_local_def("focus.py", "_call")

    def test_checkbox_no_local_call(self) -> None:
        assert not self._has_local_def("checkbox.py", "_call")

    def test_codeblock_no_local_color(self) -> None:
        assert not self._has_local_def("codeblock.py", "_color")

    def test_display_common_no_local_color(self) -> None:
        assert not self._has_local_def("_display_common.py", "_color")

    def test_interactive_common_no_local_call(self) -> None:
        assert not self._has_local_def("_interactive_common.py", "_call")


class TestBehaviourSmoke:
    """收敛后行为不变冒烟。"""

    def test_clamp_index(self) -> None:
        from src.tui.ink.widgets._widget_common import _clamp_index
        assert _clamp_index(5, 3) == 2
        assert _clamp_index(-1, 3) == 0
        assert _clamp_index(1, 3) == 1
        assert _clamp_index(1, 0) == 0

    def test_color(self) -> None:
        from src.tui.ink.widgets._widget_common import _color
        assert _color(None, 6) == 6
        assert _color(None, 23) == 23
        assert _color("red") == 1

    def test_children(self) -> None:
        from src.tui.ink.widgets._widget_common import _children
        assert _children({}) == ()
        assert _children({"children": None}) == ()
        assert _children({"children": [1, 2]}) == (1, 2)
        assert _children({"children": 1}) == (1,)

    def test_call(self) -> None:
        from src.tui.ink.widgets._widget_common import _call
        calls = []
        _call(lambda *a: calls.append(a), 1, 2)
        assert calls == [(1, 2)]
        _call(None, 1)  # None 安全
        _call(lambda: 1 / 0)  # 异常吞掉
