"""输入坐标语义值（``center`` / ``50%`` / ``center+20``）测试。

覆盖 ``_window_input.action`` 的坐标解析层与三个平台共用的注入换算入口
（``resolve_point`` / ``validate_point``），以及动作构建期的负值与语义值校验。
"""

from __future__ import annotations

import pytest

from src.tools._window_input.action import (
    ActionError,
    Point,
    build_action,
    parse_coordinate,
    resolve_point,
    validate_point,
)


# ── parse_coordinate ────────────────────────────────────

def test_parse_coordinate_integers_and_numeric_strings():
    assert parse_coordinate(37, 100) == 37
    assert parse_coordinate("37", 100) == 37
    assert parse_coordinate("0", 100) == 0


def test_parse_coordinate_keywords():
    assert parse_coordinate("center", 101) == 50
    assert parse_coordinate("middle", 101) == 50
    assert parse_coordinate("CENTRE", 100) == 50
    assert parse_coordinate("left", 100) == 0
    assert parse_coordinate("top", 100) == 0
    assert parse_coordinate("right", 100) == 99
    assert parse_coordinate("bottom", 100) == 99


def test_parse_coordinate_percentages_are_clamped():
    assert parse_coordinate("50%", 101) == 50
    assert parse_coordinate("25%", 100) == 25
    assert parse_coordinate("0%", 100) == 0
    # 100% 会被夹到最后一个像素（尺寸 - 1），不会越界
    assert parse_coordinate("100%", 100) == 99
    assert parse_coordinate("150%", 100) == 99


def test_parse_coordinate_center_offsets():
    assert parse_coordinate("center+10", 100) == 60
    assert parse_coordinate("center-10", 100) == 40
    assert parse_coordinate("middle+100", 100) == 99  # 夹到边界
    assert parse_coordinate("center-100", 100) == 0


def test_parse_coordinate_rejects_unknown_values():
    with pytest.raises(ActionError) as error:
        parse_coordinate("middle-ish", 100)
    assert "无法识别" in str(error.value)
    with pytest.raises(ActionError):
        parse_coordinate("half", 100)
    with pytest.raises(ActionError):
        parse_coordinate(True, 100)


def test_parse_coordinate_rejects_bad_size():
    with pytest.raises(ActionError):
        parse_coordinate("center", 0)


# ── resolve_point / validate_point ──────────────────────

def test_resolve_point_defaults_to_center_and_parses_semantics():
    assert resolve_point(None, None, 200, 100) == Point(100, 50)
    assert resolve_point("center", "bottom", 200, 100) == Point(100, 99)
    assert resolve_point("25%", "50%", 200, 100) == Point(50, 50)
    assert resolve_point("left", "top", 200, 100) == Point(0, 0)


def test_resolve_point_requires_both_axes_and_validates_range():
    with pytest.raises(ActionError):
        resolve_point(10, None, 200, 100)
    with pytest.raises(ActionError) as error:
        resolve_point(500, 10, 200, 100)
    assert "超出窗口范围" in str(error.value)


def test_validate_point_resolves_semantic_values():
    assert validate_point(Point("center", "center+5"), 200, 100) == Point(100, 55)


# ── build_action 校验 ───────────────────────────────────

def test_build_action_keeps_semantic_coordinates():
    action = build_action("click", {"x": "center", "y": "50%"})
    assert action.x == "center" and action.y == "50%"
    action = build_action("drag", {"to_x": "right", "to_y": "bottom"})
    assert action.to_x == "right" and action.to_y == "bottom"


def test_build_action_converts_numeric_strings_to_int():
    action = build_action("click", {"x": "5", "y": "6", "count": 3})
    assert action.x == 5 and action.y == 6 and action.count == 3


def test_build_action_rejects_negative_and_unknown_coordinates():
    with pytest.raises(ActionError):
        build_action("click", {"x": -1, "y": 0})
    with pytest.raises(ActionError):
        build_action("click", {"x": "-3", "y": 0})
    with pytest.raises(ActionError):
        build_action("click", {"x": "wherever", "y": 0})
    with pytest.raises(ActionError):
        build_action("click", {"x": 1})
    with pytest.raises(ActionError):
        build_action("drag", {"to_x": 1})


def test_drag_endpoint_uses_resolve_point_on_backend():
    """拖动终点经 resolve_point 解析语义值（三平台后端共用）。"""
    action = build_action("drag", {"from_x": "left", "from_y": "top",
                                   "to_x": "right", "to_y": "bottom"})
    assert action.from_x == "left" and action.to_y == "bottom"


# ── 基准偏移（left+20 / right-10 / bottom-30） ──────────

def test_parse_coordinate_anchor_offsets():
    assert parse_coordinate("left+20", 100) == 20
    assert parse_coordinate("top+5", 100) == 5
    assert parse_coordinate("right-10", 100) == 89
    assert parse_coordinate("bottom-30", 100) == 69
    assert parse_coordinate("LEFT+20", 100) == 20
    # 结果自动夹到有效像素范围
    assert parse_coordinate("left-50", 100) == 0
    assert parse_coordinate("right+50", 100) == 99
    # 与既有中心偏移写法共存
    assert parse_coordinate("center+10", 100) == 60


def test_parse_coordinate_anchor_offsets_reject_unknown_anchor():
    with pytest.raises(ActionError):
        parse_coordinate("middleish+10", 100)
    with pytest.raises(ActionError):
        parse_coordinate("left+", 100)


def test_build_action_accepts_anchor_offsets():
    action = build_action("click", {"x": "left+15", "y": "bottom-20"})
    assert action.x == "left+15" and action.y == "bottom-20"
    assert resolve_point(action.x, action.y, 200, 100) == Point(15, 79)
