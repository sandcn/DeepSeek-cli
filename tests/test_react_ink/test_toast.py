"""Toast 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.toast import Toast

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestToastPresetRendering:
    def test_success_green(self):
        t = Toast(message="成功", preset="success")
        codes = _get_ansi_codes(str(t.render()))
        assert "\u2713" in _strip_ansi(str(t.render()))
        assert any("32" in c for c in codes)

    def test_error_red(self):
        t = Toast(message="失败", preset="error")
        codes = _get_ansi_codes(str(t.render()))
        assert "\u2717" in _strip_ansi(str(t.render()))
        assert any("31" in c for c in codes)

    def test_warn_yellow(self):
        t = Toast(message="警告", preset="warn")
        codes = _get_ansi_codes(str(t.render()))
        assert any("33" in c for c in codes)

    def test_info_blue(self):
        t = Toast(message="信息", preset="info")
        codes = _get_ansi_codes(str(t.render()))
        assert any("34" in c for c in codes)

    def test_invalid_preset_falls_back(self):
        t = Toast(message="test", preset="invalid")
        codes = _get_ansi_codes(str(t.render()))
        assert any("34" in c for c in codes)

    def test_empty_preset_falls_back(self):
        t = Toast(message="test", preset="")
        assert t._preset == "info"


class TestToastBasicRendering:
    def test_message_rendered(self):
        t = Toast(message="保存成功")
        assert "保存成功" in _strip_ansi(str(t.render()))

    def test_bold_attribute(self):
        t = Toast(message="重要", preset="error", bold=True)
        codes = _get_ansi_codes(str(t.render()))
        assert any("1" in c for c in codes)


class TestToastEdgeCases:
    def test_empty_message_returns_empty(self):
        t = Toast(message="")
        assert t.render() == ""

    def test_empty_message_all_presets(self):
        for p in ("success", "error", "warn", "info"):
            t = Toast(message="", preset=p)
            assert t.render() == "", f"preset={p}"


class TestToastUpdate:
    def test_update_message(self):
        t = Toast(message="旧")
        assert t.update({"message": "新"}) is True
        assert t.update({"message": "新"}) is False

    def test_update_preset(self):
        t = Toast(message="t", preset="info")
        assert t.update({"preset": "success"}) is True

    def test_update_no_change(self):
        t = Toast(message="t", preset="info")
        assert t.update({"message": "t"}) is False


class TestToastRenderVNode:
    def test_render_vnode(self):
        t = Toast(message="通知", preset="success")
        vnode = t.render_vnode()
        assert vnode.type == "toast"
        assert vnode.key == "toast"
        assert vnode.props.get("preset") == "success"
