"""Alert 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.alert import Alert

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestAlertPresetRendering:
    """各 preset 渲染测试。"""

    def test_preset_success_green(self):
        alert = Alert(preset="success", title="成功", message="操作已执行")
        output = str(alert.render())
        assert _has_ansi(output)
        assert "\u2713" in _strip_ansi(output)  # ✓
        assert "成功" in _strip_ansi(output)
        codes = _get_ansi_codes(output)
        assert any("32" in c for c in codes), f"应含绿色 ANSI 码(32): {codes}"

    def test_preset_error_red(self):
        alert = Alert(preset="error", title="错误")
        output = str(alert.render())
        assert "\u2717" in _strip_ansi(output)  # ✗
        assert "错误" in _strip_ansi(output)
        codes = _get_ansi_codes(output)
        assert any("31" in c for c in codes), f"应含红色 ANSI 码(31): {codes}"

    def test_preset_warn_yellow(self):
        alert = Alert(preset="warn", title="警告")
        output = str(alert.render())
        assert "\u26a0" in _strip_ansi(output)  # ⚠
        codes = _get_ansi_codes(output)
        assert any("33" in c for c in codes), f"应含黄色 ANSI 码(33): {codes}"

    def test_preset_info_blue(self):
        alert = Alert(preset="info", title="信息")
        output = str(alert.render())
        assert "\u2139" in _strip_ansi(output)  # ℹ
        codes = _get_ansi_codes(output)
        assert any("34" in c for c in codes), f"应含蓝色 ANSI 码(34): {codes}"

    def test_preset_invalid_falls_back_to_info(self):
        alert = Alert(preset="unknown", title="回退")
        output = str(alert.render())
        assert _has_ansi(output)
        codes = _get_ansi_codes(output)
        # info = blue = 34
        assert any("34" in c for c in codes), f"无效 preset 应回退蓝色(34): {codes}"


class TestAlertBasicRendering:
    def test_empty_title_uses_preset_name(self):
        alert = Alert(preset="success")
        output = str(alert.render())
        assert "SUCCESS" in _strip_ansi(output)

    def test_message_rendered(self):
        alert = Alert(preset="info", message="详细说明")
        output = str(alert.render())
        assert "详细说明" in _strip_ansi(output)

    def test_closable_shows_x(self):
        alert = Alert(preset="warn", title="注意", closable=True)
        output = str(alert.render())
        assert "\u00d7" in _strip_ansi(output)  # ×

    def test_message_without_title(self):
        alert = Alert(preset="success", message="仅消息")
        output = str(alert.render())
        stripped = _strip_ansi(output)
        assert "SUCCESS" in stripped or "\u2713" in stripped
        assert "仅消息" in stripped


class TestAlertEdgeCases:
    def test_no_title_no_message(self):
        alert = Alert(preset="error")
        output = str(alert.render())
        assert output != ""

    def test_empty_preset_falls_back(self):
        alert = Alert(preset="", title="test")
        output = str(alert.render())
        codes = _get_ansi_codes(output)
        assert any("34" in c for c in codes), f"空 preset 应回退蓝色(34): {codes}"


class TestAlertUpdate:
    def test_update_preset(self):
        alert = Alert(preset="success")
        assert alert.update({"preset": "error"}) is True
        assert alert.update({"preset": "error"}) is False

    def test_update_title(self):
        alert = Alert(preset="info")
        assert alert.update({"title": "新标题"}) is True
        assert alert.update({"title": "新标题"}) is False

    def test_update_message(self):
        alert = Alert(preset="info")
        assert alert.update({"message": "新消息"}) is True

    def test_update_closable(self):
        alert = Alert(preset="info")
        assert alert.update({"closable": True}) is True

    def test_update_unchanged(self):
        alert = Alert(preset="info", title="标题")
        assert alert.update({"title": "标题"}) is False


class TestAlertRenderVNode:
    def test_render_vnode_returns_vnode(self):
        alert = Alert(preset="success", title="成功")
        vnode = alert.render_vnode()
        assert vnode.type == "alert"
        assert vnode.key == "alert"
        assert "preset" in vnode.props
        assert vnode.props["preset"] == "success"
