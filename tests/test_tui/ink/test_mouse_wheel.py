"""鼠标滚轮控件集成测试（useMouseInput 在标准控件中的消费行为）。

验证 Viewport / ListView / Tree / Menu / SelectInput 的滚轮交互：
滚轮事件（SGR 1006 解析出的 ``kind="mouse"`` 事件）经 input router 分发到
控件注册的 MouseHook，产生滚动/光标移动；非滚轮鼠标事件不消费。
"""

from __future__ import annotations

from src.tui._input_parser import KeyEvent
from src.tui.ink import ListView, Menu, SelectInput, Tree, Viewport, h
from tests.test_tui.ink._harness import Harness


def _wheel(direction: int) -> KeyEvent:
    """构造滚轮事件（direction: -1 上滚 / +1 下滚）。"""
    return KeyEvent(kind="mouse", mouse_action="wheel", mouse_wheel=direction,
                    mouse_button="none", mouse_x=1, mouse_y=1)


def _router(harness: Harness):
    cache = harness.reconciler._input_router_cache
    assert cache is not None, "router 未构建（无输入钩子）"
    return cache[1]


def test_viewport_wheel_scrolls_offset():
    lines = [f"line-{i}" for i in range(10)]
    harness = Harness(20)
    el = h(Viewport, {"lines": lines, "height": 3, "active": True})
    harness.render(el)
    assert "line-0" in harness.render(el)
    assert _router(harness)(_wheel(1)) is True
    out = harness.render(el)
    assert "line-0" not in out
    assert "line-3" in out


def test_viewport_wheel_up_clamped_at_top():
    lines = [f"line-{i}" for i in range(10)]
    harness = Harness(20)
    el = h(Viewport, {"lines": lines, "height": 3, "active": True})
    harness.render(el)
    # 已在顶部：仍消费滚轮事件（滚动语义），但偏移保持 0
    assert _router(harness)(_wheel(-1)) is True
    assert "line-0" in harness.render(el)


def test_viewport_inactive_ignores_wheel():
    lines = [f"line-{i}" for i in range(10)]
    harness = Harness(20)
    el = h(Viewport, {"lines": lines, "height": 3, "active": False})
    harness.render(el)
    cache = harness.reconciler._input_router_cache
    # 非激活：无输入钩子（router 未构建）或滚轮事件不被消费
    assert cache is None or cache[1](_wheel(1)) is False
    assert "line-0" in harness.render(el)


def test_listview_wheel_moves_cursor():
    moves = []
    items = [f"item-{i}" for i in range(6)]
    harness = Harness(20)

    def _build():
        return h(ListView, {
            "items": items,
            "height": 4,
            "onNavigate": moves.append,
        })

    harness.render(_build())
    assert _router(harness)(_wheel(1)) is True
    assert moves == [1]
    assert _router(harness)(_wheel(1)) is True
    assert moves == [1, 2]
    assert _router(harness)(_wheel(-1)) is True
    assert moves == [1, 2, 1]


def test_listview_wheel_at_bottom_not_consumed():
    items = [f"item-{i}" for i in range(3)]
    harness = Harness(20)
    el = h(ListView, {"items": items, "height": 3, "initialIndex": 2})
    harness.render(el)
    # 已在末项：滚轮下无效移动 → 放行（返回 False）
    assert _router(harness)(_wheel(1)) is False


def test_tree_wheel_moves_cursor():
    data = [
        {"label": "a", "children": []},
        {"label": "b", "children": []},
        {"label": "c", "children": []},
    ]
    harness = Harness(20)
    el = h(Tree, {"data": data})
    harness.render(el)
    assert _router(harness)(_wheel(1)) is True
    assert _router(harness)(_wheel(1)) is True
    # 末项：滚轮下不移动（不消费）
    assert _router(harness)(_wheel(1)) is False
    assert _router(harness)(_wheel(-1)) is True


def test_menu_wheel_skips_disabled():
    items = [
        {"label": "one"},
        {"label": "two", "disabled": True},
        {"label": "three"},
    ]
    harness = Harness(20)
    el = h(Menu, {"items": items})
    harness.render(el)
    assert _router(harness)(_wheel(1)) is True   # 跳过 disabled 到 three


def test_select_input_wheel_moves_selection():
    highlights = []
    items = [{"label": f"opt-{i}", "value": i} for i in range(5)]
    harness = Harness(20)

    def _build():
        return h(SelectInput, {
            "items": items,
            "onHighlight": highlights.append,
        })

    harness.render(_build())
    assert _router(harness)(_wheel(1)) is True
    assert highlights == [1]
    assert _router(harness)(_wheel(1)) is True
    assert highlights == [1, 2]


def test_click_event_not_consumed_by_wheel_handler():
    items = [f"item-{i}" for i in range(6)]
    harness = Harness(20)
    el = h(ListView, {"items": items, "height": 4})
    harness.render(el)
    click = KeyEvent(kind="mouse", mouse_action="press", mouse_button="left")
    # 轮子处理器只消费滚轮事件；点击事件无组件处理 → 放行给宿主兜底
    assert _router(harness)(click) is False
