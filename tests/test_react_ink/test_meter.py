"""Meter 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.meter import Meter

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestMeterColorRanges:
    def test_low_value_red(self):
        meter = Meter(value=10, bar_width=10)
        output = str(meter.render())
        codes = _get_ansi_codes(output)
        assert any("31" in c for c in codes), f"低值应红色(31): {codes}"

    def test_medium_value_yellow(self):
        meter = Meter(value=50, bar_width=10)
        codes = _get_ansi_codes(str(meter.render()))
        assert any("33" in c for c in codes), f"中值应黄色(33): {codes}"

    def test_high_value_green(self):
        meter = Meter(value=90, bar_width=10)
        codes = _get_ansi_codes(str(meter.render()))
        assert any("32" in c for c in codes), f"高值应绿色(32): {codes}"

    def test_boundary_33_percent_red(self):
        meter = Meter(value=33, bar_width=10)
        codes = _get_ansi_codes(str(meter.render()))
        assert any("31" in c for c in codes), f"33% 应红色(31): {codes}"

    def test_boundary_34_percent_yellow(self):
        meter = Meter(value=34, bar_width=10)
        codes = _get_ansi_codes(str(meter.render()))
        assert any("33" in c for c in codes), f"34% 应黄色(33): {codes}"

    def test_boundary_66_percent_yellow(self):
        meter = Meter(value=66, bar_width=10)
        codes = _get_ansi_codes(str(meter.render()))
        assert any("33" in c for c in codes), f"66% 应黄色(33): {codes}"

    def test_boundary_67_percent_green(self):
        meter = Meter(value=67, bar_width=10)
        codes = _get_ansi_codes(str(meter.render()))
        assert any("32" in c for c in codes), f"67% 应绿色(32): {codes}"


class TestMeterBasicRendering:
    def test_percent_displayed(self):
        meter = Meter(value=75, bar_width=10)
        output = str(meter.render())
        stripped = _strip_ansi(output)
        assert "75%" in stripped

    def test_bar_characters_present(self):
        meter = Meter(value=50, bar_width=10)
        output = str(meter.render())
        stripped = _strip_ansi(output)
        assert "\u2588" in stripped  # █
        assert "\u2591" in stripped  # ░

    def test_full_bar_no_empty(self):
        meter = Meter(value=100, bar_width=10)
        output = str(meter.render())
        stripped = _strip_ansi(output)
        assert "\u2591" not in stripped

    def test_empty_bar_no_filled(self):
        meter = Meter(value=0, bar_width=10)
        output = str(meter.render())
        stripped = _strip_ansi(output)
        assert "\u2588" not in stripped or stripped.count("\u2588") == 0

    def test_label_shown(self):
        meter = Meter(value=50, label="CPU", show_label=True, bar_width=10)
        output = str(meter.render())
        assert "CPU" in _strip_ansi(output)

    def test_label_hidden_by_default(self):
        meter = Meter(value=50, label="CPU", bar_width=10)
        output = str(meter.render())
        assert "CPU" not in _strip_ansi(output)

    def test_percent_hidden(self):
        meter = Meter(value=50, show_percent=False, bar_width=10)
        output = str(meter.render())
        assert "50%" not in _strip_ansi(output)


class TestMeterCustomRange:
    def test_custom_min_max(self):
        meter = Meter(value=50, min=0, max=200, bar_width=10)
        output = str(meter.render())
        assert "25%" in _strip_ansi(output)

    def test_value_below_min_clamps(self):
        meter = Meter(value=-10, bar_width=10)
        output = str(meter.render())
        assert "0%" in _strip_ansi(output)

    def test_value_above_max_clamps(self):
        meter = Meter(value=200, bar_width=10)
        output = str(meter.render())
        assert "100%" in _strip_ansi(output)

    def test_zero_range(self):
        meter = Meter(value=50, min=100, max=100, bar_width=10)
        output = str(meter.render())
        # diff=0，应返回 0%
        assert "0%" in _strip_ansi(output)


class TestMeterEdgeCases:
    def test_min_bar_width(self):
        meter = Meter(value=50, bar_width=2)
        assert meter._bar_width >= 4

    def test_negative_bar_width_handled(self):
        meter = Meter(value=50, bar_width=-5)
        assert meter._bar_width >= 4


class TestMeterUpdate:
    def test_update_value(self):
        m = Meter(value=10)
        assert m.update({"value": 90}) is True
        assert m.update({"value": 90}) is False

    def test_update_label(self):
        m = Meter(value=50, label="旧")
        assert m.update({"label": "新", "show_label": True}) is True


class TestMeterRenderVNode:
    def test_render_vnode(self):
        m = Meter(value=75)
        vnode = m.render_vnode()
        assert vnode.type == "meter"
        assert vnode.key == "meter"
        assert "percent" in vnode.props
        assert vnode.props["percent"] == 75.0
        assert vnode.props["color"] == "green"
