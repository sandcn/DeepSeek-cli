"""Checkbox 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.checkbox import Checkbox

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestCheckboxBasicRendering:
    def test_checked_shows_checkmark(self):
        cb = Checkbox(checked=True)
        assert "\u2713" in _strip_ansi(str(cb.render()))

    def test_unchecked_shows_empty(self):
        cb = Checkbox(checked=False)
        assert "[ ]" in _strip_ansi(str(cb.render()))

    def test_label_rendered(self):
        cb = Checkbox(checked=True, label="选项1")
        assert "选项1" in _strip_ansi(str(cb.render()))

    def test_no_label(self):
        cb = Checkbox(checked=True)
        output = str(cb.render())
        assert "\u2713" in _strip_ansi(output)


class TestCheckboxStyling:
    def test_checked_green_bold(self):
        cb = Checkbox(checked=True)
        codes = _get_ansi_codes(str(cb.render()))
        assert any("32" in c for c in codes), f"应含绿色(32): {codes}"
        assert any("1" in c for c in codes), f"应含bold(1): {codes}"

    def test_unchecked_dim(self):
        cb = Checkbox(checked=False)
        codes = _get_ansi_codes(str(cb.render()))
        has_dim = any("2" in c and "3" not in c and "0" not in c for c in codes) or any(
            ";2" in c or "2;" in c for c in codes
        )
        assert has_dim, f"未选中应dim: {codes}"


class TestCheckboxDisabled:
    def test_disabled_overall_dim(self):
        cb = Checkbox(checked=True, disabled=True)
        codes = _get_ansi_codes(str(cb.render()))
        has_dim = any("2" in c and "3" not in c and "0" not in c for c in codes) or any(
            ";2" in c or "2;" in c for c in codes
        )
        assert has_dim, f"禁用时应整体dim: {codes}"


class TestCheckboxUpdate:
    def test_update_checked(self):
        cb = Checkbox(checked=False)
        assert cb.update({"checked": True}) is True
        assert cb.update({"checked": True}) is False

    def test_update_label(self):
        cb = Checkbox(checked=True)
        assert cb.update({"label": "新标签"}) is True

    def test_update_no_change(self):
        cb = Checkbox(checked=True, label="t")
        assert cb.update({"checked": True, "label": "t"}) is False


class TestCheckboxRenderVNode:
    def test_render_vnode(self):
        cb = Checkbox(checked=True)
        vnode = cb.render_vnode()
        assert vnode.type == "checkbox"
        assert vnode.key == "checkbox"
        assert vnode.props.get("checked") is True
