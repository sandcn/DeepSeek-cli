"""StatusDot 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.status_dot import StatusDot

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestStatusDotStatusColors:
    def test_online_green(self):
        dot = StatusDot(status="online")
        codes = _get_ansi_codes(str(dot.render()))
        assert any("32" in c for c in codes)

    def test_offline_white(self):
        dot = StatusDot(status="offline")
        codes = _get_ansi_codes(str(dot.render()))
        assert any("37" in c for c in codes)

    def test_away_yellow(self):
        dot = StatusDot(status="away")
        codes = _get_ansi_codes(str(dot.render()))
        assert any("33" in c for c in codes)

    def test_busy_red(self):
        dot = StatusDot(status="busy")
        codes = _get_ansi_codes(str(dot.render()))
        assert any("31" in c for c in codes)

    def test_error_red(self):
        dot = StatusDot(status="error")
        codes = _get_ansi_codes(str(dot.render()))
        assert any("31" in c for c in codes)

    def test_invalid_status_falls_back_offline(self):
        dot = StatusDot(status="unknown")
        assert dot._status == "offline"


class TestStatusDotSizes:
    def test_small_dot(self):
        dot = StatusDot(status="online", size="small")
        output = str(dot.render())
        assert "\u2022" in _strip_ansi(output)  # •

    def test_medium_dot(self):
        dot = StatusDot(status="online", size="medium")
        output = str(dot.render())
        assert "\u25CF" in _strip_ansi(output)  # ●

    def test_large_dot(self):
        dot = StatusDot(status="online", size="large")
        output = str(dot.render())
        assert "\u25C6" in _strip_ansi(output)  # ◆

    def test_invalid_size_falls_back_medium(self):
        dot = StatusDot(status="online", size="huge")
        assert dot._size == "medium"


class TestStatusDotBasicRendering:
    def test_label_rendered(self):
        dot = StatusDot(status="online", label="服务正常")
        assert "服务正常" in _strip_ansi(str(dot.render()))

    def test_no_label_only_dot(self):
        dot = StatusDot(status="online")
        output = str(dot.render())
        stripped = _strip_ansi(output)
        assert "\u25CF" in stripped
        assert len(stripped.strip()) > 0

    def test_empty_label(self):
        dot = StatusDot(status="online", label="")
        output = str(dot.render())
        assert "\u25CF" in _strip_ansi(output)


class TestStatusDotUpdate:
    def test_update_status(self):
        dot = StatusDot(status="online")
        assert dot.update({"status": "busy"}) is True
        assert dot.update({"status": "busy"}) is False

    def test_update_label(self):
        dot = StatusDot(status="online")
        assert dot.update({"label": "新标签"}) is True

    def test_update_size(self):
        dot = StatusDot(status="online", size="medium")
        assert dot.update({"size": "small"}) is True

    def test_update_no_change(self):
        dot = StatusDot(status="online", label="test")
        assert dot.update({"status": "online", "label": "test"}) is False


class TestStatusDotRenderVNode:
    def test_render_vnode(self):
        dot = StatusDot(status="online", label="正常")
        vnode = dot.render_vnode()
        assert vnode.type == "status_dot"
        assert vnode.key == "status_dot"
        assert vnode.props.get("status") == "online"
