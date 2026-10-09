"""窗口输入：动作模型构建与坐标解析测试。

覆盖：六种动作的构建（默认值与显式值）、参数校验（按钮/次数/方向/数量/
时长/步数/投递方式/文本/按键）、坐标解析（默认窗口中心、越界、尺寸非法）、
拖动轨迹插值与动作摘要序列化。
"""

from __future__ import annotations

import pytest

from src.tools._window_input.action import (
    ClickAction,
    DragAction,
    INPUT_OPS,
    KeyAction,
    MoveAction,
    Point,
    ScrollAction,
    TextAction,
    build_action,
    describe_action,
    interpolate,
    resolve_point,
    validate_point,
)
from src.tools._window_input.result import ActionError


# ── op 集合 ─────────────────────────────────────────────

def test_input_ops_are_the_expected_actions():
    assert set(INPUT_OPS) == {"click", "move", "hover", "drag", "scroll", "key", "type"}


# ── click ───────────────────────────────────────────────

def test_build_click_defaults():
    action = build_action("click", {})
    assert isinstance(action, ClickAction)
    assert action.button == "left"
    assert action.count == 1
    assert action.x is None and action.y is None
    assert action.method == "auto"


def test_build_click_right_double_with_position():
    action = build_action("click", {"button": "RIGHT", "count": 2, "x": 10, "y": 20})
    assert action.button == "right"
    assert action.count == 2
    assert (action.x, action.y) == (10, 20)


def test_build_click_accepts_numeric_strings():
    action = build_action("click", {"count": "3", "x": "5", "y": "6"})
    assert action.count == 3 and action.x == 5 and action.y == 6


def test_build_click_rejects_bad_button():
    with pytest.raises(ActionError) as excinfo:
        build_action("click", {"button": "wheel"})
    assert "鼠标按钮非法" in str(excinfo.value)


def test_build_click_rejects_partial_position():
    with pytest.raises(ActionError):
        build_action("click", {"x": 5})


def test_build_click_rejects_negative_and_zero_count():
    with pytest.raises(ActionError):
        build_action("click", {"x": -1, "y": 0})
    with pytest.raises(ActionError):
        build_action("click", {"count": 0})
    with pytest.raises(ActionError):
        build_action("click", {"count": 11})


def test_build_click_with_modifiers():
    action = build_action("click", {"modifiers": ["ctrl", "shift"]})
    assert action.modifiers == ("ctrl", "shift")


# ── move ────────────────────────────────────────────────

def test_build_move_requires_point():
    with pytest.raises(ActionError) as excinfo:
        build_action("move", {})
    assert "x 与 y" in str(excinfo.value)


def test_build_move_ok():
    action = build_action("move", {"x": 1, "y": 2})
    assert isinstance(action, MoveAction)
    assert (action.x, action.y) == (1, 2)


# ── drag ────────────────────────────────────────────────

def test_build_drag_defaults():
    action = build_action("drag", {"to_x": 100, "to_y": 200})
    assert isinstance(action, DragAction)
    assert action.from_x is None and action.from_y is None
    assert (action.to_x, action.to_y) == (100, 200)
    assert action.button == "left"
    assert action.duration == pytest.approx(0.3)
    assert action.steps > 0


def test_build_drag_full():
    action = build_action("drag", {
        "from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4,
        "button": "middle", "duration": 1.5, "steps": 30,
    })
    assert (action.from_x, action.from_y) == (1, 2)
    assert (action.to_x, action.to_y) == (3, 4)
    assert action.button == "middle"
    assert action.duration == pytest.approx(1.5)
    assert action.steps == 30


def test_build_drag_requires_target():
    with pytest.raises(ActionError) as excinfo:
        build_action("drag", {"from_x": 1, "from_y": 2})
    assert "to_x" in str(excinfo.value)


def test_build_drag_rejects_partial_start():
    with pytest.raises(ActionError):
        build_action("drag", {"from_x": 1, "to_x": 3, "to_y": 4})


def test_build_drag_rejects_out_of_range():
    with pytest.raises(ActionError):
        build_action("drag", {"to_x": 1, "to_y": 2, "duration": -1})
    with pytest.raises(ActionError):
        build_action("drag", {"to_x": 1, "to_y": 2, "steps": 1})


# ── scroll ──────────────────────────────────────────────

def test_build_scroll_defaults():
    action = build_action("scroll", {})
    assert isinstance(action, ScrollAction)
    assert action.direction == "down"
    assert action.amount == 3
    assert action.x is None and action.y is None


def test_build_scroll_explicit():
    action = build_action("scroll", {"direction": "UP", "amount": 5, "x": 9, "y": 9})
    assert action.direction == "up"
    assert action.amount == 5
    assert (action.x, action.y) == (9, 9)


def test_build_scroll_rejects_bad_direction_and_amount():
    with pytest.raises(ActionError):
        build_action("scroll", {"direction": "diagonal"})
    with pytest.raises(ActionError):
        build_action("scroll", {"amount": 0})


# ── key / type ──────────────────────────────────────────

def test_build_key_and_type():
    key_action = build_action("key", {"key": "ctrl+s"})
    assert isinstance(key_action, KeyAction)
    assert key_action.shortcut.display() == "ctrl+s"
    text_action = build_action("type", {"text": "hello"})
    assert isinstance(text_action, TextAction)
    assert text_action.text == "hello"


def test_build_key_merges_modifiers_argument():
    """modifiers 参数与组合键写法等价（合并、去重、固定顺序）。"""
    action = build_action("key", {"key": "s", "modifiers": ["ctrl", "shift"]})
    assert action.shortcut.display() == "ctrl+shift+s"
    action2 = build_action("key", {"key": "ctrl+s", "modifiers": ["shift", "ctrl"]})
    assert action2.shortcut.display() == "ctrl+shift+s"


def test_build_key_requires_key():
    with pytest.raises(ActionError):
        build_action("key", {})


def test_build_type_requires_non_empty_text():
    with pytest.raises(ActionError):
        build_action("type", {})
    with pytest.raises(ActionError):
        build_action("type", {"text": ""})


# ── method ──────────────────────────────────────────────

def test_build_action_method_validation():
    assert build_action("click", {"method": "message"}).method == "message"
    assert build_action("click", {"method": "SENDINPUT"}).method == "sendinput"
    with pytest.raises(ActionError):
        build_action("click", {"method": "telepathy"})


def test_build_action_unknown_op():
    with pytest.raises(ActionError) as excinfo:
        build_action("teleport", {})
    assert "未知输入动作" in str(excinfo.value)


# ── 坐标解析 ─────────────────────────────────────────────

def test_resolve_point_defaults_to_center():
    assert resolve_point(None, None, 800, 600) == Point(400, 300)


def test_resolve_point_explicit():
    assert resolve_point(10, 20, 800, 600) == Point(10, 20)


def test_resolve_point_rejects_out_of_bounds():
    with pytest.raises(ActionError) as excinfo:
        resolve_point(800, 10, 800, 600)
    assert "超出窗口范围" in str(excinfo.value)


def test_resolve_point_rejects_partial():
    with pytest.raises(ActionError):
        resolve_point(1, None, 800, 600)


def test_resolve_point_rejects_bad_window():
    with pytest.raises(ActionError):
        resolve_point(None, None, 0, 0)


def test_validate_point_ok_and_bad():
    assert validate_point(Point(1, 1), 10, 10) == Point(1, 1)
    with pytest.raises(ActionError):
        validate_point(Point(10, 1), 10, 10)


# ── 插值 ────────────────────────────────────────────────

def test_interpolate_reaches_endpoint_and_keeps_order():
    points = interpolate(Point(0, 0), Point(100, 50), 5)
    assert len(points) == 5
    assert points[-1] == Point(100, 50)
    assert points[0] == Point(20, 10)
    assert all(points[i].x <= points[i + 1].x for i in range(len(points) - 1))


def test_interpolate_minimum_steps():
    points = interpolate(Point(0, 0), Point(10, 0), 1)
    assert len(points) >= 2


# ── 摘要 ────────────────────────────────────────────────

def test_describe_action_click_and_drag():
    click = describe_action(build_action("click", {"button": "right", "count": 2}))
    assert click["action"] == "click"
    assert click["button"] == "right"
    assert click["count"] == 2
    assert click["position"] == "center"

    drag = describe_action(build_action("drag", {"to_x": 5, "to_y": 6}))
    assert drag["action"] == "drag"
    assert drag["from"] == "center"
    assert drag["to"] == {"x": 5, "y": 6}


def test_describe_action_key_type_scroll():
    key = describe_action(build_action("key", {"key": "alt+f4"}))
    assert key["key"] == "alt+f4"
    assert key["main_key"] == "f4"

    text = describe_action(build_action("type", {"text": "abc"}))
    assert text["length"] == 3

    scroll = describe_action(build_action("scroll", {"amount": 4, "method": "message"}))
    assert scroll["amount"] == 4
    assert scroll["method"] == "message"
