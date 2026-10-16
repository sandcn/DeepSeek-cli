"""widgets._widget_common 公共辅助（含 borders/padding/outer_height）测试。"""

from __future__ import annotations

from src.tui.ink.widgets._widget_common import (
    _border_count,
    _outer_height,
    _vertical_padding,
    _call,
)


def test_border_count_variants():
    assert _border_count({"border": 1}) == 1
    assert _border_count({"border": 2}) == 1
    assert _border_count({"border": 0}) == 0
    assert _border_count({"border": False}) == 0
    assert _border_count({"border": None}) == 0
    assert _border_count({"border": "none"}) == 0
    assert _border_count({"border": "bad"}) == 0
    assert _border_count({}, default=0) == 0
    assert _border_count({}, default=1) == 1


def test_vertical_padding_variants():
    assert _vertical_padding({"padding": 2}) == (2, 2)
    assert _vertical_padding({"paddingY": 1}) == (1, 1)
    assert _vertical_padding({"padding": 1, "paddingTop": 3}) == (3, 1)
    assert _vertical_padding({}) == (0, 0)
    assert _vertical_padding({"padding": "bad"}) == (0, 0)


def test_outer_height_accounts_border_and_padding():
    assert _outer_height({}, 3, default_border=0) == 3
    assert _outer_height({"border": 1}, 3, default_border=0) == 5
    assert _outer_height({"border": 1, "paddingTop": 1, "paddingBottom": 2}, 3) == 8
    assert _outer_height({"border": 1}, -5, default_border=0) == 2


def test_call_swallows_and_logs():
    assert _call(None) is None
    _call(lambda x: (_ for _ in ()).throw(RuntimeError("x")), 1)  # 不抛异常
