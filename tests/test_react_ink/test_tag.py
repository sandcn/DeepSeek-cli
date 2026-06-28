"""Tag 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.tag import Tag

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestTagPresetRendering:
    def test_preset_blue(self):
        tag = Tag(text="blue", preset="blue")
        output = str(tag.render())
        codes = _get_ansi_codes(output)
        assert any("34" in c for c in codes), f"应含蓝色(34): {codes}"

    def test_preset_green(self):
        tag = Tag(text="green", preset="green")
        codes = _get_ansi_codes(str(tag.render()))
        assert any("32" in c for c in codes)

    def test_preset_red(self):
        tag = Tag(text="red", preset="red")
        codes = _get_ansi_codes(str(tag.render()))
        assert any("31" in c for c in codes)

    def test_preset_yellow(self):
        tag = Tag(text="yellow", preset="yellow")
        codes = _get_ansi_codes(str(tag.render()))
        assert any("33" in c for c in codes)

    def test_preset_purple(self):
        tag = Tag(text="purple", preset="purple")
        codes = _get_ansi_codes(str(tag.render()))
        # magenta = 35
        assert any("35" in c for c in codes), f"应含紫色/magenta(35): {codes}"

    def test_preset_gray(self):
        tag = Tag(text="gray", preset="gray")
        codes = _get_ansi_codes(str(tag.render()))
        # white = 37
        assert any("37" in c for c in codes), f"应含白色(37): {codes}"

    def test_invalid_preset_falls_back_to_gray(self):
        tag = Tag(text="test", preset="invalid")
        codes = _get_ansi_codes(str(tag.render()))
        assert any("37" in c for c in codes), f"无效应回退白色(37): {codes}"


class TestTagBasicRendering:
    def test_text_rendered(self):
        tag = Tag(text="Python")
        output = str(tag.render())
        assert "Python" in _strip_ansi(output)

    def test_dot_prefix_rendered(self):
        tag = Tag(text="标签")
        output = str(tag.render())
        assert "\u2022" in _strip_ansi(output)  # •

    def test_bold_attribute(self):
        tag = Tag(text="粗体", preset="blue", bold=True)
        codes = _get_ansi_codes(str(tag.render()))
        assert any("1" in c for c in codes), f"应含bold: {codes}"


class TestTagEdgeCases:
    def test_empty_text_returns_empty(self):
        tag = Tag(text="", preset="blue")
        assert tag.render() == ""

    def test_empty_text_all_presets(self):
        for preset in ("blue", "green", "red", "yellow", "purple", "gray"):
            tag = Tag(text="", preset=preset)
            assert tag.render() == "", f"preset={preset}"

    def test_empty_string_preset_falls_back(self):
        tag = Tag(text="test", preset="")
        assert tag._preset == "gray"


class TestTagUpdate:
    def test_update_text(self):
        tag = Tag(text="old")
        assert tag.update({"text": "new"}) is True
        assert tag.update({"text": "new"}) is False

    def test_update_preset(self):
        tag = Tag(text="t", preset="blue")
        assert tag.update({"preset": "red"}) is True

    def test_update_bold(self):
        tag = Tag(text="t", preset="blue")
        assert tag.update({"bold": True}) is True

    def test_update_no_change(self):
        tag = Tag(text="t", preset="blue", bold=False)
        assert tag.update({"text": "t", "preset": "blue", "bold": False}) is False


class TestTagRenderVNode:
    def test_render_vnode(self):
        tag = Tag(text="Python", preset="blue")
        vnode = tag.render_vnode()
        assert vnode.type == "tag"
        assert vnode.key == "tag"
        assert vnode.props.get("preset") == "blue"
