"""Switch 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.switch import Switch

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestSwitchBasicRendering:
    def test_checked_shows_on(self):
        sw = Switch(checked=True)
        assert "[ON]" in _strip_ansi(str(sw.render()))

    def test_unchecked_shows_off(self):
        sw = Switch(checked=False)
        assert "[OFF]" in _strip_ansi(str(sw.render()))

    def test_label_rendered(self):
        sw = Switch(checked=True, label="启用")
        assert "启用" in _strip_ansi(str(sw.render()))

    def test_no_label(self):
        sw = Switch(checked=True)
        output = str(sw.render())
        assert "[ON]" in _strip_ansi(output)
        assert len(_strip_ansi(output).strip()) > 0


class TestSwitchStyling:
    def test_checked_green_bold(self):
        sw = Switch(checked=True)
        codes = _get_ansi_codes(str(sw.render()))
        assert any("32" in c for c in codes), f"应含绿色(32): {codes}"
        assert any("1" in c for c in codes), f"应含bold(1): {codes}"

    def test_unchecked_dim(self):
        sw = Switch(checked=False)
        codes = _get_ansi_codes(str(sw.render()))
        assert any("2" in c and "3" not in c and "0" not in c for c in codes) or any(
            ";2" in c or "2;" in c for c in codes
        ), f"应含dim(2): {codes}"


class TestSwitchDisabled:
    def test_disabled_overall_dim(self):
        sw = Switch(checked=True, disabled=True)
        codes = _get_ansi_codes(str(sw.render()))
        # 禁用时整体 dim
        has_dim = any("2" in c and "3" not in c and "0" not in c for c in codes) or any(
            ";2" in c or "2;" in c for c in codes
        )
        assert has_dim, f"禁用时应整体dim: {codes}"


class TestSwitchUpdate:
    def test_update_checked(self):
        sw = Switch(checked=False)
        assert sw.update({"checked": True}) is True
        assert sw.update({"checked": True}) is False

    def test_update_label(self):
        sw = Switch(checked=True)
        assert sw.update({"label": "新标签"}) is True

    def test_update_disabled(self):
        sw = Switch(checked=True)
        assert sw.update({"disabled": True}) is True

    def test_update_no_change(self):
        sw = Switch(checked=True, label="t")
        assert sw.update({"checked": True, "label": "t"}) is False


class TestSwitchRenderVNode:
    def test_render_vnode(self):
        sw = Switch(checked=True, disabled=False)
        vnode = sw.render_vnode()
        assert vnode.type == "switch"
        assert vnode.key == "switch"
        assert vnode.props.get("checked") is True
        assert vnode.props.get("disabled") is False
