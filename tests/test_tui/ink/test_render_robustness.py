"""渲染健壮性回归测试 — 畸形输入/极端值不导致渲染崩溃。

覆盖本次「渲染错误修复」：
  - OverflowError 捕获：``int(float('inf'))`` 在布局/控件层不再崩溃
    （修复前 try/except 仅捕获 TypeError/ValueError，OverflowError 泄漏
    中断整帧渲染）；
  - 控件 items/value 不可迭代防御：ListView/SelectInput/MultiSelect 对
    None/标量/字典 items、TextInput 对非 str value 渲染安全；
  - _measure_cache props 引用级缓存（PERF-14）：无变化帧 TEXT 布局零重建，
    同 props 引用 + avail_w + fill 命中直接复用 w/h（正确性由本文件 +
    test_layout_cache_key.py 双保险）；
  - _find_committed_chat 未挂载快速路径（PERF-15）：无 committed-chat 的
    组件树每帧零 DFS；
  - Box/Text 标准门面（React Ink 生态对齐）：``<Box>``/``<Text>`` 函数
    组件与 host 等价。
"""

from __future__ import annotations

import math

from src.tui.core.style import Style
from src.tui.ink import h, TEXT, StyledRun, BOX, Row, Column
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.widgets import (
    ListView, SelectInput, MultiSelect, TextInput, ProgressBar,
    Divider, Spinner, Panel, Tree, Box, Text,
)


def _render(el, width=20, height=24):
    """渲染元素树，返回 Frame（不抛异常即通过）。"""
    r = Reconciler()
    root = r.create_root()
    r.render(root, el, width, height)
    return render_frame(root, width)


# ═══════════════════════════════════════════════════════════
# Box/Text 标准门面（React Ink 生态对齐）
# ═══════════════════════════════════════════════════════════


class TestBoxTextFacade:
    """``<Box>``/``<Text>`` 函数门面与 host 等价。"""

    def test_box_facade(self):
        el = h(Box, {"width": 20}, h(Text, {"children": "hello"}))
        f = _render(el, 40)
        assert f.lines[0].plain == "hello"

    def test_text_facade(self):
        el = h(Box, {"width": 20}, h(Text, {"children": "中文"}))
        f = _render(el, 40)
        assert f.lines[0].plain == "中文"

    def test_box_column_default(self):
        """Box 默认 column（与 Flex 一致）。"""
        el = h(Box, {"width": 10}, h(Text, {"children": "a"}), h(Text, {"children": "b"}))
        f = _render(el, 10)
        assert [ln.plain for ln in f.lines] == ["a", "b"]

    def test_box_row_explicit(self):
        el = h(Box, {"width": 10, "flexDirection": "row"},
                h(Text, {"children": "a"}), h(Text, {"children": "b"}))
        f = _render(el, 10)
        assert "".join(ln.plain for ln in f.lines) == "ab"

    def test_facade_equivalent_to_host(self):
        """门面与 h(BOX)/h(TEXT) 输出一致。"""
        f1 = _render(h(Box, {"width": 20}, h(Text, {"children": "eq"})), 40)
        f2 = _render(h(BOX, {"width": 20}, h(TEXT, {"children": "eq"})), 40)
        assert [ln.plain for ln in f1.lines] == [ln.plain for ln in f2.lines]

    def test_text_varargs_children(self):
        """Text 变参用法（h(Text, None, 'a')）归一化为文本。"""
        el = h(Box, {"width": 20}, h(Text, None, "a"))
        f = _render(el, 40)
        assert f.lines[0].plain == "a"

    def test_text_styled_prop(self):
        """Text styled props 透传。"""
        el = h(Box, {"width": 20}, h(Text, {"styled": [StyledRun("hi", Style(fg=1))]}))
        f = _render(el, 40)
        assert f.lines[0].plain == "hi"


# ═══════════════════════════════════════════════════════════
# OverflowError 捕获（int(float('inf')) 不再崩溃）
# ═══════════════════════════════════════════════════════════


class TestOverflowIntGuard:
    """布局/控件 int() 转换对 inf/nan 的兜底（修复前 OverflowError 泄漏）。"""

    def test_text_width_inf(self):
        f = _render(h(TEXT, {"width": math.inf, "children": "x"}))
        assert f is not None

    def test_box_width_inf(self):
        f = _render(h(BOX, {"width": math.inf}, [h(TEXT, {"children": "x"})]))
        assert f is not None

    def test_box_min_width_inf(self):
        f = _render(h(Column, {"minWidth": math.inf}, [h(TEXT, {"children": "x"})]))
        assert f is not None

    def test_box_max_width_inf(self):
        f = _render(h(Column, {"maxWidth": math.inf}, [h(TEXT, {"children": "x"})]))
        assert f is not None

    def test_height_inf(self):
        f = _render(h(Column, {"height": math.inf}, [h(TEXT, {"children": "x"})]))
        assert f is not None

    def test_max_height_inf(self):
        f = _render(h(Column, {"maxHeight": math.inf}, [h(TEXT, {"children": "x"})]))
        assert f is not None

    def test_padding_inf(self):
        f = _render(h(BOX, {"padding": math.inf}, [h(TEXT, {"children": "x"})]))
        assert f is not None

    def test_border_inf(self):
        f = _render(h(BOX, {"border": math.inf}, [h(TEXT, {"children": "x"})]))
        assert f is not None

    def test_gap_inf(self):
        f = _render(h(Row, {"gap": math.inf}, [h(TEXT, {"children": "x"})]))
        assert f is not None

    def test_flex_grow_inf(self):
        f = _render(h(Row, {"width": 20}, [
            h(TEXT, {"children": "x", "flexGrow": math.inf}),
            h(TEXT, {"children": "y"}),
        ]))
        assert f is not None

    def test_flex_basis_inf(self):
        f = _render(h(Row, {"width": 20}, [
            h(TEXT, {"children": "x", "flexBasis": math.inf}),
            h(TEXT, {"children": "y"}),
        ]))
        assert f is not None

    def test_absolute_top_inf(self):
        f = _render(h(BOX, {"position": "relative", "height": 5}, [
            h(TEXT, {"children": "x", "position": "absolute", "top": math.inf}),
        ]))
        assert f is not None

    def test_spacer_height_inf(self):
        f = _render(h("spacer", {"height": math.inf}))
        assert f is not None

    def test_newline_count_inf(self):
        f = _render(h("newline", {"count": math.inf}))
        assert f is not None

    def test_widgets_inf(self):
        """控件 int() 转换对 inf 的兜底。"""
        _render(h(ProgressBar, {"percent": 0.5, "width": math.inf}), 20)
        _render(h(Divider, {"width": math.inf}), 20)
        _render(h(Spinner, {"interval": math.inf}), 20)
        _render(h(Panel, {"width": math.inf}), 20)
        _render(h(SelectInput, {"items": ["a"], "limit": math.inf, "focus": False}), 20)
        _render(h(MultiSelect, {"items": ["a"], "limit": math.inf, "focus": False}), 20)
        _render(h(ListView, {"items": ["a"], "height": math.inf, "focus": False}), 20)
        _render(h(Tree, {"data": ["a"], "indent": math.inf, "focus": False}), 20)


# ═══════════════════════════════════════════════════════════
# 控件 items/value 不可迭代防御
# ═══════════════════════════════════════════════════════════


class TestControlItemsRobust:
    """控件 items/value 畸形输入渲染安全（修复前 TypeError 崩溃）。"""

    def test_listview_items_none(self):
        f = _render(h(ListView, {"items": None, "height": 2}))
        assert len(f.lines) == 2  # 视口占位

    def test_listview_items_float(self):
        f = _render(h(ListView, {"items": 3.14, "height": 2}))
        assert len(f.lines) == 2

    def test_listview_items_bool(self):
        f = _render(h(ListView, {"items": True, "height": 2}))
        assert len(f.lines) == 2

    def test_select_input_items_bool(self):
        f = _render(h(SelectInput, {"items": True, "focus": False}))
        assert f is not None

    def test_select_input_items_dict(self):
        f = _render(h(SelectInput, {"items": {"a": 1}, "focus": False}))
        assert f is not None

    def test_multi_select_items_float(self):
        f = _render(h(MultiSelect, {"items": 3.14, "focus": False}))
        assert f is not None

    def test_text_input_value_float(self):
        f = _render(h(TextInput, {"value": 3.14, "focus": False}))
        assert f is not None

    def test_text_input_value_dict(self):
        f = _render(h(TextInput, {"value": {"a": 1}, "focus": False}))
        assert f is not None

    def test_normalize_items_shared(self):
        """_normalize_items 对不可迭代回退空列表（SelectInput/MultiSelect 共用）。"""
        from src.tui.ink.widgets.interactive import _normalize_items
        assert _normalize_items(None) == []
        assert _normalize_items(3.14) == []
        assert _normalize_items(True) == []
        # dict 可迭代（迭代键），不是不可迭代场景——保持既有行为（键作为 label）
        assert _normalize_items({"a": 1}) == [{"label": "a", "value": "a"}]


# ═══════════════════════════════════════════════════════════
# _measure_cache 正确性（PERF-14）
# ═══════════════════════════════════════════════════════════


class TestMeasureCacheCorrectness:
    """props 引用级测量缓存：命中与失效均正确。"""

    def _frame_plain(self, el, width=40):
        return [ln.plain for ln in _render(el, width).lines]

    def test_same_element_reused(self):
        """同 el 对象复用 → 输出一致。"""
        el = h(Column, None, [h(TEXT, {"key": "t", "styled": [StyledRun("hi", None)]})])
        p1 = self._frame_plain(el)
        p2 = self._frame_plain(el)
        assert p1 == p2 == ["hi"]

    def test_new_element_same_content(self):
        """新 el 引用但内容相同 → 输出一致（缓存 miss 后重算）。"""
        el1 = h(Column, None, [h(TEXT, {"key": "t", "styled": [StyledRun("hi", None)]})])
        el2 = h(Column, None, [h(TEXT, {"key": "t", "styled": [StyledRun("hi", None)]})])
        assert self._frame_plain(el1) == self._frame_plain(el2)

    def test_content_change_updates(self):
        """内容变化 → 缓存失效更新。"""
        el1 = h(Column, None, [h(TEXT, {"key": "t", "styled": [StyledRun("hi", None)]})])
        r = Reconciler()
        root = r.create_root()
        r.render(root, el1, 40, 24)
        assert [ln.plain for ln in render_frame(root, 40).lines] == ["hi"]
        el2 = h(Column, None, [h(TEXT, {"key": "t", "styled": [StyledRun("bye", None)]})])
        r.render(root, el2, 40, 24)
        assert [ln.plain for ln in render_frame(root, 40).lines] == ["bye"]

    def test_width_change_updates(self):
        """显式 width 变化 → 缓存失效更新（行数/内容不同）。"""
        el1 = h(TEXT, {"key": "t", "children": "abcdefghij", "width": 5})
        el2 = h(TEXT, {"key": "t", "children": "abcdefghij", "width": 8})
        r = Reconciler()
        root = r.create_root()
        r.render(root, el1, 40, 24)
        p1 = [ln.plain for ln in render_frame(root, 40).lines]
        r.render(root, el2, 40, 24)
        p2 = [ln.plain for ln in render_frame(root, 40).lines]
        assert p1 != p2  # 宽度变化必须更新

    def test_fill_change_updates(self):
        """fill 上下文变化 → 缓存失效（row 内 fill=False vs column fill=True）。"""
        el_row = h(Row, {"width": 40}, [
            h(TEXT, {"key": "t", "children": "hello world"}),
            h(TEXT, {"children": "x"}),
        ])
        el_col = h(Column, {"width": 40}, [
            h(TEXT, {"key": "t", "children": "hello world"}),
            h(TEXT, {"children": "x"}),
        ])
        # 两者都渲染正常（fill 不同 → 缓存键不同，各自独立）
        assert _render(el_row, 40) is not None
        assert _render(el_col, 40) is not None

    def test_input_area_standard_component_renders(self):
        """InputArea 标准组件（Column + TEXT）正常渲染（无自定义 host）。"""
        from src.tui.app.input_area import InputArea
        el = h(InputArea, {
            "text": "hi", "cursor_pos": 1, "prompt": "> ",
            "completion": None, "status_active": False, "cpu": 0, "mem": 0,
            "history_search": None, "width": 40,
        })
        r = Reconciler()
        root = r.create_root()
        r.render(root, el, 40, 24)
        f = render_frame(root, 40)
        assert any("> " in ln.plain for ln in f.lines), "输入行应渲染"

        def _has_host_tag(f):
            # InputArea 返回 Column 组件树——不应出现自定义 host "input-area"
            if f.is_host and f.type == "input-area":
                return True
            c = f.child
            while c:
                if _has_host_tag(c):
                    return True
                c = c.sibling
            return False

        assert not _has_host_tag(root), "InputArea 不应产生自定义 host"


# ═══════════════════════════════════════════════════════════
# _find_committed_chat 快速路径（PERF-15）
# ═══════════════════════════════════════════════════════════


class TestCommittedChatFastPath:
    """StaticLines 未挂载快速路径：纯 TEXT 树零 DFS。"""

    def test_no_committed_flag_false(self):
        """无 StaticLines 的组件树 → _committed_chat_present=False。"""
        el = h(Column, None, [h(TEXT, {"children": "x"})])
        r = Reconciler()
        root = r.create_root()
        r.render(root, el, 40, 24)
        assert getattr(root, "_committed_chat_present", False) is False

    def test_with_committed_flag_true(self):
        """有 StaticLines（AppModel 聊天历史）→ _committed_chat_present=True。"""
        from src.tui.app.model import AppModel
        from src.tui.app.app import build_app_element
        from src.renderer.ansi.helpers import AnsiLine
        model = AppModel()
        model.append_committed("content", [AnsiLine.of("hello", None)])
        r = Reconciler()
        root = r.create_root()
        r.render(root, build_app_element(model, 40), 40, 24)
        assert getattr(root, "_committed_chat_present", False) is True

    def test_find_committed_none_for_pure_text(self):
        """纯 TEXT 树 _find_committed_chat 返回 None（快速路径）。"""
        from src.tui.ink.components import _find_committed_chat
        el = h(Column, None, [h(TEXT, {"children": "x"})])
        r = Reconciler()
        root = r.create_root()
        r.render(root, el, 40, 24)
        assert _find_committed_chat(root) is None
