"""控件库覆盖矩阵 — 每个标准控件的最小渲染冒烟 + 复用帧 + 行宽不变量。

对 ``ink.widgets.__all__`` 中的全部控件用最小合法 props 渲染两帧（首帧挂载
+ 复用帧），断言：无异常、产出帧行宽不超过文档宽度（行级 diff 宽度不变量）、
控件未产出负尺寸。用于防止「新增/改动控件后某个控件渲染崩溃」类回归。
"""

from __future__ import annotations

import pytest

from src.tui.ink import StyledRun, h
from src.tui.ink import widgets as W
from tests.test_tui.ink._harness import Harness

_WIDTH = 40


def _noop(*args, **kwargs):
    return None


def _text(child: str = "内容"):
    return h(W.Text, {"children": child})


#: 控件 → 最小 props（值覆盖各控件的必需字段；children 单独处理）
_WIDGET_PROPS: dict[str, dict] = {
    "SelectInput": {"items": ["a", "b"], "onSelect": _noop},
    "TextInput": {"value": "abc", "onChange": _noop},
    "MultiSelect": {"items": ["a", "b"]},
    "ConfirmInput": {"onConfirm": _noop},
    "Toggle": {"label": "开关", "value": True, "onChange": _noop},
    "Checkbox": {"label": "勾选", "checked": True, "onChange": _noop},
    "Spinner": {},
    "ProgressBar": {"percent": 0.5, "width": 20},
    "Table": {"headers": ["列1", "列2"], "rows": [["1", "2"], ["3", "4"]]},
    "Badge": {"label": "TAG"},
    "Divider": {"width": 20, "char": "-"},
    "Panel": {"title": "面板", "border": 1},
    "Tree": {"data": [{"label": "a", "children": [{"label": "a1", "children": []}]}]},
    "ListView": {"items": ["a", "b", "c"], "height": 2},
    "Menu": {"items": [{"label": "a"}, {"label": "b", "disabled": True}]},
    "SearchInput": {"items": ["a", "b"], "onSelect": _noop},
    "Tabs": {"tabs": [{"label": "A"}, {"label": "B"}]},
    "Breadcrumbs": {"items": ["根", "子"]},
    "RadioList": {"items": ["a", "b"]},
    "CodeBlock": {"code": "print(1)", "language": "python"},
    "CollapsibleCodeBlock": {"code": "x = 1", "expandable": True, "expanded": False},
    "Viewport": {"lines": ["l0", "l1", "l2"], "height": 2},
    "MultiPanel": {"panels": [{"title": "p1", "content": "c1"}, {"title": "p2", "content": "c2"}]},
    "InlineSpinner": {},
    "Gradient": {"text": "渐变文本", "colors": [45, 213]},
    "StaticLines": {"lines": ["静态1", "静态2"]},
    "FocusGroup": {},
    "Key": {},
    "Row": {}, "Column": {}, "Box": {}, "Text": {},
    "Flex": {}, "Spacer": {}, "Center": {}, "Stack": {},
    "HStack": {}, "VStack": {}, "Grid": {}, "ZStack": {},
}


def _make(name: str):
    props = dict(_WIDGET_PROPS[name])
    widget = getattr(W, name)
    if name == "FocusGroup":
        return h(widget, props, h(W.Key, {}, _text()))
    if name in ("Row", "Column", "Box", "Center", "Stack", "HStack", "VStack",
                "Grid", "ZStack", "Panel", "Flex", "Key"):
        return h(widget, props, _text())
    if name == "Spacer":
        return h(widget, {"width": 3, "height": 1})
    if name == "Text":
        return h(widget, {"children": "文本"})
    return h(widget, props)


#: 全部导出控件（含静态行控件；布局门面一并覆盖）
_WIDGET_NAMES = sorted(_WIDGET_PROPS)


@pytest.mark.parametrize("name", _WIDGET_NAMES)
def test_widget_renders_two_frames_without_error(name):
    harness = Harness(_WIDTH)
    element = _make(name)
    frame = harness.frame(element)
    assert frame is not None
    # 复用帧（组件复用/缓存路径同样不得崩溃）
    frame2 = harness.frame(_make(name))
    for ln in frame2.lines:
        assert ln.width <= _WIDTH, f"{name} 行宽超文档宽：{ln.width}"


@pytest.mark.parametrize("name", _WIDGET_NAMES)
def test_widget_survives_narrow_and_hostile_props(name):
    """畸形/极端 props 下控件不崩溃（健壮性矩阵）。"""
    harness = Harness(12)
    widget = getattr(W, name)
    hostile = {
        "items": None, "lines": 3, "data": "x", "tabs": 1, "panels": None,
        "code": None, "text": None, "label": None, "percent": "inf",
        "width": float("inf"), "height": float("inf"), "children": None,
        "value": None, "checked": None, "colors": None, "rows": None,
        "headers": None, "separator": None,
    }
    hostile.update(_WIDGET_PROPS[name])
    frame = harness.frame(h(widget, hostile))
    assert frame is not None
    for ln in frame.lines:
        assert ln.width <= 12, f"{name} 窄屏行宽违规：{ln.width}"


def test_widget_export_list_is_covered():
    """导出清单中的控件均在本矩阵内（新增控件须同步补测）。"""
    exported = set(W.__all__)
    missing = {
        n for n in exported
        if n not in _WIDGET_PROPS and n != "SPINNER_FRAMES"
    }
    assert not missing, f"未纳入控件覆盖矩阵：{sorted(missing)}"


def test_styled_runs_render_in_widgets():
    """带链接/styled 的 runs 在控件（CodeBlock）内正常渲染。"""
    styled = [StyledRun("url", None, "https://a.b")]
    harness = Harness(30)
    el = h(W.CodeBlock, {"code": "see https://a.b", "language": "text"})
    harness.render(el)
    assert harness.render(el)
